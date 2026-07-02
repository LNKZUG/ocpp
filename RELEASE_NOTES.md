# Release Notes

## v0.5.6.10 - User and wallbox monthly counters

Improves user attribution and monthly energy tracking for managed OCPP users and wallboxes.

### Added

* Adds one monthly kWh counter per managed OCPP user.
* Adds a wallbox sensor for the currently mapped managed user during active charging.
* Adds one monthly kWh counter per wallbox.

### Changed

* Moves auto-stop switch and delay entities into the Home Assistant configuration category.

## v0.5.6.9 - User management and Entratek sensor cleanup

Improves the known working Entratek/DUOSIDA release train with a cleaner Home Assistant UI and more complete user-management options.

### Added

* Adds an options-flow path for deleting managed OCPP users.
* Adds missing German and English labels for buttons, number entities, switches, user-management actions and additional OCPP sensors.
* Adds German umlauts to German OCPP translations.

### Fixed

* Fixes options-flow navigation so user-management screens can return to the correct menu after editing or adding users.
* Keeps fallback entity names while still using Home Assistant translation keys.

### Changed

* Disables irrelevant or unsupported Entratek/DUOSIDA metrics by default so they are hidden unless explicitly enabled.
* Leaves useful live metrics such as import current, offered current, import power, import energy, voltage and temperature enabled by default.

## v0.5.6.8 - Home Assistant 2026 compatibility fixes

Fixes issues found after testing `v0.5.6.7` on the latest Home Assistant release.

### Fixed

* Fixes the `Benutzer` configure dialog failing with `property 'config_entry' of 'OptionsFlowHandler' object has no setter`.
* Adds separate user detail sensors for status, idTags, last session energy and last session finish time instead of exposing the full user list as one long sensor attribute.
* Fixes the charger device `via_device` reference so Home Assistant no longer warns about a missing central system device.
* Handles DUOSIDA/Entratek `StatusNotification.info` values that exceed the OCPP 1.6 maximum length by trimming the optional info field before validation.

## v0.5.6.7 - Duosida transaction recovery and user registry UI

Improves the known working `v0.5.6.x` train for DUOSIDA/Entratek chargers and moves user management fully into Home Assistant integration UI.

### Added

* Configurable automatic `RemoteStopTransaction` recovery when a charger remains in `SuspendedEVSE` or reports `MeterValues` with `Interruption.Begin` after the EV-side charge stop.
* New `Auto Stop On EVSE Suspended` switch and `Auto Stop Delay` number entity for tuning the recovery behavior from Home Assistant.
* Separate `Benutzer` integration entry for global OCPP users instead of showing users under one central system device.
* `OCPP Benutzer` overview sensor with all managed users, idTags, active/blocked status, and energy metadata.
* Options-flow user management for adding, editing, activating, and deactivating OCPP users.
* Options-flow setting for the default authorization status of unknown idTags, replacing the need for `default_authorization_status` in `configuration.yaml`.

### Fixed

* Prevents DUOSIDA chargers from staying indefinitely in `Wait` after the vehicle stops charging and the plug is removed.
* Keeps charger status truthful by sending OCPP `RemoteStopTransaction` instead of faking `Available` in Home Assistant.
* Adds readable fallback names for OCPP sensors so entities no longer all appear as `charger` when translations are not resolved.
* Handles chargers that time out on `SupportedFeatureProfiles` by defaulting to the OCPP Core profile instead of dropping the websocket connection.

## v0.5.6.6 - Release notes backfill

Backfills GitHub/HACS release notes for all `v0.5.6.x` releases from `RELEASE_NOTES.md` whenever a new release is published.

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
