"""Device sign-in and retry behavior using Home Assistant's runtime."""

import importlib.util
from time import monotonic
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

if importlib.util.find_spec("homeassistant") is None:
    raise unittest.SkipTest("These lifecycle tests require Home Assistant")

from custom_components.codex_usage.client import AuthorizationPending, ConnectionError
from custom_components.codex_usage.config_flow import CodexConfigFlow


class FlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.flow = CodexConfigFlow()
        self.flow.hass = MagicMock()
        self.flow.context = {"source": "user"}
        self.flow._async_current_entries = MagicMock(return_value=[])
        self.flow.async_show_form = MagicMock(
            side_effect=lambda **kwargs: {"type": "form", **kwargs}
        )
        self.flow.async_abort = MagicMock(
            side_effect=lambda **kwargs: {"type": "abort", **kwargs}
        )
        self.flow.async_create_entry = MagicMock(
            side_effect=lambda **kwargs: {"type": "create_entry", **kwargs}
        )
        self.flow.async_set_unique_id = AsyncMock()
        self.flow._abort_if_unique_id_configured = MagicMock()
        self.client = MagicMock()
        self.client.start_login = AsyncMock(
            return_value={"device_auth_id": "device", "user_code": "code"}
        )
        self.client.finish_login = AsyncMock(
            return_value={
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_at": 1900000000,
                "account_id": "test-account",
            }
        )
        self.client.fetch_usage = AsyncMock(
            return_value={
                "account_id": "test-account",
                "rate_limit": {"primary_window": {"used_percent": 25}},
            }
        )
        self.client_patch = patch(
            "custom_components.codex_usage.config_flow.CodexClient",
            return_value=self.client,
        )
        self.session_patch = patch(
            "custom_components.codex_usage.config_flow.async_get_clientsession"
        )
        self.client_patch.start()
        self.session_patch.start()
        self.addCleanup(self.client_patch.stop)
        self.addCleanup(self.session_patch.stop)

    async def test_start_displays_code_without_polling_authorization(self):
        result = await self.flow.async_step_login()
        self.assertEqual(result["description_placeholders"], {"code": "code"})
        self.client.finish_login.assert_not_awaited()

    async def test_pending_authorization_can_be_retried(self):
        await self.flow.async_step_login()
        self.client.finish_login.side_effect = AuthorizationPending()
        result = await self.flow.async_step_login({})
        self.assertEqual(result["errors"], {"base": "authorization_pending"})
        self.assertIsNone(self.flow._credentials)

    async def test_expired_uncompleted_login_gets_a_new_code(self):
        await self.flow.async_step_login()
        self.flow._login_expires_at = monotonic() - 1
        self.client.start_login.return_value = {
            "device_auth_id": "new-device",
            "user_code": "new-code",
        }
        result = await self.flow.async_step_login({})
        self.assertEqual(result["description_placeholders"], {"code": "new-code"})
        self.client.finish_login.assert_not_awaited()

    async def test_usage_retry_keeps_credentials_after_device_code_expiry(self):
        await self.flow.async_step_login()
        self.client.fetch_usage.side_effect = ConnectionError("offline")
        result = await self.flow.async_step_login({})
        self.assertEqual(result["errors"], {"base": "cannot_connect"})
        self.flow._login_expires_at = 0
        self.client.fetch_usage.side_effect = None
        result = await self.flow.async_step_login({})
        self.assertEqual(result["type"], "create_entry")
        self.client.finish_login.assert_awaited_once()
        self.client.start_login.assert_awaited_once()

    async def test_existing_integration_does_not_start_another_login(self):
        self.flow._async_current_entries.return_value = [MagicMock()]
        result = await self.flow.async_step_user({})
        self.assertEqual(result["reason"], "single_instance_allowed")
        self.client.start_login.assert_not_awaited()
