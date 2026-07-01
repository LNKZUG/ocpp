"""Adds config flow for ocpp."""
from homeassistant import config_entries
import voluptuous as vol

from .const import (
    CONF_CPID,
    CONF_CSID,
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
            return self.async_create_entry(title=self._data[CONF_CSID], data=self._data)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        """Create the options flow."""
        return OcppOptionsFlowHandler(config_entry)


class OcppOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle OCPP options."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry
        self._selected_user_id = None

    async def async_step_init(self, user_input=None):
        """Manage OCPP options."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["add_user", "edit_user", "toggle_user"],
        )

    async def async_step_add_user(self, user_input=None):
        """Add an OCPP user."""
        errors = {}
        registry = await async_get_user_registry(self.hass)

        if user_input is not None:
            id_tags = registry.parse_id_tags(user_input["id_tags"])
            if not str(user_input["name"]).strip() or not id_tags:
                errors["base"] = "invalid_user"
            elif registry.find_conflicting_id_tags(id_tags):
                errors["base"] = "duplicate_id_tag"
            else:
                await registry.async_add_user(
                    user_input["name"],
                    id_tags,
                    user_input["active"],
                )
                return self.async_create_entry(
                    title="",
                    data=dict(self.config_entry.options),
                )

        return self.async_show_form(
            step_id="add_user",
            data_schema=vol.Schema(
                {
                    vol.Required("name"): str,
                    vol.Required("id_tags"): str,
                    vol.Required("active", default=True): bool,
                }
            ),
            errors=errors,
        )

    async def async_step_edit_user(self, user_input=None):
        """Choose a user to edit."""
        registry = await async_get_user_registry(self.hass)
        users = registry.list_users()
        if not users:
            return self.async_abort(reason="no_users")

        if user_input is not None:
            self._selected_user_id = user_input["user_id"]
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
        """Edit a selected OCPP user."""
        errors = {}
        registry = await async_get_user_registry(self.hass)
        user = registry.get_user(self._selected_user_id)
        if user is None:
            return self.async_abort(reason="user_not_found")

        if user_input is not None:
            id_tags = registry.parse_id_tags(user_input["id_tags"])
            if not str(user_input["name"]).strip() or not id_tags:
                errors["base"] = "invalid_user"
            elif registry.find_conflicting_id_tags(id_tags, self._selected_user_id):
                errors["base"] = "duplicate_id_tag"
            else:
                await registry.async_update_user(
                    self._selected_user_id,
                    name=user_input["name"],
                    id_tags=id_tags,
                    active=user_input["active"],
                )
                return self.async_create_entry(
                    title="",
                    data=dict(self.config_entry.options),
                )

        return self.async_show_form(
            step_id="edit_user_form",
            data_schema=vol.Schema(
                {
                    vol.Required("name", default=user["name"]): str,
                    vol.Required("id_tags", default=", ".join(user["id_tags"])): str,
                    vol.Required("active", default=user.get("active", True)): bool,
                }
            ),
            errors=errors,
        )

    async def async_step_toggle_user(self, user_input=None):
        """Enable or disable an OCPP user."""
        registry = await async_get_user_registry(self.hass)
        users = registry.list_users()
        if not users:
            return self.async_abort(reason="no_users")

        if user_input is not None:
            user = registry.get_user(user_input["user_id"])
            if user is None:
                return self.async_abort(reason="user_not_found")
            await registry.async_update_user(
                user["user_id"],
                active=not user.get("active", True),
            )
            return self.async_create_entry(
                title="",
                data=dict(self.config_entry.options),
            )

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
