# Release Notes

## v0.10.15.5 - LNKZUG Entratek compatibility release

This release is based on upstream `lbbrhzn/ocpp` `v0.10.15` and adds the compatibility behavior required for Entratek Power Dot Pro 2 wallboxes in the LNKZUG Home Assistant installation.

### Why this release exists

The Entratek Power Dot Pro 2 installation was known to work with an older OCPP integration based on `v0.5.6`. Moving to newer upstream versions brought useful Home Assistant and OCPP updates, but also changed assumptions around charger discovery, connection path handling, status triggers, notifications, and charging-profile capability detection.

This release keeps the newer upstream base while restoring the behavior required by the Entratek charger.

### Compatibility changes

* Empty OCPP URL path compatibility:
  Entratek setups can keep using a central system URL like `ws://192.168.248.150:9000` without appending a charge point id. The integration maps an empty WebSocket path to the configured charger entry, matching the behavior of the older working integration.

* Home Assistant notification noise reduction:
  Charger warnings are written to the log instead of creating Home Assistant persistent notifications. The Entratek charger can produce noisy warnings for unsupported or unusual OCPP behavior, and surfacing all of them in the UI is not useful.

* Status trigger compatibility:
  The automatic `TriggerMessage(StatusNotification)` call during post-connect setup is skipped. This trigger is optional and can cause issues with this charger. If a status trigger is called manually and the charger returns no usable response, it is logged at debug level instead of warning level.

* Charging-profile unit compatibility:
  OCPP 1.6 charging profiles are sent in amps. The Entratek charger does not reliably answer `ChargingScheduleAllowedChargingRateUnit`, so the fork avoids that capability query and assumes `Current`.

* Faster idle sampling:
  The default idle sampling interval is set to 60 seconds. This matches the production behavior needed for timely Home Assistant values.

### Installation note

Install this fork through HACS as a custom integration repository:

```text
https://github.com/LNKZUG/ocpp
```

Use release `v0.10.15.5` or newer and restart Home Assistant after installation.
