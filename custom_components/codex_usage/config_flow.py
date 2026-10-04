"""Authorize a separate ChatGPT session for Home Assistant."""

from time import monotonic
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .client import (
    AuthError,
    AuthorizationPending,
    CodexClient,
    ConnectionError,
    Credentials,
    DataError,
    DeviceLogin,
    parse_usage,
)
from .const import DEVICE_CODE_LIFETIME, DOMAIN


class CodexConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._login: DeviceLogin | None = None
        self._login_expires_at = 0.0
        self._credentials: Credentials | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return await self.async_step_login()
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        if user_input is not None:
            return await self.async_step_login()
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=vol.Schema({})
        )

    async def async_step_login(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        client = CodexClient(async_get_clientsession(self.hass))
        errors: dict[str, str] = {}

        try:
            if self._credentials is None and (
                self._login is None or monotonic() >= self._login_expires_at
            ):
                self._login = await client.start_login()
                self._login_expires_at = monotonic() + DEVICE_CODE_LIFETIME

            elif user_input is not None:
                if self._credentials is None:
                    assert self._login is not None
                    self._credentials = await client.finish_login(self._login)

                usage = await client.fetch_usage(self._credentials)
                parse_usage(usage)
                account_id = usage.get("account_id")
                if not isinstance(account_id, str) or not account_id:
                    raise DataError("The usage response did not identify an account")

                self._credentials["account_id"] = account_id
                await self.async_set_unique_id(account_id)

                if self.source == "reauth":
                    self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(
                        self._get_reauth_entry(),
                        data=self._credentials,
                    )

                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Codex", data=self._credentials)

        except AuthorizationPending:
            errors["base"] = "authorization_pending"
        except AuthError:
            return self.async_abort(reason="authentication_failed")
        except ConnectionError:
            errors["base"] = "cannot_connect"
        except DataError:
            return self.async_abort(reason="invalid_response")

        if self._login is None:
            return self.async_show_form(
                step_id="user", data_schema=vol.Schema({}), errors=errors
            )

        return self.async_show_form(
            step_id="login",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={"code": self._login["user_code"]},
        )
