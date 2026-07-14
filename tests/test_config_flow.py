"""Test ocpp config flow."""
from unittest.mock import AsyncMock, Mock, patch

from homeassistant import config_entries, data_entry_flow
from ocpp.v16.enums import AuthorizationStatus
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ocpp.const import (  # BINARY_SENSOR,; PLATFORMS,; SENSOR,; SWITCH,
    CONF_DEFAULT_AUTH_STATUS,
    CONF_ENERGY_PRICE_SENSOR,
    DOMAIN,
)
from custom_components.ocpp.user_registry import OcppUserRegistry

from .const import MOCK_CONFIG, MOCK_CONFIG_DATA


# This fixture bypasses the actual setup of the integration
# since we only want to test the config flow. We test the
# actual functionality of the integration in other test modules.
@pytest.fixture(autouse=True)
def bypass_setup_fixture():
    """Prevent setup."""
    with patch(
        "custom_components.ocpp.async_setup",
        return_value=True,
    ), patch(
        "custom_components.ocpp.async_setup_entry",
        return_value=True,
    ):
        yield


# Here we simiulate a successful config flow from the backend.
# Note that we use the `bypass_get_data` fixture here because
# we want the config flow validation to succeed during the test.
async def test_successful_config_flow(hass, bypass_get_data):
    """Test a successful config flow."""
    # Initialize a config flow
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    # Check that the config flow shows the user form as the first step
    assert result["type"] == data_entry_flow.RESULT_TYPE_FORM
    assert result["step_id"] == "user"

    # If a user were to enter `test_username` for username and `test_password`
    # for password, it would result in this function call
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input=MOCK_CONFIG
    )

    # Check that the config flow is complete and a new entry is created with
    # the input data
    assert result["type"] == data_entry_flow.RESULT_TYPE_CREATE_ENTRY
    assert result["title"] == "test_csid"
    assert result["data"] == MOCK_CONFIG_DATA
    assert result["result"]


async def _init_options_flow(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, data=MOCK_CONFIG_DATA, options={}, entry_id="test_options"
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] == data_entry_flow.RESULT_TYPE_MENU
    assert result["step_id"] == "init"
    return entry, result


async def _select_options_menu_item(hass, flow_id, next_step_id):
    result = await hass.config_entries.options.async_configure(
        flow_id, user_input={"next_step_id": next_step_id}
    )

    assert result["type"] == data_entry_flow.RESULT_TYPE_FORM
    assert result["step_id"] == next_step_id
    return result


async def test_options_settings_ok_returns_to_main_menu(hass):
    """Test saving settings returns to the options main menu."""
    hass.states.async_set(
        "sensor.energy_price",
        "0.25",
        {"unit_of_measurement": "EUR/kWh"},
    )
    entry, result = await _init_options_flow(hass)
    result = await _select_options_menu_item(hass, result["flow_id"], "settings")

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_DEFAULT_AUTH_STATUS: AuthorizationStatus.blocked.value,
            CONF_ENERGY_PRICE_SENSOR: "sensor.energy_price",
        },
    )

    assert result["type"] == data_entry_flow.RESULT_TYPE_MENU
    assert result["step_id"] == "init"
    assert entry.options[CONF_DEFAULT_AUTH_STATUS] == AuthorizationStatus.blocked.value
    assert entry.options[CONF_ENERGY_PRICE_SENSOR] == "sensor.energy_price"


async def test_options_settings_rejects_cost_rate_sensor(hass):
    """Test settings reject EUR/h cost sensors as an energy price source."""
    hass.states.async_set(
        "sensor.running_energy_cost",
        "2.58",
        {"unit_of_measurement": "EUR/h"},
    )
    entry, result = await _init_options_flow(hass)
    result = await _select_options_menu_item(hass, result["flow_id"], "settings")

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_DEFAULT_AUTH_STATUS: AuthorizationStatus.blocked.value,
            CONF_ENERGY_PRICE_SENSOR: "sensor.running_energy_cost",
        },
    )

    assert result["type"] == data_entry_flow.RESULT_TYPE_FORM
    assert result["errors"] == {"base": "invalid_energy_price_sensor_unit"}
    assert entry.options == {}


class _MockUserRegistry:
    """Minimal user registry for options flow tests."""

    def __init__(self):
        self.users = [
            {
                "user_id": "user-1",
                "name": "Test User",
                "id_tags": ["ABC"],
                "active": True,
            }
        ]
        self.added_user = None
        self.updated_user = None
        self.deleted_user_id = None

    @staticmethod
    def parse_id_tags(value):
        """Parse test idTags."""
        return [tag.strip() for tag in value.split(",") if tag.strip()]

    def find_conflicting_id_tags(self, id_tags, user_id=None):
        """Return no conflicts in tests."""
        return []

    def list_users(self):
        """Return test users."""
        return self.users

    def get_user(self, user_id):
        """Return a test user by id."""
        return next((user for user in self.users if user["user_id"] == user_id), None)

    async def async_add_user(self, name, id_tags, active):
        """Record added user."""
        self.added_user = {"name": name, "id_tags": id_tags, "active": active}

    async def async_update_user(self, user_id, **changes):
        """Record updated user."""
        self.updated_user = {"user_id": user_id, **changes}

    async def async_delete_user(self, user_id):
        """Record deleted user."""
        self.deleted_user_id = user_id


async def test_options_add_user_ok_returns_to_main_menu(hass):
    """Test adding a user returns to the options main menu."""
    registry = _MockUserRegistry()
    _, result = await _init_options_flow(hass)
    result = await _select_options_menu_item(hass, result["flow_id"], "add_user")

    with patch(
        "custom_components.ocpp.config_flow.async_get_user_registry",
        return_value=registry,
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                "name": "New User",
                "id_tags": "TAG1, TAG2",
                "active": True,
            },
        )

    assert result["type"] == data_entry_flow.RESULT_TYPE_MENU
    assert result["step_id"] == "init"
    assert registry.added_user == {
        "name": "New User",
        "id_tags": ["TAG1", "TAG2"],
        "active": True,
    }


async def test_options_edit_user_ok_returns_to_main_menu(hass):
    """Test editing a user returns to the options main menu."""
    registry = _MockUserRegistry()
    _, result = await _init_options_flow(hass)

    with patch(
        "custom_components.ocpp.config_flow.async_get_user_registry",
        return_value=registry,
    ):
        result = await _select_options_menu_item(hass, result["flow_id"], "edit_user")
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"user_id": "user-1"}
        )
        assert result["type"] == data_entry_flow.RESULT_TYPE_FORM
        assert result["step_id"] == "edit_user_form"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                "name": "Updated User",
                "id_tags": "XYZ",
                "active": False,
            },
        )

    assert result["type"] == data_entry_flow.RESULT_TYPE_MENU
    assert result["step_id"] == "init"
    assert registry.updated_user == {
        "user_id": "user-1",
        "name": "Updated User",
        "id_tags": ["XYZ"],
        "active": False,
    }


async def test_options_toggle_user_ok_returns_to_main_menu(hass):
    """Test changing a user's active state returns to the options main menu."""
    registry = _MockUserRegistry()
    _, result = await _init_options_flow(hass)

    with patch(
        "custom_components.ocpp.config_flow.async_get_user_registry",
        return_value=registry,
    ):
        result = await _select_options_menu_item(
            hass, result["flow_id"], "toggle_user"
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"user_id": "user-1"}
        )
        assert result["type"] == data_entry_flow.RESULT_TYPE_FORM
        assert result["step_id"] == "toggle_user_form"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"active": False}
        )

    assert result["type"] == data_entry_flow.RESULT_TYPE_MENU
    assert result["step_id"] == "init"
    assert registry.updated_user == {"user_id": "user-1", "active": False}


async def test_options_delete_user_ok_returns_to_main_menu(hass):
    """Test deleting a user returns to the options main menu."""
    registry = _MockUserRegistry()
    _, result = await _init_options_flow(hass)

    with patch(
        "custom_components.ocpp.config_flow.async_get_user_registry",
        return_value=registry,
    ):
        result = await _select_options_menu_item(
            hass, result["flow_id"], "delete_user"
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"user_id": "user-1"}
        )
        assert result["type"] == data_entry_flow.RESULT_TYPE_FORM
        assert result["step_id"] == "delete_user_form"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"confirm_delete": True}
        )

    assert result["type"] == data_entry_flow.RESULT_TYPE_MENU
    assert result["step_id"] == "init"
    assert registry.deleted_user_id == "user-1"


async def test_user_registry_delete_user_removes_user_and_sessions(hass):
    """Test deleting a user removes stored data for that user."""
    registry = OcppUserRegistry(hass)
    registry.users = {
        "user-1": {"user_id": "user-1", "name": "Test User"},
        "user-2": {"user_id": "user-2", "name": "Other User"},
    }
    registry.sessions = {
        "charger:1": {"user_id": "user-1"},
        "charger:2": {"user_id": "user-2"},
    }
    registry.async_save = AsyncMock()
    registry.notify_updated = Mock()

    await registry.async_delete_user("user-1")

    assert "user-1" not in registry.users
    assert registry.sessions == {"charger:2": {"user_id": "user-2"}}
    registry.async_save.assert_awaited_once()
    registry.notify_updated.assert_called_once()


def test_user_registry_adds_session_energy_to_current_month(hass):
    """Test user session energy is tracked in total and monthly counters."""
    registry = OcppUserRegistry(hass)
    registry.users = {
        "user-1": {
            "user_id": "user-1",
            "name": "Test User",
            "id_tags": ["ABC"],
            "energy_kwh": 10.0,
            "monthly_energy_kwh": 4.0,
            "monthly_cost": 1.0,
            "monthly_energy_period": "2026-07",
        }
    }
    registry.sessions = {
        "charger:1": {
            "transaction_id": 1,
            "user_id": "user-1",
            "id_tag": "ABC",
            "cp_id": "charger",
            "meter_start_kwh": 10.0,
        }
    }
    registry.current_month_period = Mock(return_value="2026-07")
    registry.schedule_save = Mock()
    registry.notify_updated = Mock()

    registry.record_stop_transaction(1, "charger", 11.25, energy_price=0.4)

    assert registry.users["user-1"]["energy_kwh"] == 11.25
    assert registry.users["user-1"]["monthly_energy_kwh"] == 5.25
    assert registry.users["user-1"]["monthly_cost"] == 1.5
    assert registry.users["user-1"]["monthly_energy_period"] == "2026-07"
    registry.schedule_save.assert_called_once()
    registry.notify_updated.assert_called_once()


def test_user_registry_adds_live_session_energy_deltas(hass):
    """Test live session energy only adds newly measured deltas."""
    registry = OcppUserRegistry(hass)
    registry.users = {
        "user-1": {
            "user_id": "user-1",
            "name": "Test User",
            "id_tags": ["ABC"],
            "energy_kwh": 10.0,
            "monthly_energy_kwh": 4.0,
            "monthly_cost": 1.0,
            "monthly_energy_period": "2026-07",
        }
    }
    registry.sessions = {
        "charger:1": {
            "transaction_id": 1,
            "user_id": "user-1",
            "id_tag": "ABC",
            "cp_id": "charger",
            "meter_start_kwh": 10.0,
            "credited_energy_kwh": 0.0,
        }
    }
    registry.current_month_period = Mock(return_value="2026-07")
    registry.schedule_save = Mock()
    registry.notify_updated = Mock()

    assert registry.record_session_energy(1, "charger", 0.5, 0.3) == 0.5
    assert registry.record_session_energy(1, "charger", 0.75, 0.4) == 0.25
    assert registry.record_session_energy(1, "charger", 0.7, 0.5) == 0.0
    registry.record_stop_transaction(1, "charger", 11.0, energy_price=0.6)

    assert registry.users["user-1"]["energy_kwh"] == 11.0
    assert registry.users["user-1"]["monthly_energy_kwh"] == 5.0
    assert registry.users["user-1"]["monthly_cost"] == 1.4
    assert registry.users["user-1"]["last_session_energy_kwh"] == 1.0
    assert "charger:1" not in registry.sessions
    assert registry.schedule_save.call_count == 3
    assert registry.notify_updated.call_count == 3


def test_user_registry_returns_active_session(hass):
    """Test active sessions can be restored by charger and transaction id."""
    registry = OcppUserRegistry(hass)
    registry.sessions = {
        "charger:1": {
            "transaction_id": 1,
            "user_id": "user-1",
            "id_tag": "ABC",
            "cp_id": "charger",
        }
    }

    assert registry.get_session("charger", 1)["user_id"] == "user-1"
    assert registry.get_session("charger", "1")["id_tag"] == "ABC"
    assert registry.get_session("charger", 0) is None
    assert registry.get_session("other", 1) is None


def test_user_registry_resets_monthly_counter_on_month_change(hass):
    """Test monthly user energy resets when closing a session in a new month."""
    registry = OcppUserRegistry(hass)
    registry.users = {
        "user-1": {
            "user_id": "user-1",
            "name": "Test User",
            "id_tags": ["ABC"],
            "energy_kwh": 10.0,
            "monthly_energy_kwh": 4.0,
            "monthly_cost": 1.0,
            "monthly_energy_period": "2026-06",
        }
    }
    registry.sessions = {
        "charger:1": {
            "transaction_id": 1,
            "user_id": "user-1",
            "id_tag": "ABC",
            "cp_id": "charger",
            "meter_start_kwh": 10.0,
        }
    }
    registry.current_month_period = Mock(return_value="2026-07")
    registry.schedule_save = Mock()
    registry.notify_updated = Mock()

    registry.record_stop_transaction(1, "charger", 11.25, energy_price=0.4)

    assert registry.users["user-1"]["energy_kwh"] == 11.25
    assert registry.users["user-1"]["monthly_energy_kwh"] == 1.25
    assert registry.users["user-1"]["monthly_cost"] == 0.5
    assert registry.users["user-1"]["monthly_energy_period"] == "2026-07"


# In this case, we want to simulate a failure during the config flow.
# We use the `error_on_get_data` mock instead of `bypass_get_data`
# (note the function parameters) to raise an Exception during
# validation of the input config.
# async def test_failed_config_flow(hass, error_on_get_data):
#     """Test a failed config flow due to credential validation failure."""
#
#     result = await hass.config_entries.flow.async_init(
#         DOMAIN, context={"source": config_entries.SOURCE_USER}
#     )
#
#     assert result["type"] == data_entry_flow.RESULT_TYPE_FORM
#     assert result["step_id"] == "user"
#
#     result = await hass.config_entries.flow.async_configure(
#         result["flow_id"], user_input=MOCK_CONFIG
#     )
#
#     assert result["type"] == data_entry_flow.RESULT_TYPE_FORM
#     assert result["errors"] == {"base": "auth"}
#
#
# # Our config flow also has an options flow, so we must test it as well.
# async def test_options_flow(hass):
#     """Test an options flow."""
#     # Create a new MockConfigEntry and add to HASS (we're bypassing config
#     # flow entirely)
#     entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")
#     entry.add_to_hass(hass)
#
#     # Initialize an options flow
#     await hass.config_entries.async_setup(entry.entry_id)
#     result = await hass.config_entries.options.async_init(entry.entry_id)
#
#     # Verify that the first options step is a user form
#     assert result["type"] == data_entry_flow.RESULT_TYPE_FORM
#     assert result["step_id"] == "user"
#
#     # Enter some fake data into the form
#     result = await hass.config_entries.options.async_configure(
#         result["flow_id"],
#         user_input={platform: platform != SENSOR for platform in PLATFORMS},
#     )
#
#     # Verify that the flow finishes
#     assert result["type"] == data_entry_flow.RESULT_TYPE_CREATE_ENTRY
#     assert result["title"] == "test_username"
#
#     # Verify that the options were updated
#     assert entry.options == {BINARY_SENSOR: True, SENSOR: False, SWITCH: True}
