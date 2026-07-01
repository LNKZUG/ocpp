[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)

![OCPP](https://github.com/home-assistant/brands/raw/master/custom_integrations/ocpp/icon.png)

# LNKZUG OCPP - known working Entratek release

This branch contains the known working Home Assistant OCPP integration used with Entratek Power Dot Pro 2 wallboxes in the LNKZUG setup.

It is intentionally based on the older upstream `v0.5.6` integration, plus the exact component files exported from the working production Home Assistant instance.

## Target charger

This release is intended for:

* Entratek Power Dot Pro 2
* OCPP 1.6 JSON mode
* Home Assistant custom integration installed through HACS

## Why this branch exists

Newer upstream OCPP versions changed the connection and setup flow. Those newer builds are useful long term, but the Entratek charger currently behaves correctly with the older `v0.5.6`-based integration.

This branch is therefore a conservative recovery release: install it when you need the known working behavior rather than the latest upstream code.

## Expected OCPP URL

The Entratek charger can be configured with the central system URL without a charge point id path:

```text
ws://<home-assistant-ip>:9000
```

Example:

```text
ws://192.168.248.150:9000
```

## Included compatibility changes

Compared with upstream `v0.5.6`, the production files include these practical adjustments:

* Home Assistant persistent notifications from charger warnings are suppressed and written to the log instead.
* OCPP 1.6 charging profiles assume current in amps instead of querying `ChargingScheduleAllowedChargingRateUnit`.
* The idle sampling interval default is 60 seconds.
* Home Assistant `ConfigType` typing is used for compatibility with newer Home Assistant versions.

## HACS installation

Add this repository as a custom HACS integration repository:

```text
https://github.com/LNKZUG/ocpp
```

Install release `v0.5.6.1` and restart Home Assistant.
