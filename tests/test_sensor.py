"""Tests for OCPP sensors."""

from datetime import datetime

from custom_components.ocpp.sensor import (
    UserIdTagsSensor,
    UserLastSessionEnergySensor,
    UserLastSessionFinishedSensor,
    UserStatusSensor,
    metric_display_name,
    user_entities,
)


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
            "last_session_energy_kwh": 1.25,
            "last_session_finished_at": 1000,
        }

    def get_user(self, user_id):
        """Return a test user."""
        if user_id == self.user["user_id"]:
            return self.user
        return None


def test_metric_display_name():
    """Test generated fallback names for sensor metrics."""
    assert metric_display_name("Energy.Active.Import.Register") == (
        "Energy Active Import Register"
    )
    assert metric_display_name("SoC") == "SoC"
    assert metric_display_name("RPM") == "RPM"
    assert metric_display_name("Transaction.Id") == "Transaction ID"


def test_user_entities_expose_user_details():
    """Test managed users expose details as separate entities."""
    entities = user_entities(None, RegistryStub(), "lukas")

    assert any(isinstance(entity, UserStatusSensor) for entity in entities)
    assert any(isinstance(entity, UserIdTagsSensor) for entity in entities)
    assert any(isinstance(entity, UserLastSessionEnergySensor) for entity in entities)
    assert any(isinstance(entity, UserLastSessionFinishedSensor) for entity in entities)

    values = {entity._attr_unique_id: entity.native_value for entity in entities}
    assert values["ocpp_user_lukas_status"] == "Accepted"
    assert values["ocpp_user_lukas_id_tags"] == "02BE5E0E"
    assert values["ocpp_user_lukas_last_session_energy"] == 1.25
    assert isinstance(values["ocpp_user_lukas_last_session_finished"], datetime)
