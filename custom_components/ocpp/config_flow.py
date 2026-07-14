"""Adds config flow for ocpp."""
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
import voluptuous as vol

from ocpp.v16.enums import AuthorizationStatus

from .const import (
    CONF_DEFAULT_AUTH_STATUS,
    CONF_CPID,
    CONF_CSID,
    CONF_ENERGY_PRICE_SENSOR,
    CONF_FORCE_SMART_CHARGING,
    CONF_HOST,
    CONF_IDLE_INTERVAL,
    CONF_MAX_CURRENT,
    CONF_METER_INTERVAL,
    CONF_MONITORED_VARIABLES,
    CONF_PORT,
    CONF_SKIP_SCHEMA_VALIDATION,
    CONF_SSL,
    CONF_SSL_CERTFILE_PATH,
    CONF_SSL_KEYFILE_PATH,
    CONF_WEBSOCKET_CLOSE_TIMEOUT,
    CONF_WEBSOCKET_PING_INTERVAL,
    CONF_WEBSOCKET_PING_TIMEOUT,
    CONF_WEBSOCKET_PING_TRIES,
    DEFAULT_CPID,
    DEFAULT_CSID,
    DEFAULT_FORCE_SMART_CHARGING,
    DEFAULT_HOST,
    DEFAULT_IDLE_INTERVAL,
    DEFAULT_MAX_CURRENT,
    DEFAULT_METER_INTERVAL,
    DEFAULT_MONITORED_VARIABLES,
    DEFAULT_PORT,
    DEFAULT_SKIP_SCHEMA_VALIDATION,
    DEFAULT_SSL,
    DEFAULT_SSL_CERTFILE_PATH,
    DEFAULT_SSL_KEYFILE_PATH,
    DEFAULT_WEBSOCKET_CLOSE_TIMEOUT,
    DEFAULT_WEBSOCKET_PING_INTERVAL,
    DEFAULT_WEBSOCKET_PING_TIMEOUT,
    DEFAULT_WEBSOCKET_PING_TRIES,
    DOMAIN,
    ENTRY_TYPE,
    ENTRY_TYPE_CENTRAL,
    ENTRY_TYPE_USERS,
    energy_price_divisor,
)
from .user_registry import async_get_user_registry

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Required(CONF_SSL, default=DEFAULT_SSL): bool,
        vol.Required(CONF_SSL_CERTFILE_PATH, default=DEFAULT_SSL_CERTFILE_PATH): str,
        vol.Required(CONF_SSL_KEYFILE_PATH, default=DEFAULT_SSL_KEYFILE_PATH): str,
        vol.Required(CONF_CSID, default=DEFAULT_CSID): str,
        vol.Required(CONF_CPID, default=DEFAULT_CPID): str,
        vol.Required(CONF_MAX_CURRENT, default=DEFAULT_MAX_CURRENT): int,
        vol.Required(
            CONF_MONITORED_VARIABLES, default=DEFAULT_MONITORED_VARIABLES
        ): str,
        vol.Required(CONF_METER_INTERVAL, default=DEFAULT_METER_INTERVAL): int,
        vol.Required(CONF_IDLE_INTERVAL, default=DEFAULT_IDLE_INTERVAL): int,
        vol.Required(
            CONF_WEBSOCKET_CLOSE_TIMEOUT, default=DEFAULT_WEBSOCKET_CLOSE_TIMEOUT
        ): int,
        vol.Required(
            CONF_WEBSOCKET_PING_TRIES, default=DEFAULT_WEBSOCKET_PING_TRIES
        ): int,
        vol.Required(
            CONF_WEBSOCKET_PING_INTERVAL, default=DEFAULT_WEBSOCKET_PING_INTERVAL
        ): int,
        vol.Required(
            CONF_WEBSOCKET_PING_TIMEOUT, default=DEFAULT_WEBSOCKET_PING_TIMEOUT
        ): int,
        vol.Required(
            CONF_SKIP_SCHEMA_VALIDATION, default=DEFAULT_SKIP_SCHEMA_VALIDATION
        ): bool,
        vol.Required(
            CONF_FORCE_SMART_CHARGING, default=DEFAULT_FORCE_SMART_CHARGING
        ): bool,
    }
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for OCPP."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    def __init__(self):
        """Initialize."""
        self._data = {}

    async def async_step_user(self, user_input=None):
        """Handle user initiated configuration."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Todo: validate the user input
            self._data = user_input
            self._data[CONF_MONITORED_VARIABLES] = DEFAULT_MONITORED_VARIABLES
            self._data[ENTRY_TYPE] = ENTRY_TYPE_CENTRAL
            return self.async_create_entry(title=self._data[CONF_CSID], data=self._data)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_import(self, user_input=None):
        """Create internal entries imported by the integration."""
        if user_input and user_input.get(ENTRY_TYPE) == ENTRY_TYPE_USERS:
            await self.async_set_unique_id(f"{DOMAIN}_{ENTRY_TYPE_USERS}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="Benutzer", data=user_input)

        return await self.async_step_user(user_input)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the options flow."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle OCPP options."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self._config_entry = config_entry
        self._user_id = None

    async def async_step_init(self, user_input=None):
        """Show the options menu."""
        return self._show_main_menu()

    def _show_main_menu(self):
        """Show the user-management main menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "settings",
                "add_user",
                "edit_user",
                "toggle_user",
                "delete_user",
            ],
        )

    async def _async_finish_to_main_menu(self, options=None):
        """Persist options and return to the user-management main menu."""
        if options is not None:
            self.hass.config_entries.async_update_entry(
                self._config_entry, options=options
            )
        return await self.async_step_init()

    async def async_step_settings(self, user_input=None):
        """Configure OCPP user defaults."""
        errors = {}
        current_status = self._config_entry.options.get(
            CONF_DEFAULT_AUTH_STATUS,
            self._config_entry.data.get(
                CONF_DEFAULT_AUTH_STATUS, AuthorizationStatus.accepted.value
            ),
        )
        current_energy_price_sensor = self._config_entry.options.get(
            CONF_ENERGY_PRICE_SENSOR,
            self._config_entry.data.get(CONF_ENERGY_PRICE_SENSOR, ""),
        )

        if user_input is not None:
            energy_price_sensor = user_input.get(CONF_ENERGY_PRICE_SENSOR, "")
            if energy_price_sensor:
                state = self.hass.states.get(energy_price_sensor)
                unit = (
                    state.attributes.get("unit_of_measurement", "")
                    if state is not None
                    else ""
                )
                if energy_price_divisor(unit) is None:
                    errors["base"] = "invalid_energy_price_sensor_unit"
            if not errors:
                return await self._async_finish_to_main_menu(
                    {
                        **self._config_entry.options,
                        CONF_DEFAULT_AUTH_STATUS: user_input[
                            CONF_DEFAULT_AUTH_STATUS
                        ],
                        CONF_ENERGY_PRICE_SENSOR: energy_price_sensor,
                    }
                )

        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_DEFAULT_AUTH_STATUS,
                        default=current_status,
                    ): vol.In(
                        [
                            AuthorizationStatus.accepted.value,
                            AuthorizationStatus.blocked.value,
                            AuthorizationStatus.invalid.value,
                        ]
                    ),
                    vol.Optional(
                        CONF_ENERGY_PRICE_SENSOR,
                        default=current_energy_price_sensor or None,
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_add_user(self, user_input=None):
        """Add a managed OCPP user."""
        errors = {}
        if user_input is not None:
            registry = await async_get_user_registry(self.hass)
            id_tags = registry.parse_id_tags(user_input["id_tags"])
            if not user_input["name"].strip() or not id_tags:
                errors["base"] = "invalid_user"
            elif registry.find_conflicting_id_tags(id_tags):
                errors["base"] = "duplicate_id_tag"
            else:
                await registry.async_add_user(
                    user_input["name"], id_tags, user_input["active"]
                )
                return await self.async_step_init()

        return self.async_show_form(
            step_id="add_user",
            data_schema=vol.Schema(
                {
                    vol.Required("name"): str,
                    vol.Required("id_tags"): str,
                    vol.Optional("active", default=True): bool,
                }
            ),
            errors=errors,
        )

    async def async_step_edit_user(self, user_input=None):
        """Select a managed OCPP user to edit."""
        registry = await async_get_user_registry(self.hass)
        users = registry.list_users()
        if not users:
            return self.async_abort(reason="no_users")

        if user_input is not None:
            self._user_id = user_input["user_id"]
            return await self.async_step_edit_user_form()

        return self.async_show_form(
            step_id="edit_user",
            data_schema=vol.Schema(
                {
                    vol.Required("user_id"): vol.In(
                        {user["user_id"]: user["name"] for user in users}
                    )
                }
            ),
        )

    async def async_step_edit_user_form(self, user_input=None):
        """Edit a managed OCPP user."""
        registry = await async_get_user_registry(self.hass)
        user = registry.get_user(self._user_id)
        if user is None:
            return self.async_abort(reason="user_not_found")

        errors = {}
        if user_input is not None:
            id_tags = registry.parse_id_tags(user_input["id_tags"])
            if not user_input["name"].strip() or not id_tags:
                errors["base"] = "invalid_user"
            elif registry.find_conflicting_id_tags(id_tags, self._user_id):
                errors["base"] = "duplicate_id_tag"
            else:
                await registry.async_update_user(
                    self._user_id,
                    name=user_input["name"],
                    id_tags=id_tags,
                    active=user_input["active"],
                )
                return await self.async_step_init()

        return self.async_show_form(
            step_id="edit_user_form",
            data_schema=vol.Schema(
                {
                    vol.Required("name", default=user["name"]): str,
                    vol.Required(
                        "id_tags", default=", ".join(user.get("id_tags", []))
                    ): str,
                    vol.Optional("active", default=user.get("active", True)): bool,
                }
            ),
            errors=errors,
        )

    async def async_step_toggle_user(self, user_input=None):
        """Activate or deactivate a managed OCPP user."""
        registry = await async_get_user_registry(self.hass)
        users = registry.list_users()
        if not users:
            return self.async_abort(reason="no_users")

        if user_input is not None:
            self._user_id = user_input["user_id"]
            return await self.async_step_toggle_user_form()

        return self.async_show_form(
            step_id="toggle_user",
            data_schema=vol.Schema(
                {
                    vol.Required("user_id"): vol.In(
                        {
                            user["user_id"]: "{} ({})".format(
                                user["name"],
                                "active" if user.get("active", True) else "inactive",
                            )
                            for user in users
                        }
                    )
                }
            ),
        )

    async def async_step_toggle_user_form(self, user_input=None):
        """Set the active state for a managed OCPP user."""
        registry = await async_get_user_registry(self.hass)
        user = registry.get_user(self._user_id)
        if user is None:
            return self.async_abort(reason="user_not_found")

        if user_input is not None:
            await registry.async_update_user(
                user["user_id"], active=user_input["active"]
            )
            return await self.async_step_init()

        return self.async_show_form(
            step_id="toggle_user_form",
            data_schema=vol.Schema(
                {
                    vol.Optional("active", default=user.get("active", True)): bool,
                }
            ),
        )

    async def async_step_delete_user(self, user_input=None):
        """Select a managed OCPP user to delete."""
        registry = await async_get_user_registry(self.hass)
        users = registry.list_users()
        if not users:
            return self.async_abort(reason="no_users")

        if user_input is not None:
            self._user_id = user_input["user_id"]
            return await self.async_step_delete_user_form()

        return self.async_show_form(
            step_id="delete_user",
            data_schema=vol.Schema(
                {
                    vol.Required("user_id"): vol.In(
                        {user["user_id"]: user["name"] for user in users}
                    )
                }
            ),
        )

    async def async_step_delete_user_form(self, user_input=None):
        """Confirm deleting a managed OCPP user."""
        registry = await async_get_user_registry(self.hass)
        user = registry.get_user(self._user_id)
        if user is None:
            return self.async_abort(reason="user_not_found")

        if user_input is not None:
            if user_input["confirm_delete"]:
                await registry.async_delete_user(user["user_id"])
            return await self.async_step_init()

        return self.async_show_form(
            step_id="delete_user_form",
            data_schema=vol.Schema(
                {
                    vol.Optional("confirm_delete", default=False): bool,
                }
            ),
        )
