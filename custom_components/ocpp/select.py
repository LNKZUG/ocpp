"""Select platform for ocpp."""
from __future__ import annotations

from collections import Counter

from homeassistant.components.select import DOMAIN as SELECT_DOMAIN, SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo

from .api import CentralSystem
from .const import CONF_CPID, DATA_USERS_UPDATED, DEFAULT_CPID, DOMAIN
from .user_registry import async_get_user_registry


async def async_setup_entry(hass, entry, async_add_devices):
    """Configure the Select platform."""
    central_system = hass.data[DOMAIN][entry.entry_id]
    cp_id = entry.data.get(CONF_CPID, DEFAULT_CPID)
    await async_get_user_registry(hass)

    entity = ChargeStartUserSelect(hass, central_system, cp_id)
    async_add_devices([entity], False)


def charge_start_user_options(registry):
    """Return active OCPP users mapped to unique select option labels."""
    users = [
        user
        for user in registry.list_users()
        if user.get("active", True) and user.get("id_tags", [])
    ]
    name_counts = Counter(user["name"] for user in users)
    options = {}
    for user in users:
        name = user["name"]
        label = name
        if name_counts[name] > 1:
            label = f"{name} ({user['user_id']})"
        options[label] = user
    return options


class ChargeStartUserSelect(SelectEntity):
    """Select the managed OCPP user used by the start button."""

    _attr_has_entity_name = True
    _attr_translation_key = "charge_start_user"
    _attr_icon = "mdi:account-bolt"

    def __init__(
        self,
        hass: HomeAssistant,
        central_system: CentralSystem,
        cp_id: str,
    ):
        """Instantiate the charge start user select."""
        self.hass = hass
        self.central_system = central_system
        self.cp_id = cp_id
        self._attr_unique_id = ".".join(
            [SELECT_DOMAIN, DOMAIN, self.cp_id, "charge_start_user"]
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.cp_id)},
            via_device=(DOMAIN, self.central_system.id),
        )

    async def async_added_to_hass(self) -> None:
        """Register update listeners."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, DATA_USERS_UPDATED, self._schedule_immediate_update
            )
        )

    @callback
    def _schedule_immediate_update(self) -> None:
        """Refresh select state after the user registry changes."""
        self.async_schedule_update_ha_state(True)

    @property
    def available(self) -> bool:
        """Return if the charger and registry are available."""
        return (
            self.central_system.get_available(self.cp_id)
            and self.central_system.user_registry is not None
        )

    @property
    def _option_users(self):
        """Return select options mapped to user records."""
        if self.central_system.user_registry is None:
            return {}
        return charge_start_user_options(self.central_system.user_registry)

    @property
    def options(self) -> list[str]:
        """Return available user names."""
        return list(self._option_users)

    @property
    def current_option(self) -> str | None:
        """Return the selected user name."""
        selected_user = self.central_system.get_selected_user(self.cp_id)
        if selected_user is None:
            return None
        for option, user in self._option_users.items():
            if user["user_id"] == selected_user["user_id"]:
                return option
        self.central_system.set_selected_user(self.cp_id, None)
        return None

    async def async_select_option(self, option: str) -> None:
        """Select a managed OCPP user for remote start."""
        user = self._option_users.get(option)
        if user is None:
            return
        if self.central_system.set_selected_user(self.cp_id, user["user_id"]):
            self.async_write_ha_state()
            self.hass.async_create_task(self.central_system.update(self.cp_id))
