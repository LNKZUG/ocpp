# Release Notes

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
