[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![hacs_downloads](https://img.shields.io/github/downloads/LNKZUG/ocpp/latest/total)](https://github.com/LNKZUG/ocpp/releases/latest)

![OCPP](https://github.com/home-assistant/brands/raw/master/custom_integrations/ocpp/icon.png)

This is the LNKZUG fork of the Home Assistant OCPP integration with Entratek Power Dot Pro 2 compatibility patches.

It supports Electric Vehicle chargers that use Open Charge Point Protocols 1.6j, 2.0.1 and 2.1 (experimental).

* based on the [Python OCPP Package](https://github.com/mobilityhouse/ocpp).
* based on the upstream [lbbrhzn/ocpp](https://github.com/lbbrhzn/ocpp) integration.
* HACS compliant custom repository.

## Purpose of this fork

This fork keeps the current upstream OCPP integration usable with Entratek Power Dot Pro 2 wallboxes.

The production setup that proved stable for these chargers was based on an older `v0.5.6` version of the integration. Newer upstream versions changed parts of the connection and charger setup flow, while the Entratek charger still behaves like the older setup expects in a few places. This fork applies only the compatibility changes needed for that charger while staying based on the newer upstream integration.

## Target charger

The compatibility patches are intended for:

* Entratek Power Dot Pro 2
* OCPP 1.6 JSON mode
* Home Assistant custom integration installed via HACS

Other chargers may still work, but this fork is maintained specifically for the Entratek behavior observed in the LNKZUG installation.

## OCPP URL

The Entratek Power Dot Pro 2 was proven to work with a central system URL without a charge point path:

```text
ws://<home-assistant-ip>:9000
```

Example:

```text
ws://192.168.248.150:9000
```

Current upstream versions usually use the URL path as the technical charge point id. This fork restores compatibility with the older behavior by mapping an empty path to the configured charger entry.

## Compatibility changes

The fork contains these Entratek-specific adjustments:

* Accept OCPP WebSocket connections without a charge point id in the URL path.
* Do not show charger warnings as Home Assistant persistent notifications; log them instead.
* Skip the fragile automatic `TriggerMessage(StatusNotification)` call during post-connect setup.
* Treat failed manual `TriggerMessage(StatusNotification)` responses as debug-level compatibility noise.
* Force OCPP 1.6 charging profiles to use current in amps because the Entratek charger does not reliably answer `ChargingScheduleAllowedChargingRateUnit`.
* Use a 60 second default idle sampling interval so charger values update often enough in Home Assistant.

## HACS installation

Add this repository as a custom HACS integration repository:

```text
https://github.com/LNKZUG/ocpp
```

Install the latest `v0.10.15.x` LNKZUG release and restart Home Assistant.

Documentation can be found here [home-assistant-ocpp.readthedocs.io](https://home-assistant-ocpp.readthedocs.io)
