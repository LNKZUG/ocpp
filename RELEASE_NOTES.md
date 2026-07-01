# Release Notes

## v0.5.6.5 - Release notes automation

Ensures GitHub/HACS releases include the matching section from `RELEASE_NOTES.md` instead of showing `None` in Home Assistant.

## v0.5.6.4 - User actions

Replaces the experimental options-flow user UI with Home Assistant actions:

* `ocpp.add_user`
* `ocpp.update_user`
* `ocpp.set_user_active`

This avoids the broken Configure dialog on Home Assistant versions where the old options flow fails with a 500 error.

## v0.5.6.3 - Sensor translations

Adds native Home Assistant translation keys for OCPP sensors and German enum translations for charger status values such as `Charging`, `Available`, `Preparing`, `SuspendedEV` and `SuspendedEVSE`.

## v0.5.6.2 - OCPP user management

Adds integration-native user management on top of the known working Entratek `v0.5.6` build.

### Added

* Persistent OCPP user registry stored through Home Assistant storage.
* Options-flow UI to add, edit, activate and deactivate users.
* Mapping from one or more OCPP `idTag` values to a named user.
* Managed-user authorization before the legacy YAML authorization fallback.
* One total kWh energy sensor per managed user across all configured chargers.

### Notes

Existing helpers and manually created counters are left untouched. Cost counters are intentionally not included in this version.

## v0.5.6.1 - Known working Entratek production release

This release is the conservative, known working LNKZUG build for Entratek Power Dot Pro 2 wallboxes.

It is based on upstream `lbbrhzn/ocpp` `v0.5.6` and replaces the integration component files with the exact files exported from the working Home Assistant production system.

### Intended use

Use this release when the newer `v0.10.15.x` compatibility branch does not behave correctly and the priority is to restore the proven production behavior.

### Compatibility behavior

The production files include:

* Entratek Power Dot Pro 2 compatibility for OCPP 1.6 JSON.
* OCPP central system URL support in the proven form `ws://<home-assistant-ip>:9000`.
* Home Assistant warning notifications suppressed to log output.
* Charging profile unit handling forced to amps.
* Idle sampling default set to 60 seconds.
* Home Assistant `ConfigType` import compatibility.

### Installation

Install this repository through HACS as a custom integration and select release `v0.5.6.1`.
