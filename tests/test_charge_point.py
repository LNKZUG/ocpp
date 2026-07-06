"""Implement a test by a simulating a chargepoint."""
import asyncio
from collections import defaultdict
from datetime import datetime, timezone  # timedelta,
from types import SimpleNamespace

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.button.const import SERVICE_PRESS
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.switch import (
    DOMAIN as SWITCH_DOMAIN,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.const import ATTR_ENTITY_ID
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import websockets

from custom_components.ocpp import async_setup_entry, async_unload_entry
import custom_components.ocpp.api as ocpp_api
from custom_components.ocpp.api import (
    CentralSystem,
    ChargePoint as OcppChargePoint,
    Metric,
    truncate_status_notification_info,
)
from custom_components.ocpp.button import BUTTONS
from custom_components.ocpp.const import DOMAIN as OCPP_DOMAIN
from custom_components.ocpp.enums import (
    ConfigurationKey,
    HAChargerDetails as cdet,
    HAChargerSession as csess,
    HAChargerStatuses as cstat,
    HAChargerServices as csvcs,
    Profiles as prof,
)
from custom_components.ocpp.number import NUMBERS
from custom_components.ocpp.switch import ChargePointSwitch, SWITCHES
from ocpp.routing import on
from ocpp.v16 import ChargePoint as cpclass, call, call_result
from ocpp.v16.enums import (
    Action,
    AuthorizationStatus,
    AvailabilityStatus,
    ChargePointErrorCode,
    ChargePointStatus,
    ChargingProfileStatus,
    ClearChargingProfileStatus,
    ConfigurationStatus,
    DataTransferStatus,
    DiagnosticsStatus,
    FirmwareStatus,
    RegistrationStatus,
    RemoteStartStopStatus,
    ResetStatus,
    ResetType,
    TriggerMessageStatus,
    UnitOfMeasure,
    UnlockStatus,
    Measurand,
)

from .const import MOCK_CONFIG_DATA, MOCK_CONFIG_DATA_2


async def test_supported_features_timeout_defaults_to_core():
    """Test chargers that do not answer SupportedFeatureProfiles."""

    async def call_timeout(req):
        raise asyncio.TimeoutError

    charge_point = object.__new__(OcppChargePoint)
    charge_point.id = "test_cpid"
    charge_point.central = SimpleNamespace(config={})
    charge_point._attr_supported_features = prof.NONE
    charge_point._metrics = defaultdict(lambda: Metric(None, None))
    charge_point.call = call_timeout

    await charge_point.get_supported_features()

    assert charge_point._attr_supported_features == prof.CORE
    assert charge_point._metrics[cdet.features.value].value == prof.CORE


def test_truncate_status_notification_info():
    """Test non-compliant StatusNotification info values are trimmed."""
    payload = {"info": "H8.Charge station gun signal is error, please reinsert"}

    assert truncate_status_notification_info(
        Action.status_notification.value, payload
    )
    assert payload["info"] == "H8.Charge station gun signal is error, please rein"
    assert len(payload["info"]) == 50


async def test_evse_suspended_auto_stop_sends_remote_stop():
    """Test persistent SuspendedEVSE schedules a remote transaction stop."""

    stopped = False
    triggered = False

    async def stop_transaction():
        nonlocal stopped
        stopped = True
        return True

    async def trigger_status_notification():
        nonlocal triggered
        triggered = True
        return True

    charge_point = object.__new__(OcppChargePoint)
    charge_point.id = "test_cpid"
    charge_point.hass = SimpleNamespace(async_create_task=asyncio.create_task)
    charge_point._metrics = defaultdict(lambda: Metric(None, None))
    charge_point._metrics[cstat.status_connector.value].value = (
        ChargePointStatus.suspended_evse.value
    )
    charge_point._metrics[Measurand.power_active_import.value].value = 0
    charge_point._metrics[Measurand.current_import.value].value = 0
    charge_point.active_transaction_id = 123
    charge_point.auto_stop_on_evse_suspended = True
    charge_point.auto_stop_delay = 0
    charge_point._auto_stop_task = None
    charge_point.stop_transaction = stop_transaction
    charge_point.trigger_status_notification = trigger_status_notification

    charge_point._schedule_auto_stop_on_evse_suspended("test")
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert stopped is True
    assert triggered is True


async def test_pending_remote_start_cleanup_unlocks_and_refreshes(monkeypatch):
    """Test a remote start that never becomes a transaction is cleaned up."""

    unlocked = False
    triggered = False
    reset_type = None

    async def unlock():
        nonlocal unlocked
        unlocked = True
        return True

    async def trigger_status_notification():
        nonlocal triggered
        triggered = True
        return True

    async def reset(typ):
        nonlocal reset_type
        reset_type = typ
        return True

    monkeypatch.setattr(ocpp_api, "REMOTE_START_CLEANUP_DELAY", 0)
    monkeypatch.setattr(ocpp_api, "STALE_CONNECTOR_RESET_DELAY", 0)

    charge_point = object.__new__(OcppChargePoint)
    charge_point.id = "test_cpid"
    charge_point.hass = SimpleNamespace(async_create_task=asyncio.create_task)
    charge_point._metrics = defaultdict(lambda: Metric(None, None))
    charge_point._metrics[cstat.status_connector.value].value = (
        ChargePointStatus.preparing.value
    )
    charge_point.active_transaction_id = 0
    charge_point._remote_start_cleanup_task = None
    charge_point.unlock = unlock
    charge_point.trigger_status_notification = trigger_status_notification
    charge_point.reset = reset

    charge_point._schedule_remote_start_cleanup("ABC")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert unlocked is True
    assert triggered is True
    assert reset_type == ResetType.soft


async def test_stale_preparing_without_transaction_schedules_cleanup(monkeypatch):
    """Test Preparing without a transaction is recovered even without a start task."""

    unlocked = False
    triggered = False
    reset_type = None

    async def unlock():
        nonlocal unlocked
        unlocked = True
        return True

    async def trigger_status_notification():
        nonlocal triggered
        triggered = True
        return True

    async def reset(typ):
        nonlocal reset_type
        reset_type = typ
        return True

    monkeypatch.setattr(ocpp_api, "REMOTE_START_CLEANUP_DELAY", 0)
    monkeypatch.setattr(ocpp_api, "STALE_CONNECTOR_RESET_DELAY", 0)

    async def update(cp_id):
        return None

    charge_point = object.__new__(OcppChargePoint)
    charge_point.id = "test_cpid"
    charge_point.hass = SimpleNamespace(async_create_task=asyncio.create_task)
    charge_point.central = SimpleNamespace(
        cpid="test_cpid",
        update=update,
    )
    charge_point._metrics = defaultdict(lambda: Metric(None, None))
    charge_point._metrics[cstat.id_tag.value].value = "ABC"
    charge_point._metrics[csess.current_user.value].value = "Lukas"
    charge_point._metrics[csess.current_user.value].extra_attr = {"id_tag": "ABC"}
    charge_point.active_transaction_id = 0
    charge_point._auto_stop_task = None
    charge_point._remote_start_cleanup_task = None
    charge_point.unlock = unlock
    charge_point.trigger_status_notification = trigger_status_notification
    charge_point.reset = reset

    charge_point.on_status_notification(
        connector_id=1,
        error_code=ChargePointErrorCode.no_error.value,
        status=ChargePointStatus.preparing.value,
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert unlocked is True
    assert triggered is True
    assert reset_type == ResetType.soft
    assert charge_point._metrics[cstat.id_tag.value].value is None
    assert charge_point._metrics[csess.current_user.value].value is None
    assert charge_point._metrics[csess.current_user.value].extra_attr == {}


def test_current_user_metric_maps_id_tag_to_managed_user():
    """Test current wallbox user is mapped from the transaction idTag."""

    class UserRegistryStub:
        """Minimal user registry test double."""

        def __init__(self):
            self.stop_recorded = False

        def get_authorization_status(self, id_tag):
            """Accept the managed idTag."""
            return AuthorizationStatus.accepted.value

        def get_user_for_id_tag(self, id_tag):
            """Return a managed user for the idTag."""
            if id_tag == "ABC":
                return {"user_id": "lukas", "name": "Lukas"}
            return None

        def record_start_transaction(self, *args):
            """Record start transaction calls."""

        def record_stop_transaction(self, *args):
            """Record stop transaction calls."""
            self.stop_recorded = True

    class HassStub:
        """Minimal Home Assistant test double."""

        def async_create_task(self, task):
            """Close scheduled coroutine from central.update."""
            task.close()

    async def update(_cpid):
        return None

    charge_point = object.__new__(OcppChargePoint)
    charge_point.id = "charger"
    charge_point.hass = HassStub()
    charge_point.central = SimpleNamespace(
        cpid="charger",
        config={},
        user_registry=UserRegistryStub(),
        update=update,
    )
    charge_point._metrics = defaultdict(lambda: Metric(None, None))
    charge_point._cancel_auto_stop = lambda: None

    result = charge_point.on_start_transaction(
        connector_id=1,
        id_tag="ABC",
        meter_start=1000,
    )

    assert result.id_tag_info["status"] == AuthorizationStatus.accepted.value
    assert charge_point._metrics[csess.current_user.value].value == "Lukas"
    assert charge_point._metrics[csess.current_user.value].extra_attr == {
        "id_tag": "ABC",
        "user_id": "lukas",
    }
    assert charge_point._metrics[cstat.id_tag.value].value == "ABC"

    charge_point.on_stop_transaction(
        meter_stop=2000,
        timestamp=datetime.now(tz=timezone.utc).isoformat(),
        transaction_id=charge_point.active_transaction_id,
    )

    assert charge_point._metrics[cstat.id_tag.value].value is None
    assert charge_point._metrics[csess.current_user.value].value is None
    assert charge_point._metrics[csess.current_user.value].extra_attr == {}
    assert charge_point._metrics[csess.monthly_energy.value].value == 1.0
    assert charge_point._metrics[csess.monthly_energy.value].extra_attr[
        "reset_cycle"
    ] == "monthly"
    assert charge_point.central.user_registry.stop_recorded is True


def test_meter_values_add_live_user_session_energy():
    """Test MeterValues add live session energy to the managed user registry."""

    class UserRegistryStub:
        """Minimal user registry test double."""

        def __init__(self):
            self.recorded_session_energy = None

        def record_session_energy(self, *args):
            """Record live session energy calls."""
            self.recorded_session_energy = args

    class HassStub:
        """Minimal Home Assistant test double."""

        def async_create_task(self, task):
            """Close scheduled coroutine from central.update."""
            task.close()

    async def update(_cpid):
        return None

    charge_point = object.__new__(OcppChargePoint)
    charge_point.id = "charger"
    charge_point.hass = HassStub()
    charge_point.central = SimpleNamespace(
        cpid="charger",
        user_registry=UserRegistryStub(),
        update=update,
    )
    charge_point.active_transaction_id = 123
    charge_point._charger_reports_session_energy = False
    charge_point._metrics = defaultdict(lambda: Metric(None, None))
    charge_point._metrics[csess.transaction_id.value].value = 123
    charge_point._metrics[csess.meter_start.value].value = 10.0
    charge_point._metrics[csess.current_user.value].value = "Lukas"
    charge_point._metrics[cstat.id_tag.value].value = "ABC"

    charge_point.on_meter_values(
        connector_id=1,
        transaction_id=123,
        meter_value=[
            {
                "sampledValue": [
                    {
                        "value": "11500",
                        "measurand": Measurand.energy_active_import_register.value,
                        "unit": UnitOfMeasure.wh.value,
                    }
                ]
            }
        ],
    )

    assert charge_point._metrics[csess.session_energy.value].value == 1.5
    assert charge_point.central.user_registry.recorded_session_energy == (
        123,
        "charger",
        1.5,
    )


def test_remote_start_for_user_uses_managed_user_id_tag():
    """Test user remote start uses the selected managed user's idTag."""

    class UserRegistryStub:
        """Minimal user registry test double."""

        def get_user(self, user_id):
            """Return a managed user."""
            if user_id == "lukas":
                return {
                    "user_id": "lukas",
                    "name": "Lukas",
                    "id_tags": ["ABC"],
                    "active": True,
                }
            return None

    class ChargePointStub:
        """Minimal charge point test double."""

        def __init__(self):
            self.started_id_tag = None

        async def start_transaction(self, id_tag=None):
            """Record remote start idTag."""
            self.started_id_tag = id_tag
            return True

    charge_point = ChargePointStub()
    central_system = object.__new__(CentralSystem)
    central_system.user_registry = UserRegistryStub()
    central_system.charge_points = {"charger": charge_point}

    result = asyncio.run(
        central_system.start_transaction_for_user("charger", "lukas")
    )

    assert result is True
    assert charge_point.started_id_tag == "ABC"


def test_remote_start_for_selected_user_uses_managed_user_id_tag():
    """Test selected-user remote start uses the selected managed user's idTag."""

    class UserRegistryStub:
        """Minimal user registry test double."""

        def get_user(self, user_id):
            """Return a managed user."""
            if user_id == "lukas":
                return {
                    "user_id": "lukas",
                    "name": "Lukas",
                    "id_tags": ["ABC"],
                    "active": True,
                }
            return None

    class ChargePointStub:
        """Minimal charge point test double."""

        def __init__(self):
            self.started_id_tag = None

        async def start_transaction(self, id_tag=None):
            """Record remote start idTag."""
            self.started_id_tag = id_tag
            return True

    charge_point = ChargePointStub()
    central_system = object.__new__(CentralSystem)
    central_system.user_registry = UserRegistryStub()
    central_system.selected_user_ids = {"charger": "lukas"}
    central_system.charge_points = {"charger": charge_point}

    result = asyncio.run(central_system.start_transaction_for_selected_user("charger"))

    assert result is True
    assert charge_point.started_id_tag == "ABC"


def test_charge_control_switch_requires_active_transaction():
    """Test charge control is not on for waiting states without a transaction."""

    class CentralSystemStub:
        """Minimal central system test double."""

        id = "central"

        def __init__(self):
            self.active = False

        def get_available(self, cp_id):
            """Return charger availability."""
            return True

        def get_metric(self, cp_id, metric):
            """Return connector status."""
            return ChargePointStatus.suspended_ev.value

        def has_active_transaction(self, cp_id):
            """Return active transaction state."""
            return self.active

    central_system = CentralSystemStub()
    switch = ChargePointSwitch(central_system, "charger", SWITCHES[0])

    assert switch.is_on is False

    central_system.active = True

    assert switch.is_on is True


def test_available_status_clears_authorized_id_tag_without_transaction():
    """Test idTag is cleared when authorization expires before a transaction starts."""

    class UserRegistryStub:
        """Minimal user registry test double."""

        def get_authorization_status(self, id_tag):
            """Accept the managed idTag."""
            return AuthorizationStatus.accepted.value

    class HassStub:
        """Minimal Home Assistant test double."""

        def async_create_task(self, task):
            """Close scheduled coroutine from central.update."""
            task.close()

    async def update(_cpid):
        return None

    charge_point = object.__new__(OcppChargePoint)
    charge_point.id = "charger"
    charge_point.hass = HassStub()
    charge_point.central = SimpleNamespace(
        cpid="charger",
        config={},
        user_registry=UserRegistryStub(),
        update=update,
    )
    charge_point.active_transaction_id = 0
    charge_point._metrics = defaultdict(lambda: Metric(None, None))
    charge_point._cancel_auto_stop = lambda: None

    charge_point.on_authorize(id_tag="ABC")

    assert charge_point._metrics[cstat.id_tag.value].value == "ABC"

    charge_point.on_status_notification(
        connector_id=1,
        error_code=ChargePointErrorCode.no_error.value,
        status=ChargePointStatus.available.value,
    )

    assert charge_point._metrics[cstat.id_tag.value].value is None


def test_current_user_restores_from_persisted_session():
    """Test current wallbox user is restored from the active registry session."""

    class UserRegistryStub:
        """Minimal user registry test double."""

        def get_session(self, cp_id, transaction_id):
            """Return an active session for the transaction."""
            if cp_id == "charger" and transaction_id == 123:
                return {
                    "transaction_id": 123,
                    "user_id": "lukas",
                    "id_tag": "ABC",
                    "cp_id": "charger",
                }
            return None

        def get_user(self, user_id):
            """Return the managed user."""
            if user_id == "lukas":
                return {"user_id": "lukas", "name": "Lukas"}
            return None

    charge_point = object.__new__(OcppChargePoint)
    charge_point.central = SimpleNamespace(
        cpid="charger",
        user_registry=UserRegistryStub(),
    )
    charge_point._metrics = defaultdict(lambda: Metric(None, None))

    charge_point.restore_current_user_from_session(123)

    assert charge_point._metrics[cstat.id_tag.value].value == "ABC"
    assert charge_point._metrics[csess.current_user.value].value == "Lukas"
    assert charge_point._metrics[csess.current_user.value].extra_attr == {
        "id_tag": "ABC",
        "user_id": "lukas",
    }


@pytest.mark.timeout(90)  # Set timeout for this test
async def test_cms_responses(hass, socket_enabled):
    """Test central system responses to a charger."""

    async def test_switches(hass, socket_enabled):
        """Test switch operations."""
        for switch in SWITCHES:
            await hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_ON,
                service_data={
                    ATTR_ENTITY_ID: f"{SWITCH_DOMAIN}.test_cpid_{switch.key}"
                },
                blocking=True,
            )

            await asyncio.sleep(1)
            await hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_OFF,
                service_data={
                    ATTR_ENTITY_ID: f"{SWITCH_DOMAIN}.test_cpid_{switch.key}"
                },
                blocking=True,
            )

    async def test_buttons(hass, socket_enabled):
        """Test button operations."""
        for button in BUTTONS:
            await hass.services.async_call(
                BUTTON_DOMAIN,
                SERVICE_PRESS,
                {ATTR_ENTITY_ID: f"{BUTTON_DOMAIN}.test_cpid_{button.key}"},
                blocking=True,
            )

    async def test_services(hass, socket_enabled):
        """Test service operations."""
        SERVICES = [
            csvcs.service_update_firmware,
            csvcs.service_configure,
            csvcs.service_get_configuration,
            csvcs.service_get_diagnostics,
            csvcs.service_clear_profile,
            csvcs.service_data_transfer,
            csvcs.service_set_charge_rate,
        ]
        for service in SERVICES:
            data = {}
            if service == csvcs.service_update_firmware:
                data = {"firmware_url": "http://www.charger.com/firmware.bin"}
            if service == csvcs.service_configure:
                data = {"ocpp_key": "WebSocketPingInterval", "value": "60"}
            if service == csvcs.service_get_configuration:
                data = {"ocpp_key": "UnknownKeyTest"}
            if service == csvcs.service_get_diagnostics:
                data = {"upload_url": "https://webhook.site/abc"}
            if service == csvcs.service_data_transfer:
                data = {"vendor_id": "ABC"}
            if service == csvcs.service_set_charge_rate:
                data = {"limit_amps": 30}

            await hass.services.async_call(
                OCPP_DOMAIN,
                service.value,
                service_data=data,
                blocking=True,
            )
        # test additional set charge rate options
        await hass.services.async_call(
            OCPP_DOMAIN,
            csvcs.service_set_charge_rate,
            service_data={"limit_watts": 3000},
            blocking=True,
        )
        # test custom charge profile for advanced use
        prof = {
            "chargingProfileId": 8,
            "stackLevel": 6,
            "chargingProfileKind": "Relative",
            "chargingProfilePurpose": "ChargePointMaxProfile",
            "chargingSchedule": {
                "chargingRateUnit": "A",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": 16.0}],
            },
        }
        data = {"custom_profile": str(prof)}
        await hass.services.async_call(
            OCPP_DOMAIN,
            csvcs.service_set_charge_rate,
            service_data=data,
            blocking=True,
        )

        for number in NUMBERS:
            # test setting value of number slider
            await hass.services.async_call(
                NUMBER_DOMAIN,
                "set_value",
                service_data={"value": "10"},
                blocking=True,
                target={ATTR_ENTITY_ID: f"{NUMBER_DOMAIN}.test_cpid_{number.key}"},
            )

    # Test MOCK_CONFIG_DATA_2
    if True:
        # Create a mock entry so we don't have to go through config flow
        config_entry2 = MockConfigEntry(
            domain=OCPP_DOMAIN,
            data=MOCK_CONFIG_DATA_2,
            entry_id="test_cms2",
            title="test_cms2",
        )
        config_entry2.add_to_hass(hass)

        assert await async_setup_entry(hass, config_entry2)
        await hass.async_block_till_done()

        # no subprotocol
        async with websockets.connect(
            "ws://127.0.0.1:9002/CP_1_nosub",
        ) as ws2:
            # use a different id for debugging
            cp2 = ChargePoint("CP_1_no_subprotocol", ws2)
            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        cp2.start(),
                        cp2.send_boot_notification(),
                        cp2.send_authorize(),
                        cp2.send_heartbeat(),
                        cp2.send_status_notification(),
                        cp2.send_firmware_status(),
                        cp2.send_data_transfer(),
                        cp2.send_start_transaction(),
                        cp2.send_stop_transaction(),
                        cp2.send_meter_periodic_data(),
                    ),
                    timeout=5,
                )
            except asyncio.TimeoutError:
                pass
            await ws2.close()
        await asyncio.sleep(1)
        await async_unload_entry(hass, config_entry2)
        await hass.async_block_till_done()

    # Create a mock entry so we don't have to go through config flow
    config_entry = MockConfigEntry(
        domain=OCPP_DOMAIN, data=MOCK_CONFIG_DATA, entry_id="test_cms", title="test_cms"
    )
    config_entry.add_to_hass(hass)
    assert await async_setup_entry(hass, config_entry)
    await hass.async_block_till_done()

    cs = hass.data[OCPP_DOMAIN][config_entry.entry_id]

    # no subprotocol
    async with websockets.connect(
        "ws://127.0.0.1:9000/CP_1_unsup",
    ) as ws:
        # use a different id for debugging
        cp = ChargePoint("CP_1_no_subprotocol", ws)
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    cp.start(),
                    cp.send_boot_notification(),
                    cp.send_authorize(),
                    cp.send_heartbeat(),
                    cp.send_status_notification(),
                    cp.send_firmware_status(),
                    cp.send_data_transfer(),
                    cp.send_start_transaction(),
                    cp.send_stop_transaction(),
                    cp.send_meter_periodic_data(),
                ),
                timeout=5,
            )
        except websockets.exceptions.ConnectionClosedOK:
            pass
        await ws.close()

    await asyncio.sleep(1)

    # unsupported subprotocol
    async with websockets.connect(
        "ws://127.0.0.1:9000/CP_1_unsup",
        subprotocols=["ocpp0.0"],
    ) as ws:
        # use a different id for debugging
        cp = ChargePoint("CP_1_unsupported_subprotocol", ws)
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    cp.start(),
                    cp.send_boot_notification(),
                    cp.send_authorize(),
                    cp.send_heartbeat(),
                    cp.send_status_notification(),
                    cp.send_firmware_status(),
                    cp.send_data_transfer(),
                    cp.send_start_transaction(),
                    cp.send_stop_transaction(),
                    cp.send_meter_periodic_data(),
                ),
                timeout=5,
            )
        except websockets.exceptions.ConnectionClosedOK:
            pass
        await ws.close()

    await asyncio.sleep(1)

    # test restore feature of meter_start and active_tranasction_id.
    async with websockets.connect(
        "ws://127.0.0.1:9000/CP_1_res_vals",
        subprotocols=["ocpp1.6"],
    ) as ws:
        # use a different id for debugging
        cp = ChargePoint("CP_1_restore_values", ws)
        cp.active_transactionId = None
        # send None values
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    cp.start(),
                    cp.send_meter_periodic_data(),
                ),
                timeout=5,
            )
        except asyncio.TimeoutError:
            pass
        # check if None
        assert cs.get_metric("test_cpid", "Energy.Meter.Start") is None
        assert cs.get_metric("test_cpid", "Transaction.Id") is None
        # send new data
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    cp.send_start_transaction(12344),
                    cp.send_meter_periodic_data(),
                ),
                timeout=5,
            )
        except asyncio.TimeoutError:
            pass
        # save for reference the values for meter_start and transaction_id
        saved_meter_start = int(cs.get_metric("test_cpid", "Energy.Meter.Start"))
        saved_transactionId = int(cs.get_metric("test_cpid", "Transaction.Id"))
        # delete current values from api memory
        cs.del_metric("test_cpid", "Energy.Meter.Start")
        cs.del_metric("test_cpid", "Transaction.Id")
        # send new data
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    cp.send_meter_periodic_data(),
                ),
                timeout=5,
            )
        except asyncio.TimeoutError:
            pass
        await ws.close()

    # check if restored old values from HA when api have lost the values, i.e. simulated reboot of HA
    assert int(cs.get_metric("test_cpid", "Energy.Meter.Start")) == saved_meter_start
    assert int(cs.get_metric("test_cpid", "Transaction.Id")) == saved_transactionId

    await asyncio.sleep(1)

    # test ocpp messages sent from charger to cms
    async with websockets.connect(
        "ws://127.0.0.1:9000/CP_1_norm",
        subprotocols=["ocpp1.5", "ocpp1.6"],
    ) as ws:
        # use a different id for debugging
        cp = ChargePoint("CP_1_normal", ws)
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    cp.start(),
                    cp.send_boot_notification(),
                    cp.send_authorize(),
                    cp.send_heartbeat(),
                    cp.send_status_notification(),
                    cp.send_security_event(),
                    cp.send_firmware_status(),
                    cp.send_data_transfer(),
                    cp.send_start_transaction(12345),
                    cp.send_meter_err_phases(),
                    cp.send_meter_line_voltage(),
                    cp.send_meter_periodic_data(),
                    # add delay to allow meter data to be processed
                    cp.send_stop_transaction(1),
                ),
                timeout=5,
            )
        except asyncio.TimeoutError:
            pass
        await ws.close()
    assert int(cs.get_metric("test_cpid", "Energy.Active.Import.Register")) == int(
        1305570 / 1000
    )
    assert int(cs.get_metric("test_cpid", "Energy.Session")) == int(
        (54321 - 12345) / 1000
    )
    assert int(cs.get_metric("test_cpid", "Current.Import")) == int(0)
    assert int(cs.get_metric("test_cpid", "Voltage")) == int(228)
    assert cs.get_unit("test_cpid", "Energy.Active.Import.Register") == "kWh"
    assert cs.get_metric("unknown_cpid", "Energy.Active.Import.Register") is None
    assert cs.get_unit("unknown_cpid", "Energy.Active.Import.Register") is None
    assert cs.get_extra_attr("unknown_cpid", "Energy.Active.Import.Register") is None
    assert int(cs.get_supported_features("unknown_cpid")) == int(0)
    assert (
        await asyncio.wait_for(
            cs.set_max_charge_rate_amps("unknown_cpid", 0), timeout=1
        )
        is False
    )
    assert (
        await asyncio.wait_for(
            cs.set_charger_state("unknown_cpid", csvcs.service_clear_profile, False),
            timeout=1,
        )
        is False
    )

    await asyncio.sleep(1)
    # test ocpp messages sent from cms to charger, through HA switches/services
    # should reconnect as already started above
    # test processing of clock aligned meter data
    async with websockets.connect(
        "ws://127.0.0.1:9000/CP_1_serv",
        subprotocols=["ocpp1.6"],
    ) as ws:
        cp = ChargePoint("CP_1_services", ws)
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    cp.start(),
                    cs.charge_points[cs.cpid].trigger_boot_notification(),
                    cs.charge_points[cs.cpid].trigger_status_notification(),
                    test_switches(hass, socket_enabled),
                    test_services(hass, socket_enabled),
                    test_buttons(hass, socket_enabled),
                    cp.send_meter_clock_data(),
                ),
                timeout=5,
            )
        except asyncio.TimeoutError:
            pass
        await ws.close()
    assert int(cs.get_metric("test_cpid", "Frequency")) == int(50)
    assert float(cs.get_metric("test_cpid", "Energy.Active.Import.Register")) == float(
        1101.452
    )

    await asyncio.sleep(1)

    # test ocpp messages sent from charger that don't support errata 3.9
    # i.e. "Energy.Meter.Start" starts from 0 for each session and "Energy.Active.Import.Register"
    # reports starting from 0 Wh for every new transaction id. Total main meter values are without transaction id.
    async with websockets.connect(
        "ws://127.0.0.1:9000/CP_1_non_er_3.9",
        subprotocols=["ocpp1.6"],
    ) as ws:
        # use a different id for debugging
        cp = ChargePoint("CP_1_non_errata_3.9", ws)
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    cp.start(),
                    cp.send_start_transaction(0),
                    cp.send_meter_periodic_data(),
                    cp.send_main_meter_clock_data(),
                    # add delay to allow meter data to be processed
                    cp.send_stop_transaction(1),
                ),
                timeout=5,
            )
        except asyncio.TimeoutError:
            pass
        await ws.close()

    # Last sent "Energy.Active.Import.Register" value without transaction id should be here.
    assert int(cs.get_metric("test_cpid", "Energy.Active.Import.Register")) == int(
        67230012 / 1000
    )
    assert cs.get_unit("test_cpid", "Energy.Active.Import.Register") == "kWh"

    # Last sent "Energy.Active.Import.Register" value with transaction id should be here.
    assert int(cs.get_metric("test_cpid", "Energy.Session")) == int(1305570 / 1000)
    assert cs.get_unit("test_cpid", "Energy.Session") == "kWh"

    await asyncio.sleep(1)

    # test ocpp messages sent from charger that don't support errata 3.9 with meter values with kWh as energy unit
    async with websockets.connect(
        "ws://127.0.0.1:9000/CP_1_non_er_3.9",
        subprotocols=["ocpp1.6"],
    ) as ws:
        # use a different id for debugging
        cp = ChargePoint("CP_1_non_errata_3.9", ws)
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    cp.start(),
                    cp.send_start_transaction(0),
                    cp.send_meter_energy_kwh(),
                    cp.send_meter_clock_data(),
                    # add delay to allow meter data to be processed
                    cp.send_stop_transaction(1),
                ),
                timeout=5,
            )
        except asyncio.TimeoutError:
            pass
        await ws.close()

    assert int(cs.get_metric("test_cpid", "Energy.Active.Import.Register")) == int(1101)
    assert int(cs.get_metric("test_cpid", "Energy.Session")) == int(11)
    assert cs.get_unit("test_cpid", "Energy.Active.Import.Register") == "kWh"

    # test ocpp rejection messages sent from charger to cms
    cs.charge_points["test_cpid"].received_boot_notification = False
    cs.charge_points["test_cpid"].post_connect_success = False
    async with websockets.connect(
        "ws://127.0.0.1:9000/CP_1_error",
        subprotocols=["ocpp1.6"],
    ) as ws:
        cp = ChargePoint("CP_1_error", ws)
        cp.accept = False
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    cp.start(),
                    cs.charge_points[cs.cpid].trigger_boot_notification(),
                    cs.charge_points[cs.cpid].trigger_status_notification(),
                    test_switches(hass, socket_enabled),
                    test_services(hass, socket_enabled),
                    test_buttons(hass, socket_enabled),
                ),
                timeout=3,
            )
        except asyncio.TimeoutError:
            pass
        except websockets.exceptions.ConnectionClosedOK:
            pass
        await ws.close()

    await asyncio.sleep(1)
    # test ping timeout, change cpid to start new connection
    cs.cpid = "CP_3_test"
    async with websockets.connect(
        "ws://127.0.0.1:9000/CP_3",
        subprotocols=["ocpp1.6"],
    ) as ws:
        cp = ChargePoint("CP_3_test", ws)
        ws.state = 3  # CLOSED = 3
        await asyncio.sleep(3)
        await ws.close()

    # test services when charger is unavailable
    await asyncio.sleep(1)
    await test_services(hass, socket_enabled)
    await async_unload_entry(hass, config_entry)
    await hass.async_block_till_done()


class ChargePoint(cpclass):
    """Representation of real client Charge Point."""

    def __init__(self, id, connection, response_timeout=30):
        """Init extra variables for testing."""
        super().__init__(id, connection)
        self.active_transactionId: int = 0
        self.accept: bool = True

    @on(Action.GetConfiguration)
    def on_get_configuration(self, key, **kwargs):
        """Handle a get configuration requests."""
        if key[0] == ConfigurationKey.supported_feature_profiles.value:
            if self.accept is True:
                return call_result.GetConfiguration(
                    configuration_key=[
                        {
                            "key": key[0],
                            "readonly": False,
                            "value": "Core,FirmwareManagement,LocalAuthListManagement,Reservation,SmartCharging,RemoteTrigger,Dummy",
                        }
                    ]
                )
            else:
                return call_result.GetConfiguration(
                    configuration_key=[
                        {
                            "key": key[0],
                            "readonly": False,
                            "value": "",
                        }
                    ]
                )
        if key[0] == ConfigurationKey.heartbeat_interval.value:
            return call_result.GetConfiguration(
                configuration_key=[{"key": key[0], "readonly": False, "value": "300"}]
            )
        if key[0] == ConfigurationKey.number_of_connectors.value:
            return call_result.GetConfiguration(
                configuration_key=[{"key": key[0], "readonly": False, "value": "1"}]
            )
        if key[0] == ConfigurationKey.web_socket_ping_interval.value:
            if self.accept is True:
                return call_result.GetConfiguration(
                    configuration_key=[
                        {"key": key[0], "readonly": False, "value": "60"}
                    ]
                )
            else:
                return call_result.GetConfiguration(
                    unknown_key=["WebSocketPingInterval"]
                )
        if key[0] == ConfigurationKey.meter_values_sampled_data.value:
            return call_result.GetConfiguration(
                configuration_key=[
                    {
                        "key": key[0],
                        "readonly": False,
                        "value": "Energy.Active.Import.Register",
                    }
                ]
            )
        if key[0] == ConfigurationKey.meter_value_sample_interval.value:
            if self.accept is True:
                return call_result.GetConfiguration(
                    configuration_key=[
                        {"key": key[0], "readonly": False, "value": "60"}
                    ]
                )
            else:
                return call_result.GetConfiguration(
                    configuration_key=[{"key": key[0], "readonly": True, "value": "60"}]
                )
        if (
            key[0]
            == ConfigurationKey.charging_schedule_allowed_charging_rate_unit.value
        ):
            return call_result.GetConfiguration(
                configuration_key=[
                    {"key": key[0], "readonly": False, "value": "Current"}
                ]
            )
        if key[0] == ConfigurationKey.authorize_remote_tx_requests.value:
            if self.accept is True:
                return call_result.GetConfiguration(
                    configuration_key=[
                        {"key": key[0], "readonly": False, "value": "false"}
                    ]
                )
            else:
                return call_result.GetConfiguration(unknown_key=[key[0]])
        if key[0] == ConfigurationKey.charge_profile_max_stack_level.value:
            return call_result.GetConfiguration(
                configuration_key=[{"key": key[0], "readonly": False, "value": "3"}]
            )
        return call_result.GetConfiguration(
            configuration_key=[{"key": key[0], "readonly": False, "value": ""}]
        )

    @on(Action.ChangeConfiguration)
    def on_change_configuration(self, **kwargs):
        """Handle a get configuration request."""
        if self.accept is True:
            return call_result.ChangeConfiguration(ConfigurationStatus.accepted)
        else:
            return call_result.ChangeConfiguration(ConfigurationStatus.rejected)

    @on(Action.ChangeAvailability)
    def on_change_availability(self, **kwargs):
        """Handle change availability request."""
        if self.accept is True:
            return call_result.ChangeAvailability(AvailabilityStatus.accepted)
        else:
            return call_result.ChangeAvailability(AvailabilityStatus.rejected)

    @on(Action.UnlockConnector)
    def on_unlock_connector(self, **kwargs):
        """Handle unlock request."""
        if self.accept is True:
            return call_result.UnlockConnector(UnlockStatus.unlocked)
        else:
            return call_result.UnlockConnector(UnlockStatus.unlock_failed)

    @on(Action.Reset)
    def on_reset(self, **kwargs):
        """Handle change availability request."""
        if self.accept is True:
            return call_result.Reset(ResetStatus.accepted)
        else:
            return call_result.Reset(ResetStatus.rejected)

    @on(Action.RemoteStartTransaction)
    def on_remote_start_transaction(self, **kwargs):
        """Handle remote start request."""
        if self.accept is True:
            asyncio.create_task(self.send_start_transaction())
            return call_result.RemoteStartTransaction(RemoteStartStopStatus.accepted)
        else:
            return call_result.RemoteStopTransaction(RemoteStartStopStatus.rejected)

    @on(Action.RemoteStopTransaction)
    def on_remote_stop_transaction(self, **kwargs):
        """Handle remote stop request."""
        if self.accept is True:
            return call_result.RemoteStopTransaction(RemoteStartStopStatus.accepted)
        else:
            return call_result.RemoteStopTransaction(RemoteStartStopStatus.rejected)

    @on(Action.SetChargingProfile)
    def on_set_charging_profile(self, **kwargs):
        """Handle set charging profile request."""
        if self.accept is True:
            return call_result.SetChargingProfile(ChargingProfileStatus.accepted)
        else:
            return call_result.SetChargingProfile(ChargingProfileStatus.rejected)

    @on(Action.ClearChargingProfile)
    def on_clear_charging_profile(self, **kwargs):
        """Handle clear charging profile request."""
        if self.accept is True:
            return call_result.ClearChargingProfile(ClearChargingProfileStatus.accepted)
        else:
            return call_result.ClearChargingProfile(ClearChargingProfileStatus.unknown)

    @on(Action.TriggerMessage)
    def on_trigger_message(self, **kwargs):
        """Handle trigger message request."""
        if self.accept is True:
            return call_result.TriggerMessage(TriggerMessageStatus.accepted)
        else:
            return call_result.TriggerMessage(TriggerMessageStatus.rejected)

    @on(Action.UpdateFirmware)
    def on_update_firmware(self, **kwargs):
        """Handle update firmware request."""
        return call_result.UpdateFirmware()

    @on(Action.GetDiagnostics)
    def on_get_diagnostics(self, **kwargs):
        """Handle get diagnostics request."""
        return call_result.GetDiagnostics()

    @on(Action.DataTransfer)
    def on_data_transfer(self, **kwargs):
        """Handle get data transfer request."""
        if self.accept is True:
            return call_result.DataTransfer(DataTransferStatus.accepted)
        else:
            return call_result.DataTransfer(DataTransferStatus.rejected)

    async def send_boot_notification(self):
        """Send a boot notification."""
        request = call.BootNotification(
            charge_point_model="Optimus", charge_point_vendor="The Mobility House"
        )
        resp = await self.call(request)
        assert resp.status == RegistrationStatus.accepted

    async def send_heartbeat(self):
        """Send a heartbeat."""
        request = call.Heartbeat()
        resp = await self.call(request)
        assert len(resp.current_time) > 0

    async def send_authorize(self):
        """Send an authorize request."""
        request = call.Authorize(id_tag="test_cp")
        resp = await self.call(request)
        assert resp.id_tag_info["status"] == AuthorizationStatus.accepted

    async def send_firmware_status(self):
        """Send a firmware status notification."""
        request = call.FirmwareStatusNotification(status=FirmwareStatus.downloaded)
        resp = await self.call(request)
        assert resp is not None

    async def send_diagnostics_status(self):
        """Send a diagnostics status notification."""
        request = call.DiagnosticsStatusNotification(status=DiagnosticsStatus.uploaded)
        resp = await self.call(request)
        assert resp is not None

    async def send_data_transfer(self):
        """Send a data transfer."""
        request = call.DataTransfer(
            vendor_id="The Mobility House",
            message_id="Test123",
            data="Test data transfer",
        )
        resp = await self.call(request)
        assert resp.status == DataTransferStatus.accepted

    async def send_start_transaction(self, meter_start: int = 12345):
        """Send a start transaction notification."""
        request = call.StartTransaction(
            connector_id=1,
            id_tag="test_cp",
            meter_start=meter_start,
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
        )
        resp = await self.call(request)
        self.active_transactionId = resp.transaction_id
        assert resp.id_tag_info["status"] == AuthorizationStatus.accepted.value

    async def send_status_notification(self):
        """Send a status notification."""
        request = call.StatusNotification(
            connector_id=0,
            error_code=ChargePointErrorCode.no_error,
            status=ChargePointStatus.suspended_ev,
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
            info="Test info",
            vendor_id="The Mobility House",
            vendor_error_code="Test error",
        )
        resp = await self.call(request)
        request = call.StatusNotification(
            connector_id=1,
            error_code=ChargePointErrorCode.no_error,
            status=ChargePointStatus.charging,
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
            info="Test info",
            vendor_id="The Mobility House",
            vendor_error_code="Test error",
        )
        resp = await self.call(request)
        request = call.StatusNotification(
            connector_id=2,
            error_code=ChargePointErrorCode.no_error,
            status=ChargePointStatus.available,
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
            info="Test info",
            vendor_id="The Mobility House",
            vendor_error_code="Available",
        )
        resp = await self.call(request)

        assert resp is not None

    async def send_meter_periodic_data(self):
        """Send periodic meter data notification."""
        n = 0
        while self.active_transactionId == 0 and n < 2:
            await asyncio.sleep(1)
            n += 1
        request = call.MeterValues(
            connector_id=1,
            transaction_id=self.active_transactionId,
            meter_value=[
                {
                    "timestamp": "2021-06-21T16:15:09Z",
                    "sampledValue": [
                        {
                            "value": "1305590.000",
                            "context": "Sample.Periodic",
                            "measurand": "Energy.Active.Import.Register",
                            "location": "Outlet",
                            "unit": "Wh",
                        },
                        {
                            "value": "20.000",
                            "context": "Sample.Periodic",
                            "measurand": "Current.Import",
                            "location": "Outlet",
                            "unit": "A",
                            "phase": "L1",
                        },
                        {
                            "value": "0.000",
                            "context": "Sample.Periodic",
                            "measurand": "Current.Import",
                            "location": "Outlet",
                            "unit": "A",
                            "phase": "L2",
                        },
                        {
                            "value": "0.000",
                            "context": "Sample.Periodic",
                            "measurand": "Current.Import",
                            "location": "Outlet",
                            "unit": "A",
                            "phase": "L3",
                        },
                        {
                            "value": "16.000",
                            "context": "Sample.Periodic",
                            "measurand": "Current.Offered",
                            "location": "Outlet",
                            "unit": "A",
                        },
                        {
                            "value": "50.010",
                            "context": "Sample.Periodic",
                            "measurand": "Frequency",
                            "location": "Outlet",
                        },
                        {
                            "value": "0.000",
                            "context": "Sample.Periodic",
                            "measurand": "Power.Active.Import",
                            "location": "Outlet",
                            "unit": "kW",
                        },
                        {
                            "value": "0.000",
                            "context": "Sample.Periodic",
                            "measurand": "Power.Active.Import",
                            "location": "Outlet",
                            "unit": "W",
                            "phase": "L1",
                        },
                        {
                            "value": "0.000",
                            "context": "Sample.Periodic",
                            "measurand": "Power.Active.Import",
                            "location": "Outlet",
                            "unit": "W",
                            "phase": "L2",
                        },
                        {
                            "value": "0.000",
                            "context": "Sample.Periodic",
                            "measurand": "Power.Active.Import",
                            "location": "Outlet",
                            "unit": "W",
                            "phase": "L3",
                        },
                        {
                            "value": "0.000",
                            "context": "Sample.Periodic",
                            "measurand": "Power.Factor",
                            "location": "Outlet",
                        },
                        {
                            "value": "38.500",
                            "context": "Sample.Periodic",
                            "measurand": "Temperature",
                            "location": "Body",
                            "unit": "Celsius",
                        },
                        {
                            "value": "228.000",
                            "context": "Sample.Periodic",
                            "measurand": "Voltage",
                            "location": "Outlet",
                            "unit": "V",
                            "phase": "L1-N",
                        },
                        {
                            "value": "228.000",
                            "context": "Sample.Periodic",
                            "measurand": "Voltage",
                            "location": "Outlet",
                            "unit": "V",
                            "phase": "L2-N",
                        },
                        {
                            "value": "0.000",
                            "context": "Sample.Periodic",
                            "measurand": "Voltage",
                            "location": "Outlet",
                            "unit": "V",
                            "phase": "L3-N",
                        },
                        {
                            "value": "89.00",
                            "context": "Sample.Periodic",
                            "measurand": "Power.Reactive.Import",
                            "unit": "W",
                        },
                        {
                            "value": "0.010",
                            "context": "Transaction.Begin",
                            "unit": "kWh",
                        },
                        {
                            "value": "1305570.000",
                        },
                    ],
                }
            ],
        )
        resp = await self.call(request)
        assert resp is not None

    async def send_meter_line_voltage(self):
        """Send line voltages."""
        while self.active_transactionId == 0:
            await asyncio.sleep(1)
        request = call.MeterValues(
            connector_id=1,
            transaction_id=self.active_transactionId,
            meter_value=[
                {
                    "timestamp": "2021-06-21T16:15:09Z",
                    "sampledValue": [
                        {
                            "value": "395.900",
                            "context": "Sample.Periodic",
                            "measurand": "Voltage",
                            "location": "Outlet",
                            "unit": "V",
                            "phase": "L1-L2",
                        },
                        {
                            "value": "396.300",
                            "context": "Sample.Periodic",
                            "measurand": "Voltage",
                            "location": "Outlet",
                            "unit": "V",
                            "phase": "L2-L3",
                        },
                        {
                            "value": "398.900",
                            "context": "Sample.Periodic",
                            "measurand": "Voltage",
                            "location": "Outlet",
                            "unit": "V",
                            "phase": "L3-L1",
                        },
                    ],
                }
            ],
        )
        resp = await self.call(request)
        assert resp is not None

    async def send_meter_err_phases(self):
        """Send erroneous voltage phase."""
        while self.active_transactionId == 0:
            await asyncio.sleep(1)
        request = call.MeterValues(
            connector_id=1,
            transaction_id=self.active_transactionId,
            meter_value=[
                {
                    "timestamp": "2021-06-21T16:15:09Z",
                    "sampledValue": [
                        {
                            "value": "230",
                            "context": "Sample.Periodic",
                            "measurand": "Voltage",
                            "location": "Outlet",
                            "unit": "V",
                            "phase": "L1",
                        },
                        {
                            "value": "23",
                            "context": "Sample.Periodic",
                            "measurand": "Current.Import",
                            "location": "Outlet",
                            "unit": "A",
                            "phase": "L1-N",
                        },
                    ],
                }
            ],
        )
        resp = await self.call(request)
        assert resp is not None

    async def send_meter_energy_kwh(self):
        """Send periodic energy meter value with kWh unit."""
        while self.active_transactionId == 0:
            await asyncio.sleep(1)
        request = call.MeterValues(
            connector_id=1,
            transaction_id=self.active_transactionId,
            meter_value=[
                {
                    "timestamp": "2021-06-21T16:15:09Z",
                    "sampledValue": [
                        {
                            "unit": "kWh",
                            "value": "11",
                            "context": "Sample.Periodic",
                            "format": "Raw",
                            "measurand": "Energy.Active.Import.Register",
                        },
                    ],
                }
            ],
        )
        resp = await self.call(request)
        assert resp is not None

    async def send_main_meter_clock_data(self):
        """Send periodic main meter value. Main meter values dont have transaction_id."""
        while self.active_transactionId == 0:
            await asyncio.sleep(1)
        request = call.MeterValues(
            connector_id=1,
            meter_value=[
                {
                    "timestamp": "2021-06-21T16:15:09Z",
                    "sampledValue": [
                        {
                            "value": "67230012",
                            "context": "Sample.Clock",
                            "format": "Raw",
                            "measurand": "Energy.Active.Import.Register",
                            "location": "Inlet",
                        },
                    ],
                }
            ],
        )
        resp = await self.call(request)
        assert resp is not None

    async def send_meter_clock_data(self):
        """Send periodic meter data notification."""
        self.active_transactionId = 0
        request = call.MeterValues(
            connector_id=1,
            transaction_id=self.active_transactionId,
            meter_value=[
                {
                    "timestamp": "2021-06-21T16:15:09Z",
                    "sampledValue": [
                        {
                            "measurand": "Voltage",
                            "context": "Sample.Clock",
                            "unit": "V",
                            "value": "228.490",
                        },
                        {
                            "measurand": "Power.Active.Import",
                            "context": "Sample.Clock",
                            "unit": "W",
                            "value": "0.000",
                        },
                        {
                            "measurand": "Energy.Active.Import.Register",
                            "context": "Sample.Clock",
                            "unit": "kWh",
                            "value": "1101.452",
                        },
                        {
                            "measurand": "Current.Import",
                            "context": "Sample.Clock",
                            "unit": "A",
                            "value": "0.054",
                        },
                        {
                            "measurand": "Frequency",
                            "context": "Sample.Clock",
                            "value": "50.000",
                        },
                    ],
                },
            ],
        )
        resp = await self.call(request)
        assert resp is not None

    async def send_stop_transaction(self, delay: int = 0):
        """Send a stop transaction notification."""
        # add delay to allow meter data to be processed
        await asyncio.sleep(delay)
        n = 0
        while self.active_transactionId == 0 and n < 2:
            await asyncio.sleep(1)
            n += 1
        request = call.StopTransaction(
            meter_stop=54321,
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
            transaction_id=self.active_transactionId,
            reason="EVDisconnected",
            id_tag="test_cp",
        )
        resp = await self.call(request)
        assert resp.id_tag_info["status"] == AuthorizationStatus.accepted.value

    async def send_security_event(self):
        """Send a security event notification."""
        request = call.SecurityEventNotification(
            type="SettingSystemTime",
            timestamp="2022-09-29T20:58:29Z",
            tech_info="BootNotification",
        )
        await self.call(request)
