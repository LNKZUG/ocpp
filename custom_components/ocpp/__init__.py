"""Custom integration for Chargers that support the Open Charge Point Protocol."""

import asyncio
import logging

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers import device_registry
import homeassistant.helpers.config_validation as cv
import voluptuous as vol

from ocpp.v16.enums import AuthorizationStatus

from .api import CentralSystem
from .const import (
    CONF_AUTH_LIST,
    CONF_AUTH_STATUS,
    CONF_CPID,
    CONF_CSID,
    CONF_DEFAULT_AUTH_STATUS,
    CONF_ID_TAG,
    CONF_NAME,
    CONFIG,
    DEFAULT_CPID,
    DEFAULT_CSID,
    DOMAIN,
    ENTRY_TYPE,
    ENTRY_TYPE_CENTRAL,
    ENTRY_TYPE_USERS,
    PLATFORMS,
    SENSOR,
)
from .user_registry import async_get_user_registry

_LOGGER: logging.Logger = logging.getLogger(__package__)
logging.getLogger(DOMAIN).setLevel(logging.INFO)

SETUP_LOCKS = "setup_locks"

AUTH_LIST_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ID_TAG): cv.string,
        vol.Optional(CONF_NAME): cv.string,
        vol.Optional(CONF_AUTH_STATUS): cv.string,
    }
)

CONFIG_SCHEMA = vol.Schema(
    {
        vol.Optional(
            CONF_DEFAULT_AUTH_STATUS, default=AuthorizationStatus.accepted.value
        ): cv.string,
        vol.Optional(CONF_AUTH_LIST, default={}): vol.Schema(
            {cv.string: AUTH_LIST_SCHEMA}
        ),
    },
    extra=vol.ALLOW_EXTRA,
)

ADD_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_ID_TAG): cv.string,
        vol.Optional("active", default=True): cv.boolean,
    }
)

UPDATE_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ID_TAG): cv.string,
        vol.Optional(CONF_NAME): cv.string,
        vol.Optional("new_id_tags"): cv.string,
        vol.Optional("active"): cv.boolean,
    }
)

SET_USER_ACTIVE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ID_TAG): cv.string,
        vol.Required("active"): cv.boolean,
    }
)


async def async_setup_user_services(hass: HomeAssistant) -> None:
    """Register OCPP user management actions."""
    if hass.services.has_service(DOMAIN, "add_user"):
        return

    async def handle_add_user(call):
        registry = await async_get_user_registry(hass)
        id_tags = registry.parse_id_tags(call.data[CONF_ID_TAG])
        if not id_tags:
            raise HomeAssistantError("At least one id_tag is required")
        conflicts = registry.find_conflicting_id_tags(id_tags)
        if conflicts:
            raise HomeAssistantError(
                "OCPP id_tag already assigned: {}".format(", ".join(conflicts))
            )
        await registry.async_add_user(
            call.data[CONF_NAME],
            id_tags,
            call.data["active"],
        )

    async def handle_update_user(call):
        registry = await async_get_user_registry(hass)
        user = registry.get_user_for_id_tag(call.data[CONF_ID_TAG])
        if user is None:
            raise HomeAssistantError("No OCPP user found for id_tag")

        id_tags = None
        if "new_id_tags" in call.data:
            id_tags = registry.parse_id_tags(call.data["new_id_tags"])
            conflicts = registry.find_conflicting_id_tags(id_tags, user["user_id"])
            if conflicts:
                raise HomeAssistantError(
                    "OCPP id_tag already assigned: {}".format(", ".join(conflicts))
                )

        await registry.async_update_user(
            user["user_id"],
            name=call.data.get(CONF_NAME),
            id_tags=id_tags,
            active=call.data.get("active"),
        )

    async def handle_set_user_active(call):
        registry = await async_get_user_registry(hass)
        user = registry.get_user_for_id_tag(call.data[CONF_ID_TAG])
        if user is None:
            raise HomeAssistantError("No OCPP user found for id_tag")
        await registry.async_update_user(
            user["user_id"],
            active=call.data["active"],
        )

    hass.services.async_register(DOMAIN, "add_user", handle_add_user, ADD_USER_SCHEMA)
    hass.services.async_register(
        DOMAIN,
        "update_user",
        handle_update_user,
        UPDATE_USER_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "set_user_active",
        handle_set_user_active,
        SET_USER_ACTIVE_SCHEMA,
    )


async def async_setup(hass: HomeAssistant, config: ConfigType):
    """Read configuration from yaml."""

    ocpp_config = config.get(DOMAIN, {})
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}
    hass.data[DOMAIN][CONFIG] = ocpp_config
    await async_setup_user_services(hass)
    _LOGGER.info(f"config = {ocpp_config}")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up this integration from config entry."""
    if hass.data.get(DOMAIN) is None:
        hass.data.setdefault(DOMAIN, {})
        _LOGGER.info(entry.data)

    if entry.entry_id in hass.data[DOMAIN]:
        return True

    await async_get_user_registry(hass)
    await async_setup_user_services(hass)

    entry_type = entry.data.get(ENTRY_TYPE, ENTRY_TYPE_CENTRAL)
    if entry_type == ENTRY_TYPE_USERS:
        await hass.config_entries.async_forward_entry_setups(entry, [SENSOR])
        return True

    if not any(
        existing_entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_USERS
        for existing_entry in hass.config_entries.async_entries(DOMAIN)
    ):
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_IMPORT},
            data={ENTRY_TYPE: ENTRY_TYPE_USERS},
        )

    setup_locks = hass.data[DOMAIN].setdefault(SETUP_LOCKS, {})
    setup_lock = setup_locks.setdefault(entry.entry_id, asyncio.Lock())

    async with setup_lock:
        if entry.entry_id in hass.data[DOMAIN]:
            return True

        return await _async_setup_central_entry_locked(hass, entry)


async def _async_setup_central_entry_locked(hass: HomeAssistant, entry: ConfigEntry):
    """Start the central system once the entry setup lock is held."""
    central_sys = await CentralSystem.create(hass, entry)

    dr = device_registry.async_get(hass)

    """ Create Central System Device """
    dr.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.data.get(CONF_CSID, DEFAULT_CSID))},
        name=entry.data.get(CONF_CSID, DEFAULT_CSID),
        model="OCPP Central System",
    )

    """ Create Charge Point Device """
    dr.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.data.get(CONF_CPID, DEFAULT_CPID))},
        name=entry.data.get(CONF_CPID, DEFAULT_CPID),
        model="Unknown",
        via_device=(DOMAIN, entry.data.get(CONF_CSID, DEFAULT_CSID)),
    )

    hass.data[DOMAIN][entry.entry_id] = central_sys

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    if entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_USERS:
        return await hass.config_entries.async_unload_platforms(entry, [SENSOR])

    central_sys = hass.data[DOMAIN].get(entry.entry_id)
    if central_sys is None:
        return True

    central_sys._server.close()
    await central_sys._server.wait_closed()

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
        hass.data[DOMAIN].get(SETUP_LOCKS, {}).pop(entry.entry_id, None)

    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
