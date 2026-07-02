"""Button platform for ocpp."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from homeassistant.components.button import (
    DOMAIN as BUTTON_DOMAIN,
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, EntityCategory

from .api import CentralSystem
from .const import CONF_CPID, DATA_USERS_UPDATED, DEFAULT_CPID, DOMAIN
from .enums import HAChargerServices
from .user_registry import async_get_user_registry


@dataclass
class OcppButtonDescription(ButtonEntityDescription):
    """Class to describe a Button entity."""

    press_action: str | None = None


BUTTONS: Final = [
    OcppButtonDescription(
        key="reset",
        name="Reset",
        translation_key="reset",
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.CONFIG,
        press_action=HAChargerServices.service_reset.name,
    ),
    OcppButtonDescription(
        key="unlock",
        name="Unlock",
        translation_key="unlock",
        device_class=ButtonDeviceClass.UPDATE,
        entity_category=EntityCategory.CONFIG,
        press_action=HAChargerServices.service_unlock.name,
    ),
    OcppButtonDescription(
        key="start_selected_user_charge",
        name="Start Selected User Charge",
        translation_key="start_selected_user_charge",
        icon="mdi:ev-station",
    ),
]


async def async_setup_entry(hass, entry, async_add_devices):
    """Configure the Button platform."""

    central_system = hass.data[DOMAIN][entry.entry_id]
    cp_id = entry.data.get(CONF_CPID, DEFAULT_CPID)

    entities = []

    for ent in BUTTONS:
        entities.append(ChargePointButton(central_system, cp_id, ent))

    async_add_devices(entities, False)

    registry = await async_get_user_registry(hass)
    known_user_ids = set()

    @callback
    def add_missing_user_buttons():
        new_entities = []
        for user in registry.list_users():
            user_id = user["user_id"]
            if user_id in known_user_ids:
                continue
            known_user_ids.add(user_id)
            new_entities.append(
                UserChargeStartButton(central_system, cp_id, user_id)
            )
        if new_entities:
            async_add_devices(new_entities, False)

    add_missing_user_buttons()
    entry.async_on_unload(
        async_dispatcher_connect(hass, DATA_USERS_UPDATED, add_missing_user_buttons)
    )


class ChargePointButton(ButtonEntity):
    """Individual button for charge point."""

    _attr_has_entity_name = True
    entity_description: OcppButtonDescription

    def __init__(
        self,
        central_system: CentralSystem,
        cp_id: str,
        description: OcppButtonDescription,
    ):
        """Instantiate instance of a ChargePointButton."""
        self.cp_id = cp_id
        self.central_system = central_system
        self.entity_description = description
        self._attr_unique_id = ".".join(
            [BUTTON_DOMAIN, DOMAIN, self.cp_id, self.entity_description.key]
        )
        self._attr_translation_key = self.entity_description.translation_key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.cp_id)},
            via_device=(DOMAIN, self.central_system.id),
        )

    @property
    def available(self) -> bool:
        """Return charger availability."""
        if self.entity_description.key == "start_selected_user_charge":
            return (
                self.central_system.get_available(self.cp_id)
                and self.central_system.get_selected_user(self.cp_id) is not None
            )
        return self.central_system.get_available(self.cp_id)  # type: ignore [no-any-return]

    async def async_press(self) -> None:
        """Triggers the charger press action service."""
        if self.entity_description.key == "start_selected_user_charge":
            await self.central_system.start_transaction_for_selected_user(self.cp_id)
            return
        await self.central_system.set_charger_state(
            self.cp_id, self.entity_description.press_action
        )


class UserChargeStartButton(ButtonEntity):
    """Button to remote start charging for one managed OCPP user."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:ev-station"

    def __init__(
        self,
        central_system: CentralSystem,
        cp_id: str,
        user_id: str,
    ):
        """Instantiate a user charge start button."""
        self.cp_id = cp_id
        self.central_system = central_system
        self.user_id = user_id
        self._attr_unique_id = ".".join(
            [BUTTON_DOMAIN, DOMAIN, self.cp_id, "start_charge_user", self.user_id]
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.cp_id)},
            via_device=(DOMAIN, self.central_system.id),
        )

    @property
    def name(self) -> str | None:
        """Return the button name."""
        user = self._user
        name = user.get("name") if user is not None else self.user_id
        return f"Ladevorgang {name} starten"

    @property
    def _user(self):
        """Return the current user record."""
        if self.central_system.user_registry is None:
            return None
        return self.central_system.user_registry.get_user(self.user_id)

    @property
    def available(self) -> bool:
        """Return if user charging can be started."""
        user = self._user
        return (
            self.central_system.get_available(self.cp_id)
            and user is not None
            and user.get("active", True)
            and bool(user.get("id_tags", []))
        )

    async def async_press(self) -> None:
        """Start charging for this user."""
        await self.central_system.start_transaction_for_user(self.cp_id, self.user_id)
