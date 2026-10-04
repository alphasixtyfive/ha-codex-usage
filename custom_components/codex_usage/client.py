"""Read Codex cloud usage and maintain an independent ChatGPT login."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import math
from typing import Any, Mapping, TypedDict

import aiohttp

AUTH_URL = "https://auth.openai.com/api/accounts"
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
HEADERS = {"User-Agent": "codex_home_assistant"}


class Credentials(TypedDict):
    access_token: str
    refresh_token: str
    expires_at: float
    account_id: str | None


class DeviceLogin(TypedDict):
    device_auth_id: str
    user_code: str


class AuthError(Exception):
    """The user needs to sign in again."""


class ConnectionError(Exception):
    """The cloud service could not be reached."""


class DataError(Exception):
    """The service returned incomplete or unexpected data."""


class AuthorizationPending(Exception):
    """The user has not finished the device login."""


@dataclass(frozen=True)
class Usage:
    remaining_percent: float
    resets_at: datetime | None
    window_minutes: float | None
    plan: str | None


def _is_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def parse_usage(data: Mapping[str, Any]) -> Usage:
    """Report the smallest remaining allowance and the same window's reset."""
    if not isinstance(data, dict):
        raise DataError("Codex returned an unexpected usage response")
    rate_limit = data.get("rate_limit")
    if not isinstance(rate_limit, dict):
        raise DataError("No Codex usage limit was reported")
    plan = data.get("plan_type")
    if plan is not None and not isinstance(plan, str):
        raise DataError("Invalid Codex account plan")

    windows: list[Usage] = []
    for key in ("primary_window", "secondary_window"):
        window = rate_limit.get(key)
        if window is None:
            continue
        if not isinstance(window, dict) or not _is_number(window.get("used_percent")):
            raise DataError("Invalid Codex usage percentage")
        seconds = window.get("limit_window_seconds")
        reset = window.get("reset_at")
        if seconds is not None and (not _is_number(seconds) or seconds <= 0):
            raise DataError("Invalid Codex usage window")
        if reset is not None and (not _is_number(reset) or reset <= 0):
            raise DataError("Invalid Codex reset time")
        try:
            resets_at = (
                datetime.fromtimestamp(reset, UTC) if reset is not None else None
            )
        except (ValueError, OverflowError, OSError) as error:
            raise DataError("Invalid Codex reset time") from error
        windows.append(
            Usage(
                remaining_percent=max(0, min(100, 100 - window["used_percent"])),
                resets_at=resets_at,
                window_minutes=seconds / 60 if seconds is not None else None,
                plan=plan,
            )
        )
    if not windows:
        raise DataError("No Codex usage window is available")
    return min(
        windows,
        key=lambda window: (
            window.remaining_percent,
            window.window_minutes or math.inf,
        ),
    )


def parse_credentials(
    response: Mapping[str, Any],
    previous: Mapping[str, Any] | None = None,
) -> Credentials:
    """Keep a rotated refresh token and preserve one omitted by the service."""
    previous = previous or {}
    access = response.get("access_token")
    refresh = response.get("refresh_token") or previous.get("refresh_token")
    if (
        not isinstance(access, str)
        or not access
        or not isinstance(refresh, str)
        or not refresh
    ):
        raise DataError("The login response did not include renewable credentials")
    try:
        segment = access.split(".")[1]
        claims = json.loads(
            base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
        )
    except (IndexError, ValueError, TypeError) as error:
        raise DataError(
            "The login response did not include a valid access token"
        ) from error
    if not isinstance(claims, dict):
        raise DataError("The login response did not include valid access token claims")
    expires = claims.get("exp")
    if not _is_number(expires) or expires <= 0:
        raise DataError("The access token did not include an expiry time")
    identity = claims.get("https://api.openai.com/auth") or {}
    if not isinstance(identity, dict):
        raise DataError("The access token did not include a valid account")
    account_id = identity.get("chatgpt_account_id")
    account_id = account_id or previous.get("account_id")
    if account_id is not None and (not isinstance(account_id, str) or not account_id):
        raise DataError("The access token did not include a valid account identifier")
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_at": expires,
        "account_id": account_id,
    }


class CodexClient:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session

    async def _request(
        self,
        method: str,
        url: str,
        *,
        pending: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            async with self.session.request(
                method,
                url,
                timeout=aiohttp.ClientTimeout(total=20),
                allow_redirects=False,
                **kwargs,
            ) as response:
                if pending and response.status in (403, 404):
                    raise AuthorizationPending
                if response.status in (400, 401, 403):
                    raise AuthError("ChatGPT sign-in is required")
                if response.status != 200:
                    raise ConnectionError(f"Codex returned HTTP {response.status}")
                result = await response.json()
                if not isinstance(result, dict):
                    raise DataError("Codex returned an unexpected response")
                return result
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            raise ConnectionError("Cannot reach Codex") from error
        except (ValueError, TypeError) as error:
            raise DataError("Codex returned an invalid response") from error

    async def start_login(self) -> DeviceLogin:
        result = await self._request(
            "POST",
            f"{AUTH_URL}/deviceauth/usercode",
            headers=HEADERS,
            json={"client_id": CLIENT_ID},
        )
        if any(
            not isinstance(result.get(k), str) or not result[k]
            for k in ("device_auth_id", "user_code")
        ):
            raise DataError("Codex did not provide a device login code")
        return {
            "device_auth_id": result["device_auth_id"],
            "user_code": result["user_code"],
        }

    async def finish_login(self, registration: DeviceLogin) -> Credentials:
        authorization = await self._request(
            "POST",
            f"{AUTH_URL}/deviceauth/token",
            headers=HEADERS,
            pending=True,
            json={k: registration[k] for k in ("device_auth_id", "user_code")},
        )
        if any(
            not isinstance(authorization.get(k), str) or not authorization[k]
            for k in ("authorization_code", "code_verifier")
        ):
            raise DataError("Codex did not complete device authorization")
        response = await self._request(
            "POST",
            f"{AUTH_URL}/oauth/token",
            headers=HEADERS,
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "code": authorization["authorization_code"],
                "code_verifier": authorization["code_verifier"],
                "redirect_uri": "https://auth.openai.com/deviceauth/callback",
            },
        )
        return parse_credentials(response)

    async def refresh(self, credentials: Mapping[str, Any]) -> Credentials:
        response = await self._request(
            "POST",
            f"{AUTH_URL}/oauth/token",
            headers=HEADERS,
            data={
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": credentials["refresh_token"],
            },
        )
        result = parse_credentials(response, credentials)
        if result["account_id"] != credentials["account_id"]:
            raise AuthError("The renewed token belongs to a different account")
        return result

    async def fetch_usage(self, credentials: Mapping[str, Any]) -> dict[str, Any]:
        headers = {**HEADERS, "Authorization": "Bearer " + credentials["access_token"]}
        if credentials.get("account_id"):
            headers["ChatGPT-Account-Id"] = credentials["account_id"]
        return await self._request("GET", USAGE_URL, headers=headers)
