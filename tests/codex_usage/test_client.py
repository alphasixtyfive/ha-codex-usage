"""Cloud usage parsing and authentication contract checks without real credentials."""

import base64
from datetime import UTC, datetime
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

SOURCE = Path(__file__).resolve().parents[2] / "custom_components/codex_usage/client.py"
spec = importlib.util.spec_from_file_location("codex_usage_client", SOURCE)
client = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = client
spec.loader.exec_module(client)


def window(used, seconds=604800, reset=1791580429):
    return {"used_percent": used, "limit_window_seconds": seconds, "reset_at": reset}


def usage(primary=None, secondary=None):
    return {
        "plan_type": "pro",
        "rate_limit": {"primary_window": primary, "secondary_window": secondary},
    }


def token(account="test-account", exp=1900000000):
    claims = {
        "exp": exp,
        "https://api.openai.com/auth": {"chatgpt_account_id": account},
    }
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"test.{payload}.signature"


def session(response, status=200):
    result = MagicMock()
    result.status = status
    result.json = AsyncMock(return_value=response)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=result)
    context.__aexit__ = AsyncMock(return_value=False)
    result_session = MagicMock()
    result_session.request.return_value = context
    return result_session


class UsageTests(unittest.TestCase):
    def test_weekly_remaining_and_reset(self):
        result = client.parse_usage(usage(window(27)))
        self.assertEqual(result.remaining_percent, 73)
        self.assertEqual(result.window_minutes, 10080)
        self.assertEqual(
            result.resets_at, datetime(2026, 10, 9, 21, 13, 49, tzinfo=UTC)
        )

    def test_lowest_remaining_and_matching_reset(self):
        result = client.parse_usage(usage(window(27), window(95, 18000, 1791000000)))
        self.assertEqual(result.remaining_percent, 5)
        self.assertEqual(result.window_minutes, 300)
        self.assertEqual(result.resets_at, datetime(2026, 10, 3, 4, tzinfo=UTC))

    def test_secondary_only(self):
        self.assertEqual(
            client.parse_usage(usage(secondary=window(0))).remaining_percent, 100
        )

    def test_optional_reset_is_unknown(self):
        result = client.parse_usage(usage(window(27, reset=None)))
        self.assertEqual(result.remaining_percent, 73)
        self.assertIsNone(result.resets_at)

    def test_other_limits_are_excluded(self):
        result = usage(window(27))
        result.update(
            code_review_rate_limit=usage(window(100))["rate_limit"],
            additional_rate_limits=[usage(window(100))["rate_limit"]],
        )
        self.assertEqual(client.parse_usage(result).remaining_percent, 73)

    def test_no_window_is_not_a_full_allowance(self):
        for data in (
            {},
            usage(),
            [],
            usage(window(None)),
            usage(window(True)),
            usage(window(float("nan"))),
            usage(window(20, 0)),
            usage(window(20, reset=-1)),
            usage(window(20, reset=1e30)),
        ):
            with self.subTest(data=data), self.assertRaises(client.DataError):
                client.parse_usage(data)

    def test_boundary_clamping_and_decimals(self):
        for used, expected in ((-2, 100), (120, 0), (27.5, 72.5)):
            self.assertEqual(
                client.parse_usage(usage(window(used))).remaining_percent, expected
            )

    def test_large_numbers_and_invalid_plan_are_rejected(self):
        for data in (usage(window(10**1000)), {**usage(window(27)), "plan_type": []}):
            with self.assertRaises(client.DataError):
                client.parse_usage(data)

    def test_equal_allowances_choose_the_shorter_window(self):
        result = client.parse_usage(usage(window(27), window(27, 18000, 1791000000)))
        self.assertEqual(result.window_minutes, 300)

    def test_token_rotation_preserves_only_the_current_credentials(self):
        result = client.parse_credentials(
            {"access_token": token(), "refresh_token": "replacement"},
            {"refresh_token": "old", "account_id": "test-account"},
        )
        self.assertEqual(result["refresh_token"], "replacement")
        self.assertEqual(result["account_id"], "test-account")
        self.assertEqual(result["expires_at"], 1900000000)
        self.assertEqual(
            set(result), {"access_token", "refresh_token", "expires_at", "account_id"}
        )

    def test_omitted_refresh_token_is_preserved(self):
        result = client.parse_credentials(
            {"access_token": token()}, {"refresh_token": "saved"}
        )
        self.assertEqual(result["refresh_token"], "saved")

    def test_invalid_tokens_fail_without_echoing_credentials(self):
        for data in (
            {},
            {"access_token": token()},
            {"access_token": "secret-invalid-token", "refresh_token": "secret-refresh"},
            {"access_token": token(exp=None), "refresh_token": "secret-refresh"},
            {"access_token": token(exp=-1), "refresh_token": "secret-refresh"},
        ):
            with (
                self.subTest(data_keys=list(data)),
                self.assertRaises(client.DataError) as error,
            ):
                client.parse_credentials(data)
            self.assertNotIn("secret", str(error.exception))


class RequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_device_login_keeps_only_the_registration_fields(self):
        response = {
            "device_auth_id": "device",
            "user_code": "code",
            "unused": "ignored",
        }
        result = await client.CodexClient(session(response)).start_login()
        self.assertEqual(result, {"device_auth_id": "device", "user_code": "code"})

    async def test_completed_login_exchanges_the_authorization_code(self):
        api = client.CodexClient(MagicMock())
        api._request = AsyncMock(
            side_effect=[
                {"authorization_code": "authorization", "code_verifier": "verifier"},
                {"access_token": token(), "refresh_token": "refresh"},
            ]
        )
        result = await api.finish_login(
            {"device_auth_id": "device", "user_code": "code"}
        )
        self.assertEqual(result["account_id"], "test-account")
        self.assertEqual(result["refresh_token"], "refresh")
        request = api._request.call_args_list[1]
        self.assertEqual(request.kwargs["data"]["grant_type"], "authorization_code")
        self.assertEqual(request.kwargs["data"]["code_verifier"], "verifier")

    async def test_unexpected_json_is_a_data_error(self):
        with self.assertRaises(client.DataError):
            await client.CodexClient(session([])).fetch_usage({"access_token": "test"})

    async def test_fetch_uses_bearer_and_account_header_without_redirects(self):
        s = session(usage(window(27)))
        await client.CodexClient(s).fetch_usage(
            {"access_token": "test", "account_id": "test-account"}
        )
        args, kwargs = s.request.call_args
        self.assertEqual(args, ("GET", client.USAGE_URL))
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test")
        self.assertEqual(kwargs["headers"]["ChatGPT-Account-Id"], "test-account")

    async def test_refresh_is_oauth_only_and_preserves_omitted_refresh_token(self):
        s = session({"access_token": token()})
        result = await client.CodexClient(s).refresh(
            {"refresh_token": "saved", "account_id": "test-account"}
        )
        self.assertEqual(result["refresh_token"], "saved")
        args, kwargs = s.request.call_args
        self.assertEqual(args, ("POST", client.AUTH_URL + "/oauth/token"))
        self.assertEqual(kwargs["data"]["grant_type"], "refresh_token")
        self.assertEqual(kwargs["data"]["refresh_token"], "saved")
        self.assertNotIn("Authorization", kwargs["headers"])

    async def test_wrong_account_refresh_is_rejected(self):
        s = session({"access_token": token("other-account")})
        with self.assertRaises(client.AuthError):
            await client.CodexClient(s).refresh(
                {"refresh_token": "saved", "account_id": "test-account"}
            )

    async def test_http_errors_do_not_echo_response_data(self):
        for status, exception in (
            (401, client.AuthError),
            (429, client.ConnectionError),
            (503, client.ConnectionError),
        ):
            with self.subTest(status=status), self.assertRaises(exception):
                await client.CodexClient(
                    session({"secret": "ignored"}, status)
                ).fetch_usage({"access_token": "test"})

    async def test_device_authorization_pending(self):
        with self.assertRaises(client.AuthorizationPending):
            await client.CodexClient(session({}, 403)).finish_login(
                {"device_auth_id": "test", "user_code": "test"}
            )


if __name__ == "__main__":
    unittest.main()
