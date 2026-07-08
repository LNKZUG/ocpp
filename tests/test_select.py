"""Test OCPP select entities."""

from custom_components.ocpp.api import PRICE_OPTIMIZED_CHARGE_MODES
from custom_components.ocpp.select import ChargeModeSelect, charge_start_user_options


class RegistryStub:
    """Minimal user registry test double."""

    def list_users(self):
        """Return managed users."""
        return [
            {
                "user_id": "lukas",
                "name": "Lukas",
                "id_tags": ["ABC"],
                "active": True,
            },
            {
                "user_id": "lukas_2",
                "name": "Lukas",
                "id_tags": ["DEF"],
                "active": True,
            },
            {
                "user_id": "inactive",
                "name": "Inactive",
                "id_tags": ["GHI"],
                "active": False,
            },
            {
                "user_id": "missing_tag",
                "name": "Missing Tag",
                "id_tags": [],
                "active": True,
            },
        ]


def test_charge_start_user_options_are_active_and_unique():
    """Test charge start select options use active users with idTags."""
    options = charge_start_user_options(RegistryStub())

    assert list(options) == ["Lukas (lukas)", "Lukas (lukas_2)"]
    assert options["Lukas (lukas)"]["user_id"] == "lukas"
    assert options["Lukas (lukas_2)"]["user_id"] == "lukas_2"


def test_charge_mode_select_options():
    """Test charge mode select exposes the supported control modes."""

    class CentralSystemStub:
        """Minimal central system test double."""

        id = "central"

        def get_available(self, cp_id):
            """Return charger availability."""
            return True

        def get_charge_mode(self, cp_id):
            """Return selected charge mode."""
            return PRICE_OPTIMIZED_CHARGE_MODES[0]

    entity = ChargeModeSelect(None, CentralSystemStub(), "charger")

    assert entity.options == PRICE_OPTIMIZED_CHARGE_MODES
    assert entity.current_option == "Standard"
