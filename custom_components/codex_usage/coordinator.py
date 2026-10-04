"""One cloud poll updates both sensors and renews the integration's login."""

from datetime import timedelta
import logging
from time import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import (
    AuthError,
    CodexClient,
    ConnectionError,
    DataError,
    Usage,
    parse_usage,
)
from .const import DOMAIN, POLL_INTERVAL


class CodexCoordinator(DataUpdateCoordinator[Usage]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            logging.getLogger(__name__),
            name=DOMAIN,
            config_entry=entry,
            always_update=False,
            update_interval=timedelta(seconds=POLL_INTERVAL),
        )
        self.entry = entry
        self.client = CodexClient(async_get_clientsession(hass))

    async def _async_renew_login(self) -> None:
        credentials = await self.client.refresh(dict(self.entry.data))
        self.hass.config_entries.async_update_entry(self.entry, data=credentials)

    async def _async_update_data(self) -> Usage:
        try:
            refreshed = False
            if self.entry.data["expires_at"] <= time() + 60:
                await self._async_renew_login()
                refreshed = True
            try:
                response = await self.client.fetch_usage(self.entry.data)
            except AuthError:
                if refreshed:
                    raise
                await self._async_renew_login()
                response = await self.client.fetch_usage(self.entry.data)
            account_id = response.get("account_id")
            if not isinstance(account_id, str) or not account_id:
                raise DataError("The usage response did not identify an account")
            if account_id != self.entry.data["account_id"]:
                raise AuthError("The usage response belongs to a different account")
            return parse_usage(response)
        except AuthError as error:
            raise ConfigEntryAuthFailed(str(error)) from error
        except (ConnectionError, DataError) as error:
            raise UpdateFailed(str(error)) from error
