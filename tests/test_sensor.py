"""Tests for OCPP sensors."""

from custom_components.ocpp.sensor import metric_display_name


def test_metric_display_name():
    """Test generated fallback names for sensor metrics."""
    assert metric_display_name("Energy.Active.Import.Register") == (
        "Energy Active Import Register"
    )
    assert metric_display_name("SoC") == "SoC"
    assert metric_display_name("RPM") == "RPM"
    assert metric_display_name("Transaction.Id") == "Transaction ID"
