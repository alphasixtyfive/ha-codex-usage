"""Login renewal, retries, and failures using Home Assistant's runtime."""

from time import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock

try:
    from homeassistant.exceptions import ConfigEntryAuthFailed
    from homeassistant.helpers.update_coordinator import UpdateFailed
except ImportError:
    raise unittest.SkipTest("These lifecycle tests require Home Assistant")

from custom_components.codex_usage.client import AuthError, ConnectionError
from custom_components.codex_usage.coordinator import CodexCoordinator


def response(account="test-account"):
    return {
        "account_id": account,
        "rate_limit": {"primary_window": {"used_percent": 25}},
    }


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.entry = SimpleNamespace(
            data={
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_at": time() + 3600,
                "account_id": "test-account",
            }
        )
        self.coordinator = CodexCoordinator.__new__(CodexCoordinator)
        self.coordinator.entry = self.entry
        self.coordinator.hass = MagicMock()
        self.coordinator.hass.config_entries.async_update_entry.side_effect = (
            lambda entry, data: setattr(entry, "data", data)
        )
        self.coordinator.client = MagicMock()
        self.coordinator.client.fetch_usage = AsyncMock(return_value=response())
        self.coordinator.client.refresh = AsyncMock(
            return_value={
                **self.entry.data,
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_at": time() + 7200,
            }
        )

    async def test_valid_login_needs_one_usage_request(self):
        result = await self.coordinator._async_update_data()
        self.assertEqual(result.remaining_percent, 75)
        self.coordinator.client.fetch_usage.assert_awaited_once()
        self.coordinator.client.refresh.assert_not_awaited()

    async def test_expired_login_is_renewed_and_saved_before_fetch(self):
        self.entry.data["expires_at"] = time() - 1
        await self.coordinator._async_update_data()
        self.coordinator.client.refresh.assert_awaited_once()
        self.assertEqual(self.entry.data["refresh_token"], "new-refresh")
        self.coordinator.client.fetch_usage.assert_awaited_once_with(self.entry.data)

    async def test_unauthorized_fetch_renews_and_retries_once(self):
        self.coordinator.client.fetch_usage.side_effect = [
            AuthError("expired"),
            response(),
        ]
        await self.coordinator._async_update_data()
        self.assertEqual(self.coordinator.client.fetch_usage.await_count, 2)
        self.coordinator.client.refresh.assert_awaited_once()

    async def test_failed_retry_requires_reauthentication(self):
        self.coordinator.client.fetch_usage.side_effect = AuthError("revoked")
        with self.assertRaises(ConfigEntryAuthFailed):
            await self.coordinator._async_update_data()
        self.assertEqual(self.coordinator.client.fetch_usage.await_count, 2)
        self.coordinator.client.refresh.assert_awaited_once()

    async def test_already_renewed_login_is_not_renewed_again(self):
        self.entry.data["expires_at"] = time() - 1
        self.coordinator.client.fetch_usage.side_effect = AuthError("revoked")
        with self.assertRaises(ConfigEntryAuthFailed):
            await self.coordinator._async_update_data()
        self.coordinator.client.refresh.assert_awaited_once()
        self.coordinator.client.fetch_usage.assert_awaited_once()

    async def test_wrong_account_is_rejected(self):
        self.coordinator.client.fetch_usage.return_value = response("other-account")
        with self.assertRaises(ConfigEntryAuthFailed):
            await self.coordinator._async_update_data()

    async def test_missing_account_is_a_data_failure(self):
        self.coordinator.client.fetch_usage.return_value = {}
        with self.assertRaises(UpdateFailed):
            await self.coordinator._async_update_data()
        self.coordinator.client.refresh.assert_not_awaited()

    async def test_service_and_data_failures_are_update_failures(self):
        for failure in (ConnectionError("offline"), None):
            with self.subTest(failure=failure):
                self.coordinator.client.fetch_usage.side_effect = failure
                self.coordinator.client.fetch_usage.return_value = {
                    "account_id": "test-account"
                }
                with self.assertRaises(UpdateFailed):
                    await self.coordinator._async_update_data()
