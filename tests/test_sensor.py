"""Tests for OCPP sensors."""

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from custom_components.ocpp.api import PRICE_OPTIMIZED_CHARGE_STATUS
from custom_components.ocpp.const import DEFAULT_DISABLED_METRICS
from custom_components.ocpp.enums import HAChargerSession, HAChargerStatuses
from custom_components.ocpp.sensor import (
    ChargePointMetric,
    OcppSensorDescription,
    UserIdTagsSensor,
    UserLastSessionEnergySensor,
    UserLastSessionFinishedSensor,
    UserMonthlyEnergySensor,
    UserStatusSensor,
    metric_display_name,
    metric_translation_key,
    user_entities,
)


class CentralSystemStub:
    """Minimal central system test double."""

    id = "central"

    def __init__(self, metrics=None):
        """Initialize metric values."""
        self.metrics = metrics or {}

    def get_metric(self, cp_id, metric):
        """Return a metric value."""
        return self.metrics.get(metric)

    def is_price_optimized_charging_paused(self, cp_id):
        """Return price-optimized pause state."""
        return False


class RegistryStub:
    """Minimal user registry test double."""

    def __init__(self):
        """Initialize registry test data."""
        self.user = {
            "user_id": "lukas",
            "name": "Lukas",
            "id_tags": ["02BE5E0E"],
            "active": True,
            "energy_kwh": 12.5,
            "monthly_energy_kwh": 3.5,
            "monthly_energy_period": "2026-07",
            "last_session_energy_kwh": 1.25,
            "last_session_finished_at": 1000,
        }

    def get_user(self, user_id):
        """Return a test user."""
        if user_id == self.user["user_id"]:
            return self.user
        return None

    @staticmethod
    def current_month_period():
        """Return the test period."""
        return "2026-07"


def test_metric_display_name():
    """Test generated fallback names for sensor metrics."""
    assert metric_display_name("Energy.Active.Import.Register") == (
        "Energy Active Import Register"
    )
    assert metric_display_name("SoC") == "SoC"
    assert metric_display_name("RPM") == "RPM"
    assert metric_display_name("Transaction.Id") == "Transaction ID"
    assert metric_display_name("Current.User") == "Current User"
    assert metric_display_name("Energy.Month") == "Energy Month"


def test_unsupported_metrics_are_disabled_by_default():
    """Test noisy charge point metrics are hidden until explicitly enabled."""
    assert "SoC" in DEFAULT_DISABLED_METRICS
    assert "Power.Offered" in DEFAULT_DISABLED_METRICS
    assert "Heartbeat" in DEFAULT_DISABLED_METRICS
    assert "Temperature" not in DEFAULT_DISABLED_METRICS

    disabled_metric = OcppSensorDescription(
        key="soc",
        name="SoC",
        metric="SoC",
        translation_key=metric_translation_key("SoC"),
        enabled_default=False,
    )
    active_metric = OcppSensorDescription(
        key="temperature",
        name="Temperature",
        metric="Temperature",
        translation_key=metric_translation_key("Temperature"),
        enabled_default=True,
    )

    disabled_entity = ChargePointMetric(
        None, CentralSystemStub(), "charger", disabled_metric
    )
    active_entity = ChargePointMetric(
        None, CentralSystemStub(), "charger", active_metric
    )

    assert disabled_entity._attr_entity_registry_enabled_default is False
    assert active_entity._attr_entity_registry_enabled_default is True


def test_wallbox_current_user_and_monthly_energy_sensor_classes():
    """Test wallbox user and monthly energy metrics use the correct classes."""
    current_user = ChargePointMetric(
        None,
        CentralSystemStub(),
        "charger",
        OcppSensorDescription(
            key="current_user",
            name="Current User",
            metric=HAChargerSession.current_user.value,
            translation_key="current_user",
        ),
    )
    monthly_energy = ChargePointMetric(
        None,
        CentralSystemStub(),
        "charger",
        OcppSensorDescription(
            key="energy_month",
            name="Energy Month",
            metric=HAChargerSession.monthly_energy.value,
            translation_key="energy_month",
        ),
    )

    assert current_user.device_class is None
    assert current_user.state_class is None
    assert monthly_energy.device_class == SensorDeviceClass.ENERGY
    assert monthly_energy.state_class == SensorStateClass.TOTAL


def test_id_tag_sensor_can_clear_restored_value():
    """Test Id Tag sensor returns None instead of stale restored state."""
    entity = ChargePointMetric(
        None,
        CentralSystemStub({HAChargerStatuses.id_tag.value: None}),
        "charger",
        OcppSensorDescription(
            key="id_tag",
            name="Id Tag",
            metric=HAChargerStatuses.id_tag.value,
            translation_key="id_tag",
        ),
    )
    entity._attr_native_value = "02BE5E0E"

    assert entity.native_value is None
    assert entity._attr_native_value is None


def test_status_sensor_reflects_price_optimized_pause():
    """Test charger status shows price optimization pauses."""

    class PausedCentralSystemStub(CentralSystemStub):
        """Central system test double with active price pause."""

        def is_price_optimized_charging_paused(self, cp_id):
            """Return price-optimized pause state."""
            return True

    entity = ChargePointMetric(
        None,
        PausedCentralSystemStub({HAChargerStatuses.status_connector.value: "Charging"}),
        "charger",
        OcppSensorDescription(
            key="status_connector",
            name="Status Connector",
            metric=HAChargerStatuses.status_connector.value,
            translation_key="status_connector",
        ),
    )

    assert PRICE_OPTIMIZED_CHARGE_STATUS in entity.options
    assert entity.native_value == PRICE_OPTIMIZED_CHARGE_STATUS


def test_user_entities_expose_user_details():
    """Test managed users expose details as separate entities."""
    entities = user_entities(None, RegistryStub(), "lukas")

    assert any(isinstance(entity, UserStatusSensor) for entity in entities)
    assert any(isinstance(entity, UserIdTagsSensor) for entity in entities)
    assert any(isinstance(entity, UserMonthlyEnergySensor) for entity in entities)
    assert any(isinstance(entity, UserLastSessionEnergySensor) for entity in entities)
    assert any(isinstance(entity, UserLastSessionFinishedSensor) for entity in entities)

    values = {entity._attr_unique_id: entity.native_value for entity in entities}
    assert values["ocpp_user_lukas_energy"] == 12.5
    assert values["ocpp_user_lukas_monthly_energy"] == 3.5
    assert values["ocpp_user_lukas_status"] == "Accepted"
    assert values["ocpp_user_lukas_id_tags"] == "02BE5E0E"
    assert values["ocpp_user_lukas_last_session_energy"] == 1.25
    assert isinstance(values["ocpp_user_lukas_last_session_finished"], datetime)


def test_user_monthly_energy_sensor_resets_stale_period():
    """Test monthly user energy displays zero for an old period."""
    registry = RegistryStub()
    registry.user["monthly_energy_period"] = "2026-06"

    entity = UserMonthlyEnergySensor(None, registry, "lukas")

    assert entity.native_value == 0.0
    assert entity.extra_state_attributes == {
        "period": "2026-07",
        "reset_cycle": "monthly",
    }
