# Release Notes

## v0.5.28 - Stable v0.5.25 runtime with focused fixes

Supersedes `v0.5.26` and `v0.5.27` by returning to the proven `v0.5.25` runtime behavior and applying only narrowly scoped compatibility and metering fixes.

### Changed

* Restores the established one-CentralSystem-and-port-per-wallbox runtime from `v0.5.25` and removes the later lifecycle, service-routing, and multi-wallbox refactors.
* Clearly labels the configured price source as an energy-price sensor and accepts only `EUR/kWh`, `ct/kWh`, or `EUR/MWh` units.
* Keeps WebSockets `15.0.1` compatibility required by current Home Assistant installations without changing the established OCPP server lifecycle.
* Keeps repository-level HACS validation disabled while retaining HACS ZIP release support.

### Fixed

* Uses cumulative `Energy.Active.Import.Interval` values as the active session-energy fallback when a charger does not send `Energy.Active.Import.Register`.
* Rejects `EUR/h` running-cost sensors in the options flow instead of silently producing incorrect or missing monthly costs.
* Restores German charger status translations with Hassfest-compatible enum keys.
* Keeps the manifest, test dependencies, and GitHub Actions compatible with the current validation environment.

## v0.5.27 - Home Assistant and CI compatibility

Updates the WebSocket runtime used by current Home Assistant installations and makes the repository validation pipeline reproducible again.

### Changed

* Updates the pinned WebSockets runtime to `15.0.1` while keeping the established OCPP server behavior on its compatibility API.
* Updates the GitHub checkout and Python setup actions to their Node.js 24 compatible versions.
* Runs the full linting workflow once for maintained branch changes instead of duplicating it for the following release tag.
* Keeps the existing OCPP status values unchanged while removing invalid state-translation tables that could not pass current Home Assistant validation.

### Fixed

* Restores clean Home Assistant test startup by constraining `josepy` to the version range supported by the pinned Home Assistant dependency chain.
* Repairs the isolated Bandit pre-commit environment by updating the hook and declaring its missing `pbr` dependency.
* Sorts the integration manifest according to current Hassfest requirements and removes obsolete direct repository URLs from configuration translations.
* Updates WebSockets test doubles and closed-connection simulation for the current client and server API.

## v0.5.26 - Reliable multi-wallbox lifecycle

Hardens transaction ordering, persisted controls, service routing, and the release pipeline while preserving the existing one-CentralSystem-and-port-per-wallbox setup.

### Added

* Routes charger services to an optional `cpid`, while existing single-wallbox service calls continue to work without changes.
* Shows monthly energy that could not be priced as `unpriced_energy_kwh` instead of silently presenting it as zero cost.
* Adds regression coverage for delayed transaction messages, isolated state migration, invalid price units, and invalid-session cleanup.

### Changed

* Stores charger control state per existing config entry and transparently imports that wallbox's values from the previous shared store on first load.
* Reapplies persisted standard or price-optimized charging intent after connect and reconnect.
* Registers Home Assistant charger services once at integration level instead of redefining them for every connection.
* Pins the OCPP runtime dependencies and updates the stable Home Assistant test package so clean CI installs remain reproducible.
* Runs CI for the maintained branch and release tags and accepts normal semantic version tags in release-note backfilling.
* Applies the repository's existing Black and isort rules to the Python source and tests.

### Fixed

* Prevents a delayed `StopTransaction` from clearing a newer active transaction.
* Handles delayed transaction meter values without overwriting the absolute wallbox meter or a newer session.
* Persists EVSE-suspend auto-stop settings even while a wallbox is disconnected.
* Serializes post-connect setup and reconciles charging profiles after reconnects.
* Normalizes supported `ct/kWh`, `EUR/kWh`, and `EUR/MWh` price units and rejects missing or ambiguous units.
* Prevents shared entity descriptions and dispatcher listeners from leaking state across config entries or reloads.
* Rejects duplicate host/port listeners, preserves user-selected monitored variables, retries unavailable ports, and validates YAML authorization lists correctly.
* Stops invalid firmware or diagnostics URLs and read-only configuration writes before an OCPP request is sent.
* Makes the long OCPP integration test deterministic by sending order-sensitive messages in their intended sequence.

## v0.5.25 - User cost sensors and meter cleanup

Adds dynamic charging cost sensors for managed users and tightens meter handling after transactions stop.

### Added

* Adds price-based cost sensors for managed users so Home Assistant can show per-user charging costs from the configured energy price.

### Fixed

* Keeps transaction-specific meter values from reviving a transaction that was already stopped.
* Clears stale live current and power values when `MeterValues` arrive for a stopped transaction.
* Preserves session energy from chargers that report session energy directly before resetting the transaction bookkeeping.

## v0.5.24 - Persist charging controls

Keeps wallbox charging controls and selected users stable across Home Assistant restarts and integration updates.

### Fixed

* Persists the selected `Lademodus` per wallbox, including `Strompreis optimiert`.
* Persists the price optimized charging pause/allow switch state per wallbox.
* Persists the selected managed start user per wallbox.
* Restores active transaction metadata and the currently logged-in user from the persisted user session when a wallbox reconnects.

## v0.5.23 - Restore active sessions safely

Fixes restart and cleanup handling for price optimized charging sessions.

### Fixed

* Restores active transaction context after Home Assistant restarts without using the current meter register as a new session start.
* Restores the active managed user and idTag from the persisted user session when meter values recover an active transaction.
* Keeps `Pausiert aufgrund strompreisoptimiertem Laden` limited to active transactions, so idle wallboxes can show `Bereit`.
* Clears active session duration and session energy after a successful `StopTransaction` while still booking the completed session to monthly/user counters.
* Allows active session sensors to clear restored numeric values instead of showing stale session data after the backend state is reset.

## v0.5.22 - Preserve price mode session counters

Keeps active charging session counters stable while switching price optimized charging controls.

### Fixed

* Preserves active session metrics such as `Id Tag`, `Current User`, `Transaction.Id`, meter start, session energy, and session time across price pause and resume profile changes.
* Prevents `Energy.Session` from decreasing during the same active transaction if a wallbox reports lower meter values while pausing or resuming.
* Keeps the price optimized pause switch state unchanged when the underlying OCPP pause or resume command fails.

## v0.5.21 - Price pause auto-stop guard

Fixes price optimized pause handling so switching back to `Standard` can resume the active charging transaction.

### Fixed

* Prevents the EVSE-suspend auto-stop logic from sending `RemoteStopTransaction` while charging is paused by price optimization.
* Cancels a pending EVSE auto-stop as soon as the price pause profile is applied.
* Keeps the charge mode in `Strompreis optimiert` if clearing the pause profile fails, so Home Assistant does not show `Standard` before charging has actually resumed.

## v0.5.20 - Price optimized charging

Adds Home Assistant controls for price optimized charging sessions without ending the active OCPP transaction.

### Added

* Adds a `Charge mode` select with `Standard` and `Strompreis optimiert`.
* Adds a price optimized charging switch for automations to allow or pause charging during an active transaction.
* Pauses charging through a dedicated zero-limit charging profile and resumes by clearing only that pause profile.

### Changed

* Shows price optimized pauses in the charger status as `Pausiert aufgrund strompreisoptimiertem Laden` in German.
* Applies a pending price pause immediately when a transaction starts in price optimized mode.

## v0.5.19 - Live user energy counters

Keeps managed-user energy counters moving while a charging transaction is still active.

### Changed

* Adds live delta booking for managed-user total and monthly energy counters during `MeterValues`.
* Stores the already credited session energy so repeated meter updates and Home Assistant restarts do not double-count existing values.
* Keeps `StopTransaction` compatible by only adding any remaining uncredited session energy and still recording the final session summary.

## v0.5.18 - Stale preparing recovery

Recovers wallboxes that get stuck in `Preparing` or `SuspendedEV` without ever starting a transaction.

### Fixed

* Starts the pending-start cleanup directly from stale connector status notifications, so recovery still runs after Home Assistant restarts or repeated `Preparing` updates.
* Clears pending `Id Tag` and `Current User` attribution when a stale start is cleaned up.
* Requests connector unlock and a fresh status notification, then sends a soft reset if the wallbox still reports `Preparing` or `SuspendedEV` without an active transaction.

## v0.5.17 - Persistent current user restore

Keeps managed-user attribution visible after Home Assistant restarts while a charging transaction is still active.

### Fixed

* Restores the wallbox `Current User` entity from the persisted active user session when `MeterValues` recover the active `Transaction.Id`.
* Restores the matching wallbox `Id Tag` value from the persisted transaction session instead of leaving the active user empty after restart.
* Keeps the charge-control switch off while a remote start is only waiting in `Preparing` or `SuspendedEV` and no transaction has started yet.
* Cleans up accepted remote starts that never become a transaction by unlocking the connector and requesting a fresh status notification after the pending-start timeout.
* Allows the `Id Tag` sensor to clear its restored state instead of showing a stale idTag after the backend value was reset.

## v0.5.16 - User remote-start controls and idTag cleanup

Adds managed-user remote-start controls for Home Assistant and clears stale idTag state after ended or abandoned charging attempts.

### Added

* Adds per-user remote-start buttons for active managed OCPP users with idTags.
* Adds a managed-user select entity plus a separate start button that only starts charging after a user is selected.
* Sends the selected user's idTag with `RemoteStartTransaction` while keeping the existing charge-control switch behavior unchanged.

### Fixed

* Clears the wallbox `Id Tag` entity when a transaction stops.
* Clears the wallbox `Id Tag` entity when a tag was authorized but no transaction started and the connector returns to `Available`.

## v0.5.15 - HACS sortable counter release

Republishes the user and wallbox counter release with a three-part version number so HACS sorts it above `v0.5.6.9`.

### Fixed

* Uses a HACS-friendly version sequence after `v0.5.6.9`, avoiding four-part versions such as `v0.5.6.11` being listed below `v0.5.6.9`.
* Keeps release ZIP manifests on plain semantic versions without a leading `v`.

## v0.5.6.11 - HACS release version fix

Fixes the HACS release package generated for the user and wallbox counter release.

### Fixed

* Publishes release ZIP manifests with a plain semantic version such as `0.5.6.11` instead of a tag-style version such as `v0.5.6.10`.

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
