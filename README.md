[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)

![OCPP](https://github.com/home-assistant/brands/raw/master/custom_integrations/ocpp/icon.png)

# LNKZUG OCPP

Known working Home Assistant OCPP custom integration for Entratek Power Dot Pro 2 / DUOSIDA Mode3@11KW chargers.

This fork is based on upstream `v0.5.6` and keeps the production behavior that works with the LNKZUG wallboxes.

## Installation

Add this repository to HACS as a custom integration:

```text
https://github.com/LNKZUG/ocpp
```

Install release `v0.5.27` or newer from the maintained `v0.5.x` train and restart Home Assistant.

The previous experimental `v0.10.x` releases are retired in this fork. Use the `v0.5.x` train for Entratek/DUOSIDA chargers.

## Charger URL

Configure each charger with its own Central System / port, for example:

```text
ws://192.168.248.150:9000
ws://192.168.248.150:9001
```

Do not add a charge point id path unless your charger is explicitly configured for that.

## Entratek compatibility

This fork includes the proven production adjustments:

* suppress Home Assistant persistent notifications and log them instead,
* use amps for OCPP charging profiles,
* use 60 seconds as idle sampling interval,
* keep Home Assistant `ConfigType` compatibility.

## User management

Release `v0.5.6.2` adds integration-native user management.

Add users through Home Assistant actions:

* `ocpp.add_user`
* `ocpp.update_user`
* `ocpp.set_user_active`

Users are matched by OCPP `idTag`.

For every managed user the integration creates energy sensors:

```text
OCPP <user name> Ladeenergie
OCPP <user name> Ladeenergie Monat
```

The total sensor adds completed charging sessions across all configured chargers. The monthly sensor resets on the Home Assistant local monthly cycle. Existing helpers and manually created sensors are not changed.

Each wallbox also exposes the currently mapped managed user during an active transaction:

```text
OCPP <charger name> Angemeldeter Nutzer
OCPP <charger name> Ladeenergie Monat
```
