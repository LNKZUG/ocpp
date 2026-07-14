"""Representation of a OCCP Entities."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import logging
from math import sqrt
import ssl
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OK, STATE_UNAVAILABLE, STATE_UNKNOWN, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry, entity_component, entity_registry
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
import voluptuous as vol
import websockets.protocol
import websockets.server

from ocpp.exceptions import NotImplementedError, TypeConstraintViolationError
from ocpp.messages import CallError
from ocpp.routing import on
from ocpp.v16 import ChargePoint as cp, call, call_result
from ocpp.v16.enums import (
    Action,
    AuthorizationStatus,
    AvailabilityStatus,
    AvailabilityType,
    ChargePointStatus,
    ChargingProfileKindType,
    ChargingProfilePurposeType,
    ChargingProfileStatus,
    ChargingRateUnitType,
    ClearChargingProfileStatus,
    ConfigurationStatus,
    DataTransferStatus,
    Measurand,
    MessageTrigger,
    Phase,
    RegistrationStatus,
    RemoteStartStopStatus,
    ResetStatus,
    ResetType,
    TriggerMessageStatus,
    UnitOfMeasure,
    UnlockStatus,
)

from .const import (
    CONF_AUTH_LIST,
    CONF_AUTH_STATUS,
    CONF_CPID,
    CONF_CSID,
    CONF_DEFAULT_AUTH_STATUS,
    CONF_ENERGY_PRICE_SENSOR,
    CONF_FORCE_SMART_CHARGING,
    CONF_HOST,
    CONF_ID_TAG,
    CONF_IDLE_INTERVAL,
    CONF_METER_INTERVAL,
    CONF_MONITORED_VARIABLES,
    CONF_PORT,
    CONF_SKIP_SCHEMA_VALIDATION,
    CONF_SSL,
    CONF_SSL_CERTFILE_PATH,
    CONF_SSL_KEYFILE_PATH,
    CONF_SUBPROTOCOL,
    CONF_WEBSOCKET_CLOSE_TIMEOUT,
    CONF_WEBSOCKET_PING_INTERVAL,
    CONF_WEBSOCKET_PING_TIMEOUT,
    CONF_WEBSOCKET_PING_TRIES,
    CONFIG,
    DEFAULT_AUTO_STOP_DELAY,
    DEFAULT_AUTO_STOP_ON_EVSE_SUSPENDED,
    DEFAULT_CPID,
    DEFAULT_CSID,
    DEFAULT_ENERGY_UNIT,
    DEFAULT_FORCE_SMART_CHARGING,
    DEFAULT_HOST,
    DEFAULT_IDLE_INTERVAL,
    DEFAULT_MEASURAND,
    DEFAULT_METER_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_POWER_UNIT,
    DEFAULT_SKIP_SCHEMA_VALIDATION,
    DEFAULT_SSL,
    DEFAULT_SSL_CERTFILE_PATH,
    DEFAULT_SSL_KEYFILE_PATH,
    DEFAULT_SUBPROTOCOL,
    DEFAULT_WEBSOCKET_CLOSE_TIMEOUT,
    DEFAULT_WEBSOCKET_PING_INTERVAL,
    DEFAULT_WEBSOCKET_PING_TIMEOUT,
    DEFAULT_WEBSOCKET_PING_TRIES,
    DOMAIN,
    HA_ENERGY_UNIT,
    HA_POWER_UNIT,
    UNITS_OCCP_TO_HA,
    USER_REGISTRY,
)
from .enums import (
    ConfigurationKey as ckey,
    HAChargerDetails as cdet,
    HAChargerServices as csvcs,
    HAChargerSession as csess,
    HAChargerStatuses as cstat,
    OcppMisc as om,
    Profiles as prof,
)

_LOGGER: logging.Logger = logging.getLogger(__package__)
logging.getLogger(DOMAIN).setLevel(logging.INFO)
# Uncomment these when Debugging
# logging.getLogger("asyncio").setLevel(logging.DEBUG)
# logging.getLogger("websockets").setLevel(logging.DEBUG)

REMOTE_START_CLEANUP_DELAY = 180
STALE_CONNECTOR_RESET_DELAY = 5
PRICE_OPTIMIZED_CHARGE_MODE_STANDARD = "Standard"
PRICE_OPTIMIZED_CHARGE_MODE_OPTIMIZED = "Strompreis optimiert"
PRICE_OPTIMIZED_CHARGE_MODES = [
    PRICE_OPTIMIZED_CHARGE_MODE_STANDARD,
    PRICE_OPTIMIZED_CHARGE_MODE_OPTIMIZED,
]
PRICE_PAUSE_PROFILE_ID = 80
PRICE_OPTIMIZED_CHARGE_STATUS = "PriceOptimizedChargingPaused"
STORAGE_VERSION = 1
STORAGE_CHARGE_STATE = f"{DOMAIN}_charge_state"

TIME_MINUTES = UnitOfTime.MINUTES

ACTIVE_SESSION_METRICS = (
    cstat.id_tag.value,
    csess.current_user.value,
    csess.transaction_id.value,
    csess.meter_start.value,
    csess.session_energy.value,
    csess.session_time.value,
)


def truncate_status_notification_info(action, payload: dict, max_length: int = 50):
    """Trim too-long StatusNotification info fields for non-compliant chargers."""
    if action != Action.status_notification.value:
        return False
    info = payload.get("info")
    if not isinstance(info, str) or len(info) <= max_length:
        return False
    payload["info"] = info[:max_length]
    return True


UFW_SERVICE_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("firmware_url"): cv.string,
        vol.Optional("delay_hours"): cv.positive_int,
        vol.Optional(CONF_CPID): cv.string,
    }
)
CONF_SERVICE_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("ocpp_key"): cv.string,
        vol.Required("value"): cv.string,
        vol.Optional(CONF_CPID): cv.string,
    }
)
GCONF_SERVICE_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("ocpp_key"): cv.string,
        vol.Optional(CONF_CPID): cv.string,
    }
)
GDIAG_SERVICE_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("upload_url"): cv.string,
        vol.Optional(CONF_CPID): cv.string,
    }
)
TRANS_SERVICE_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("vendor_id"): cv.string,
        vol.Optional("message_id"): cv.string,
        vol.Optional("data"): cv.string,
        vol.Optional(CONF_CPID): cv.string,
    }
)
CHRGR_SERVICE_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional("limit_amps"): cv.positive_float,
        vol.Optional("limit_watts"): cv.positive_int,
        vol.Optional("conn_id"): cv.positive_int,
        vol.Optional("custom_profile"): vol.Any(cv.string, dict),
        vol.Optional(CONF_CPID): cv.string,
    }
)


class CentralSystem:
    """Server for handling OCPP connections."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        """Instantiate instance of a CentralSystem."""
        self.hass = hass
        self.entry = entry
        self.host = entry.data.get(CONF_HOST, DEFAULT_HOST)
        self.port = entry.data.get(CONF_PORT, DEFAULT_PORT)
        self.csid = entry.data.get(CONF_CSID, DEFAULT_CSID)
        self.cpid = entry.data.get(CONF_CPID, DEFAULT_CPID)
        self.websocket_close_timeout = entry.data.get(
            CONF_WEBSOCKET_CLOSE_TIMEOUT, DEFAULT_WEBSOCKET_CLOSE_TIMEOUT
        )
        self.websocket_ping_tries = entry.data.get(
            CONF_WEBSOCKET_PING_TRIES, DEFAULT_WEBSOCKET_PING_TRIES
        )
        self.websocket_ping_interval = entry.data.get(
            CONF_WEBSOCKET_PING_INTERVAL, DEFAULT_WEBSOCKET_PING_INTERVAL
        )
        self.websocket_ping_timeout = entry.data.get(
            CONF_WEBSOCKET_PING_TIMEOUT, DEFAULT_WEBSOCKET_PING_TIMEOUT
        )

        self.subprotocol = entry.data.get(CONF_SUBPROTOCOL, DEFAULT_SUBPROTOCOL)
        self._server = None
        self.config = entry.data
        self.id = entry.entry_id
        self.user_registry = hass.data[DOMAIN].get(USER_REGISTRY)
        self._charge_state_store = Store(
            hass,
            STORAGE_VERSION,
            f"{STORAGE_CHARGE_STATE}_{entry.entry_id}",
        )
        self._legacy_charge_state_store = Store(
            hass, STORAGE_VERSION, STORAGE_CHARGE_STATE
        )
        self.selected_user_ids = {}
        self.charge_modes = {}
        self.price_optimized_charging_allowed = {}
        self.auto_stop_on_evse_suspended = {}
        self.auto_stop_delays = {}
        self.charge_points = {}
        if entry.data.get(CONF_SSL, DEFAULT_SSL):
            self.ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            # see https://community.home-assistant.io/t/certificate-authority-and-self-signed-certificate-for-ssl-tls/196970
            localhost_certfile = entry.data.get(
                CONF_SSL_CERTFILE_PATH, DEFAULT_SSL_CERTFILE_PATH
            )
            localhost_keyfile = entry.data.get(
                CONF_SSL_KEYFILE_PATH, DEFAULT_SSL_KEYFILE_PATH
            )
            self.ssl_context.load_cert_chain(
                localhost_certfile, keyfile=localhost_keyfile
            )
        else:
            self.ssl_context = None

    @staticmethod
    async def create(hass: HomeAssistant, entry: ConfigEntry):
        """Create instance and start listening for OCPP connections on given port."""
        self = CentralSystem(hass, entry)
        await self.async_load_charge_state()

        server = await websockets.server.serve(
            self.on_connect,
            self.host,
            self.port,
            subprotocols=[self.subprotocol],
            ping_interval=None,  # ping interval is not used here, because we send pings mamually in ChargePoint.monitor_connection()
            ping_timeout=None,
            close_timeout=self.websocket_close_timeout,
            ssl=self.ssl_context,
        )
        self._server = server
        return self

    async def async_load_charge_state(self) -> None:
        """Load persisted charger control state."""
        data = await self._charge_state_store.async_load()
        if data is None:
            legacy_data = await self._legacy_charge_state_store.async_load() or {}
            data = {
                key: {self.cpid: values[self.cpid]}
                for key in (
                    "selected_user_ids",
                    "charge_modes",
                    "price_optimized_charging_allowed",
                    "auto_stop_on_evse_suspended",
                    "auto_stop_delays",
                )
                if isinstance((values := legacy_data.get(key)), dict)
                and self.cpid in values
            }
            if data:
                await self._charge_state_store.async_save(data)
        self.selected_user_ids = dict(data.get("selected_user_ids", {}))
        self.charge_modes = {
            cp_id: mode
            for cp_id, mode in data.get("charge_modes", {}).items()
            if mode in PRICE_OPTIMIZED_CHARGE_MODES
        }
        self.price_optimized_charging_allowed = {
            cp_id: bool(allowed)
            for cp_id, allowed in data.get(
                "price_optimized_charging_allowed", {}
            ).items()
        }
        self.auto_stop_on_evse_suspended = {
            cp_id: bool(enabled)
            for cp_id, enabled in data.get("auto_stop_on_evse_suspended", {}).items()
        }
        self.auto_stop_delays = {
            cp_id: float(delay)
            for cp_id, delay in data.get("auto_stop_delays", {}).items()
        }

    async def async_save_charge_state(self) -> None:
        """Persist charger control state."""
        await self._charge_state_store.async_save(
            {
                "selected_user_ids": self.selected_user_ids,
                "charge_modes": self.charge_modes,
                "price_optimized_charging_allowed": (
                    self.price_optimized_charging_allowed
                ),
                "auto_stop_on_evse_suspended": getattr(
                    self, "auto_stop_on_evse_suspended", {}
                ),
                "auto_stop_delays": getattr(self, "auto_stop_delays", {}),
            }
        )

    @callback
    def schedule_charge_state_save(self) -> None:
        """Schedule persistence of charger control state."""
        if hasattr(self, "hass"):
            self.hass.async_create_task(self.async_save_charge_state())

    async def on_connect(
        self, websocket: websockets.server.WebSocketServerProtocol, path: str
    ):
        """Request handler executed for every new OCPP connection."""
        if self.config.get(CONF_SKIP_SCHEMA_VALIDATION, DEFAULT_SKIP_SCHEMA_VALIDATION):
            _LOGGER.warning("Skipping websocket subprotocol validation")
        else:
            if websocket.subprotocol is not None:
                _LOGGER.info("Websocket Subprotocol matched: %s", websocket.subprotocol)
            else:
                # In the websockets lib if no subprotocols are supported by the
                # client and the server, it proceeds without a subprotocol,
                # so we have to manually close the connection.
                _LOGGER.warning(
                    "Protocols mismatched | expected Subprotocols: %s,"
                    " but client supports  %s | Closing connection",
                    websocket.available_subprotocols,
                    websocket.request_headers.get("Sec-WebSocket-Protocol", ""),
                )
                return await websocket.close()

        _LOGGER.info(f"Charger websocket path={path}")
        reported_cp_id = path.strip("/")
        reported_cp_id = reported_cp_id[reported_cp_id.rfind("/") + 1 :]
        cp_id = reported_cp_id or self.cpid
        if self.cpid not in self.charge_points:
            _LOGGER.info(f"Charger {cp_id} connected to {self.host}:{self.port}.")
            charge_point = ChargePoint(cp_id, websocket, self.hass, self.entry, self)
            self.charge_points[self.cpid] = charge_point
            await charge_point.start()
        else:
            _LOGGER.info(f"Charger {cp_id} reconnected to {self.host}:{self.port}.")
            charge_point: ChargePoint = self.charge_points[self.cpid]
            await charge_point.reconnect(websocket)
        _LOGGER.info(f"Charger {cp_id} disconnected from {self.host}:{self.port}.")

    def get_metric(self, cp_id: str, measurand: str):
        """Return last known value for given measurand."""
        if cp_id in self.charge_points:
            return self.charge_points[cp_id]._metrics[measurand].value
        return None

    def del_metric(self, cp_id: str, measurand: str):
        """Set given measurand to None."""
        if cp_id in self.charge_points:
            self.charge_points[cp_id]._metrics[measurand].value = None
        return None

    def get_unit(self, cp_id: str, measurand: str):
        """Return unit of given measurand."""
        if cp_id in self.charge_points:
            return self.charge_points[cp_id]._metrics[measurand].unit
        return None

    def get_ha_unit(self, cp_id: str, measurand: str):
        """Return home assistant unit of given measurand."""
        if cp_id in self.charge_points:
            return self.charge_points[cp_id]._metrics[measurand].ha_unit
        return None

    def get_extra_attr(self, cp_id: str, measurand: str):
        """Return last known extra attributes for given measurand."""
        if cp_id in self.charge_points:
            return self.charge_points[cp_id]._metrics[measurand].extra_attr
        return None

    def get_available(self, cp_id: str):
        """Return whether the charger is available."""
        if cp_id in self.charge_points:
            return self.charge_points[cp_id].status == STATE_OK
        return False

    def get_supported_features(self, cp_id: str):
        """Return what profiles the charger supports."""
        if cp_id in self.charge_points:
            return self.charge_points[cp_id].supported_features
        return 0

    def get_auto_stop_on_evse_suspended(self, cp_id: str):
        """Return whether EVSE suspend should automatically stop the transaction."""
        if cp_id in self.charge_points:
            return self.charge_points[cp_id].auto_stop_on_evse_suspended
        return getattr(self, "auto_stop_on_evse_suspended", {}).get(
            cp_id, DEFAULT_AUTO_STOP_ON_EVSE_SUSPENDED
        )

    def has_active_transaction(self, cp_id: str):
        """Return whether the charger has an active transaction."""
        if cp_id in self.charge_points:
            return self.charge_points[cp_id].active_transaction_id != 0
        return False

    def set_auto_stop_on_evse_suspended(self, cp_id: str, value: bool):
        """Set whether EVSE suspend should automatically stop the transaction."""
        if not hasattr(self, "auto_stop_on_evse_suspended"):
            self.auto_stop_on_evse_suspended = {}
        self.auto_stop_on_evse_suspended[cp_id] = bool(value)
        if cp_id in self.charge_points:
            self.charge_points[cp_id].auto_stop_on_evse_suspended = bool(value)
        self.schedule_charge_state_save()
        return True

    def get_auto_stop_delay(self, cp_id: str):
        """Return EVSE suspend auto-stop delay in seconds."""
        if cp_id in self.charge_points:
            return self.charge_points[cp_id].auto_stop_delay
        return getattr(self, "auto_stop_delays", {}).get(cp_id, DEFAULT_AUTO_STOP_DELAY)

    def set_auto_stop_delay(self, cp_id: str, value: float):
        """Set EVSE suspend auto-stop delay in seconds."""
        if not hasattr(self, "auto_stop_delays"):
            self.auto_stop_delays = {}
        self.auto_stop_delays[cp_id] = float(value)
        if cp_id in self.charge_points:
            self.charge_points[cp_id].auto_stop_delay = float(value)
        self.schedule_charge_state_save()
        return True

    def set_selected_user(self, cp_id: str, user_id: str | None) -> bool:
        """Store the selected OCPP user for a charge point."""
        if user_id is None:
            self.selected_user_ids.pop(cp_id, None)
            self.schedule_charge_state_save()
            return True
        if self.user_registry is None:
            return False
        user = self.user_registry.get_user(user_id)
        if user is None or not user.get("active", True) or not user.get("id_tags", []):
            return False
        self.selected_user_ids[cp_id] = user_id
        self.schedule_charge_state_save()
        return True

    def get_selected_user(self, cp_id: str):
        """Return the selected OCPP user for a charge point."""
        if self.user_registry is None:
            return None
        user_id = self.selected_user_ids.get(cp_id)
        if user_id is None:
            return None
        user = self.user_registry.get_user(user_id)
        if user is None or not user.get("active", True) or not user.get("id_tags", []):
            self.selected_user_ids.pop(cp_id, None)
            self.schedule_charge_state_save()
            return None
        return user

    async def start_transaction_for_user(self, cp_id: str, user_id: str):
        """Remote start a transaction for a managed OCPP user."""
        if self.user_registry is None:
            return False
        user = self.user_registry.get_user(user_id)
        if user is None or not user.get("active", True) or not user.get("id_tags", []):
            return False
        if cp_id in self.charge_points:
            return await self.charge_points[cp_id].start_transaction(user["id_tags"][0])
        return False

    async def start_transaction_for_selected_user(self, cp_id: str):
        """Remote start a transaction for the selected managed OCPP user."""
        user = self.get_selected_user(cp_id)
        if user is None:
            return False
        return await self.start_transaction_for_user(cp_id, user["user_id"])

    async def set_max_charge_rate_amps(self, cp_id: str, value: float):
        """Set the maximum charge rate in amps."""
        if cp_id in self.charge_points:
            return await self.charge_points[cp_id].set_charge_rate(limit_amps=value)
        return False

    def get_charge_mode(self, cp_id: str) -> str:
        """Return selected charge mode for a charge point."""
        return self.charge_modes.get(cp_id, PRICE_OPTIMIZED_CHARGE_MODE_STANDARD)

    async def set_charge_mode(self, cp_id: str, mode: str) -> bool:
        """Set selected charge mode and apply the matching charger state."""
        if mode not in PRICE_OPTIMIZED_CHARGE_MODES:
            return False

        if cp_id not in self.charge_points:
            self.charge_modes[cp_id] = mode
            self.schedule_charge_state_save()
            return True

        if mode == PRICE_OPTIMIZED_CHARGE_MODE_STANDARD:
            if not await self.charge_points[cp_id].resume_price_optimized_charging(
                force=True
            ):
                return False
            self.charge_modes[cp_id] = mode
            self.schedule_charge_state_save()
            return True

        self.charge_modes[cp_id] = mode
        if not self.get_price_optimized_charging_allowed(cp_id):
            if not await self.charge_points[cp_id].pause_price_optimized_charging():
                self.charge_modes[cp_id] = PRICE_OPTIMIZED_CHARGE_MODE_STANDARD
                self.schedule_charge_state_save()
                return False
        self.schedule_charge_state_save()
        return True

    def get_price_optimized_charging_allowed(self, cp_id: str) -> bool:
        """Return whether price-optimized charging is currently allowed."""
        return self.price_optimized_charging_allowed.get(cp_id, True)

    def get_energy_price_sensor_entity_id(self, cp_id: str) -> str | None:
        """Return the configured live energy price sensor for a charge point."""
        if cp_id != self.cpid:
            return None
        entity_id = self.entry.options.get(
            CONF_ENERGY_PRICE_SENSOR,
            self.entry.data.get(CONF_ENERGY_PRICE_SENSOR, ""),
        )
        return entity_id or None

    def get_current_energy_price(self, cp_id: str) -> float | None:
        """Return the current energy price in currency per kWh."""
        entity_id = self.get_energy_price_sensor_entity_id(cp_id)
        if entity_id is None:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        try:
            price = float(str(state.state).replace(",", "."))
        except (TypeError, ValueError):
            _LOGGER.warning(
                "Ignoring invalid OCPP energy price sensor state for %s: %s",
                entity_id,
                state.state,
            )
            return None
        unit = str(state.attributes.get("unit_of_measurement", ""))
        normalized_unit = unit.lower().replace(" ", "")
        if normalized_unit in ("ct/kwh", "cent/kwh", "c/kwh"):
            price = price / 100
        elif normalized_unit in ("eur/mwh", "€/mwh", "euro/mwh"):
            price = price / 1000
        elif normalized_unit not in ("eur/kwh", "€/kwh", "euro/kwh"):
            _LOGGER.warning(
                "Ignoring OCPP energy price sensor %s with unsupported unit %s",
                entity_id,
                unit or "<missing>",
            )
            return None
        return price

    async def set_price_optimized_charging_allowed(
        self, cp_id: str, allowed: bool
    ) -> bool:
        """Store and apply price-optimized charging pause state."""
        if cp_id not in self.charge_points:
            self.price_optimized_charging_allowed[cp_id] = allowed
            self.schedule_charge_state_save()
            return True
        if self.get_charge_mode(cp_id) != PRICE_OPTIMIZED_CHARGE_MODE_OPTIMIZED:
            self.price_optimized_charging_allowed[cp_id] = allowed
            self.schedule_charge_state_save()
            return True
        if allowed:
            if not await self.charge_points[cp_id].resume_price_optimized_charging():
                return False
            self.price_optimized_charging_allowed[cp_id] = allowed
            self.schedule_charge_state_save()
            return True
        if not await self.charge_points[cp_id].pause_price_optimized_charging():
            return False
        self.price_optimized_charging_allowed[cp_id] = allowed
        self.schedule_charge_state_save()
        return True

    def is_price_optimized_charging_paused(self, cp_id: str) -> bool:
        """Return whether charging is currently paused by price optimization."""
        if (
            self.get_charge_mode(cp_id) != PRICE_OPTIMIZED_CHARGE_MODE_OPTIMIZED
            or self.get_price_optimized_charging_allowed(cp_id)
            or cp_id not in self.charge_points
            or not self.has_active_transaction(cp_id)
        ):
            return False
        return bool(
            getattr(self.charge_points[cp_id], "_price_pause_profile_applied", False)
        )

    async def set_charger_state(
        self, cp_id: str, service_name: str, state: bool = True
    ):
        """Carry out requested service/state change on connected charger."""
        resp = False
        if cp_id in self.charge_points:
            if service_name == csvcs.service_availability.name:
                resp = await self.charge_points[cp_id].set_availability(state)
            if service_name == csvcs.service_charge_start.name:
                resp = await self.charge_points[cp_id].start_transaction()
            if service_name == csvcs.service_charge_stop.name:
                resp = await self.charge_points[cp_id].stop_transaction()
            if service_name == csvcs.service_reset.name:
                resp = await self.charge_points[cp_id].reset()
            if service_name == csvcs.service_unlock.name:
                resp = await self.charge_points[cp_id].unlock()
        return resp

    async def update(self, cp_id: str):
        """Update sensors values in HA."""
        er = entity_registry.async_get(self.hass)
        dr = device_registry.async_get(self.hass)
        identifiers = {(DOMAIN, cp_id)}
        dev = dr.async_get_device(identifiers)
        # _LOGGER.info("Device id: %s updating", dev.name)
        for ent in entity_registry.async_entries_for_device(er, dev.id):
            # _LOGGER.info("Entity id: %s updating", ent.entity_id)
            self.hass.async_create_task(
                entity_component.async_update_entity(self.hass, ent.entity_id)
            )

    def device_info(self):
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self.id)},
        }


class ChargePoint(cp):
    """Server side representation of a charger."""

    def __init__(
        self,
        id: str,
        connection: websockets.server.WebSocketServerProtocol,
        hass: HomeAssistant,
        entry: ConfigEntry,
        central: CentralSystem,
        interval_meter_metrics: int = 10,
        skip_schema_validation: bool = False,
    ):
        """Instantiate a ChargePoint."""

        super().__init__(id, connection)

        for action in self.route_map:
            self.route_map[action]["_skip_schema_validation"] = skip_schema_validation

        self.interval_meter_metrics = interval_meter_metrics
        self.hass = hass
        self.entry = entry
        self.central = central
        self.status = "init"
        # Indicates if the charger requires a reboot to apply new
        # configuration.
        self._requires_reboot = False
        self.preparing = asyncio.Event()
        self.active_transaction_id: int = 0
        self._stopped_transaction_ids = set()
        self._stopped_session_energy_transaction_ids = set()
        self.triggered_boot_notification = False
        self.received_boot_notification = False
        self.post_connect_success = False
        self.tasks = None
        self._auto_stop_task = None
        self._remote_start_cleanup_task = None
        self._remote_start_cleanup_id_tag = None
        self._price_pause_profile_applied = False
        self._post_connect_lock = asyncio.Lock()
        self.auto_stop_on_evse_suspended = central.get_auto_stop_on_evse_suspended(
            central.cpid
        )
        self.auto_stop_delay = central.get_auto_stop_delay(central.cpid)
        self._charger_reports_session_energy = False
        self._metrics = defaultdict(lambda: Metric(None, None))
        self._metrics[cdet.identifier.value].value = id
        self._metrics[csess.session_time.value].unit = TIME_MINUTES
        self._metrics[csess.session_energy.value].unit = UnitOfMeasure.kwh.value
        self._metrics[csess.monthly_energy.value].unit = UnitOfMeasure.kwh.value
        self._metrics[csess.meter_start.value].unit = UnitOfMeasure.kwh.value
        self._attr_supported_features = prof.NONE
        self._metrics[cstat.reconnects.value].value: int = 0
        self.restore_latest_active_session_from_registry()

    async def post_connect(self, force: bool = False):
        """Logic to be executed right after a charger connects."""
        async with self._post_connect_lock:
            if self.post_connect_success and not force:
                return
            await self._post_connect()

    async def _post_connect(self):
        """Configure a newly connected charger exactly once."""
        try:
            self.status = STATE_OK
            await asyncio.sleep(2)
            await self.get_supported_features()
            resp = await self.get_configuration(ckey.number_of_connectors.value)
            self._metrics[cdet.connectors.value].value = resp
            await self.get_configuration(ckey.heartbeat_interval.value)

            all_measurands = self.entry.data.get(
                CONF_MONITORED_VARIABLES, DEFAULT_MEASURAND
            )

            accepted_measurands = []
            key = ckey.meter_values_sampled_data.value

            for measurand in all_measurands.split(","):
                _LOGGER.debug(f"'{self.id}' trying measurand '{measurand}'")
                req = call.ChangeConfiguration(key=key, value=measurand)
                resp = await self.call(req)
                if resp.status == ConfigurationStatus.accepted:
                    _LOGGER.debug(f"'{self.id}' adding measurand '{measurand}'")
                    accepted_measurands.append(measurand)

            accepted_measurands = ",".join(accepted_measurands)

            if len(accepted_measurands) > 0:
                _LOGGER.debug(f"'{self.id}' allowed measurands '{accepted_measurands}'")
                await self.configure(
                    ckey.meter_values_sampled_data.value,
                    accepted_measurands,
                )
            else:
                _LOGGER.debug(f"'{self.id}' measurands not configurable by OCPP")
                resp = await self.get_configuration(
                    ckey.meter_values_sampled_data.value
                )
                accepted_measurands = resp
                _LOGGER.debug(f"'{self.id}' allowed measurands '{accepted_measurands}'")

            updated_entry = {**self.entry.data}
            updated_entry[CONF_MONITORED_VARIABLES] = accepted_measurands
            self.hass.config_entries.async_update_entry(self.entry, data=updated_entry)

            await self.configure(
                ckey.meter_value_sample_interval.value,
                str(self.entry.data.get(CONF_METER_INTERVAL, DEFAULT_METER_INTERVAL)),
            )
            await self.configure(
                ckey.clock_aligned_data_interval.value,
                str(self.entry.data.get(CONF_IDLE_INTERVAL, DEFAULT_IDLE_INTERVAL)),
            )
            #            await self.configure(
            #                "StopTxnSampledData", ",".join(self.entry.data[CONF_MONITORED_VARIABLES])
            #            )
            #            await self.start_transaction()

            self.post_connect_success = True
            _LOGGER.debug(f"'{self.id}' post connection setup completed successfully")

            # nice to have, but not needed for integration to function
            # and can cause issues with some chargers
            await self.configure(ckey.web_socket_ping_interval.value, "60")
            await self.set_availability()
            if prof.REM in self._attr_supported_features:
                if self.received_boot_notification is False:
                    await self.trigger_boot_notification()
                await self.trigger_status_notification()
            await self.reconcile_charge_control_state()
        except NotImplementedError as e:
            _LOGGER.error("Configuration of the charger failed: %s", e)

    async def get_supported_features(self):
        """Get supported features."""
        req = call.GetConfiguration(key=[ckey.supported_feature_profiles.value])
        try:
            resp = await self.call(req)
            feature_list = (resp.configuration_key[0][om.value.value]).split(",")
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "%s did not respond to SupportedFeatureProfiles, defaulting to Core",
                self.id,
            )
            feature_list = [om.feature_profile_core.value]
        if feature_list[0] == "":
            _LOGGER.warning("No feature profiles detected, defaulting to Core")
            await self.notify_ha("No feature profiles detected, defaulting to Core")
            feature_list = [om.feature_profile_core.value]
        if self.central.config.get(
            CONF_FORCE_SMART_CHARGING, DEFAULT_FORCE_SMART_CHARGING
        ):
            _LOGGER.warning("Force Smart Charging feature profile")
            self._attr_supported_features |= prof.SMART
        for item in feature_list:
            item = item.strip().replace(" ", "")
            if item == om.feature_profile_core.value:
                self._attr_supported_features |= prof.CORE
            elif item == om.feature_profile_firmware.value:
                self._attr_supported_features |= prof.FW
            elif item == om.feature_profile_smart.value:
                self._attr_supported_features |= prof.SMART
            elif item == om.feature_profile_reservation.value:
                self._attr_supported_features |= prof.RES
            elif item == om.feature_profile_remote.value:
                self._attr_supported_features |= prof.REM
            elif item == om.feature_profile_auth.value:
                self._attr_supported_features |= prof.AUTH
            else:
                _LOGGER.warning("Unknown feature profile detected ignoring: %s", item)
                await self.notify_ha(
                    f"Warning: Unknown feature profile detected ignoring {item}"
                )
        self._metrics[cdet.features.value].value = self._attr_supported_features
        _LOGGER.debug("Feature profiles returned: %s", self._attr_supported_features)

    async def trigger_boot_notification(self):
        """Trigger a boot notification."""
        req = call.TriggerMessage(requested_message=MessageTrigger.boot_notification)
        resp = await self.call(req)
        if resp.status == TriggerMessageStatus.accepted:
            self.triggered_boot_notification = True
            return True
        else:
            self.triggered_boot_notification = False
            _LOGGER.warning("Failed with response: %s", resp.status)
            return False

    async def trigger_status_notification(self):
        """Trigger status notifications for all connectors."""
        return_value = True
        nof_connectors = int(self._metrics[cdet.connectors.value].value or 1)
        for id in range(0, nof_connectors + 1):
            _LOGGER.debug(f"trigger status notification for connector={id}")
            req = call.TriggerMessage(
                requested_message=MessageTrigger.status_notification,
                connector_id=int(id),
            )
            resp = await self.call(req)
            if resp.status != TriggerMessageStatus.accepted:
                _LOGGER.warning("Failed with response: %s", resp.status)
                return_value = False
        return return_value

    async def clear_profile(self, profile_id: int | None = None):
        """Clear all charging profiles."""
        payload = {}
        if profile_id is not None:
            payload["id"] = profile_id
        req = call.ClearChargingProfile(**payload)
        resp = await self.call(req)
        if resp.status == ClearChargingProfileStatus.accepted:
            return True
        else:
            _LOGGER.warning("Failed with response: %s", resp.status)
            await self.notify_ha(
                f"Warning: Clear profile failed with response {resp.status}"
            )
            return False

    async def set_charge_rate(
        self,
        limit_amps: int = 32,
        limit_watts: int = 22000,
        conn_id: int = 0,
        profile: dict | None = None,
        profile_id: int = 8,
    ):
        """Set a charging profile with defined limit."""
        if profile is not None:  # assumes advanced user and correct profile format
            req = call.SetChargingProfile(
                connector_id=conn_id, cs_charging_profiles=profile
            )
            resp = await self.call(req)
            if resp.status == ChargingProfileStatus.accepted:
                return True
            else:
                _LOGGER.warning("Failed with response: %s", resp.status)
                await self.notify_ha(
                    f"Warning: Set charging profile failed with response {resp.status}"
                )
                return False

        if prof.SMART in self._attr_supported_features:
            #            resp = await self.get_configuration(
            resp = om.current.value
            #                ckey.charging_schedule_allowed_charging_rate_unit.value
            #            )
            _LOGGER.info(
                "Charger supports setting the following units: %s",
                resp,
            )
            _LOGGER.info("If more than one unit supported default unit is Amps")
            if om.current.value in resp:
                lim = limit_amps
                units = ChargingRateUnitType.amps.value
            else:
                lim = limit_watts
                units = ChargingRateUnitType.watts.value
            resp = await self.get_configuration(
                ckey.charge_profile_max_stack_level.value
            )
            stack_level = int(resp)
            req = call.SetChargingProfile(
                connector_id=conn_id,
                cs_charging_profiles={
                    om.charging_profile_id.value: profile_id,
                    om.stack_level.value: stack_level,
                    om.charging_profile_kind.value: ChargingProfileKindType.relative.value,
                    om.charging_profile_purpose.value: ChargingProfilePurposeType.charge_point_max_profile.value,
                    om.charging_schedule.value: {
                        om.charging_rate_unit.value: units,
                        om.charging_schedule_period.value: [
                            {om.start_period.value: 0, om.limit.value: lim}
                        ],
                    },
                },
            )
        else:
            _LOGGER.info("Smart charging is not supported by this charger")
            return False
        resp = await self.call(req)
        if resp.status == ChargingProfileStatus.accepted:
            return True
        else:
            _LOGGER.debug(
                "ChargePointMaxProfile is not supported by this charger, trying TxDefaultProfile instead..."
            )
            # try a lower stack level for chargers where level < maximum, not <=
            req = call.SetChargingProfile(
                connector_id=conn_id,
                cs_charging_profiles={
                    om.charging_profile_id.value: profile_id,
                    om.stack_level.value: stack_level - 1,
                    om.charging_profile_kind.value: ChargingProfileKindType.relative.value,
                    om.charging_profile_purpose.value: ChargingProfilePurposeType.tx_default_profile.value,
                    om.charging_schedule.value: {
                        om.charging_rate_unit.value: units,
                        om.charging_schedule_period.value: [
                            {om.start_period.value: 0, om.limit.value: lim}
                        ],
                    },
                },
            )
            resp = await self.call(req)
            if resp.status == ChargingProfileStatus.accepted:
                return True
            else:
                _LOGGER.warning("Failed with response: %s", resp.status)
                await self.notify_ha(
                    f"Warning: Set charging profile failed with response {resp.status}"
                )
                return False

    def _active_session_snapshot(self) -> dict | None:
        """Return current active session metrics for short OCPP control changes."""
        transaction_id = self.active_transaction_id
        if transaction_id == 0:
            return None
        metrics = getattr(self, "_metrics", None)

        return {
            "transaction_id": transaction_id,
            "charger_reports_session_energy": getattr(
                self, "_charger_reports_session_energy", False
            ),
            "metrics": {
                metric: {
                    "value": metrics[metric].value,
                    "unit": metrics[metric].unit,
                    "extra_attr": dict(metrics[metric].extra_attr),
                }
                for metric in ACTIVE_SESSION_METRICS
            }
            if metrics is not None
            else {},
        }

    def _restore_active_session_snapshot(self, snapshot: dict | None) -> None:
        """Restore active session metrics if a control change cleared them."""
        if snapshot is None:
            return
        if self.active_transaction_id != snapshot["transaction_id"]:
            return
        if self.active_transaction_id == 0:
            return

        self._charger_reports_session_energy = snapshot[
            "charger_reports_session_energy"
        ]
        for metric, values in snapshot["metrics"].items():
            if self._metrics[metric].value is not None:
                continue
            if values["value"] is None:
                continue
            self._metrics[metric].value = values["value"]
            self._metrics[metric].unit = values["unit"]
            self._metrics[metric].extra_attr = dict(values["extra_attr"])

    def _set_session_energy(
        self, session_energy: float, unit: str | None = None
    ) -> None:
        """Set session energy without decreasing it during an active transaction."""
        metric = self._metrics[csess.session_energy.value]
        if float(session_energy) < 0:
            _LOGGER.debug(
                "%s ignores negative session energy %.6f for transaction %s",
                self.id,
                float(session_energy),
                self.active_transaction_id,
            )
            return
        if metric.value is not None and float(session_energy) < float(metric.value):
            _LOGGER.debug(
                "%s ignores decreasing session energy %.6f -> %.6f for transaction %s",
                self.id,
                float(metric.value),
                float(session_energy),
                self.active_transaction_id,
            )
            return

        metric.value = float(session_energy)
        if unit is not None:
            metric.unit = unit
        metric.extra_attr[cstat.id_tag.name] = self._metrics[cstat.id_tag.value].value

    def _restore_active_transaction_id(self, transaction_id: int | None) -> None:
        """Restore active transaction id from HA state or MeterValues."""
        if self._metrics[csess.transaction_id.value].value is not None:
            return
        if transaction_id in getattr(self, "_stopped_transaction_ids", set()):
            return

        value = self.get_ha_metric(csess.transaction_id.value)
        if value is None:
            value = transaction_id
        else:
            value = int(value)
            _LOGGER.debug(
                "%s was None, restored value=%s from HA.",
                csess.transaction_id.value,
                value,
            )

        if value in (None, 0, "0"):
            return
        self._metrics[csess.transaction_id.value].value = value
        self.active_transaction_id = int(value)

    def _active_registry_session(self, transaction_id: int | str | None = None):
        """Return the persisted active user session for this transaction."""
        if self.central.user_registry is None:
            return None
        get_session = getattr(self.central.user_registry, "get_session", None)
        if get_session is None:
            return None
        if transaction_id is None:
            transaction_id = self.active_transaction_id
        return get_session(self.central.cpid, transaction_id)

    def _restore_meter_start(self) -> None:
        """Restore transaction meter start without using the current meter value."""
        if self._metrics[csess.meter_start.value].value is not None:
            return

        session = self._active_registry_session()
        if session is not None:
            value = float(session["meter_start_kwh"])
            self._metrics[csess.meter_start.value].value = value
            _LOGGER.debug(
                "%s was None, restored value=%s from user registry session.",
                csess.meter_start.value,
                value,
            )
            return

        value = self.get_ha_metric(csess.meter_start.value)
        if value is not None:
            value = float(value)
            self._metrics[csess.meter_start.value].value = value
            _LOGGER.debug(
                "%s was None, restored value=%s from HA.",
                csess.meter_start.value,
                value,
            )

    def _restore_current_session_user(self) -> None:
        """Restore active session user metadata from registry or HA state."""
        if self._metrics[csess.current_user.value].value is None:
            self.restore_current_user_from_session(self.active_transaction_id)

        if self._metrics[cstat.id_tag.value].value is None:
            value = self.get_ha_metric(cstat.id_tag.value)
            if value is not None:
                self._metrics[cstat.id_tag.value].value = value

        if self._metrics[csess.current_user.value].value is None:
            value = self.get_ha_metric(csess.current_user.value)
            if value is not None:
                self._metrics[csess.current_user.value].value = value

    async def pause_price_optimized_charging(self):
        """Pause charging without ending the active transaction."""
        if self.active_transaction_id == 0:
            return True
        snapshot = self._active_session_snapshot()
        _LOGGER.info(
            "%s pauses charging for price-optimized mode without stopping transaction %s",
            self.id,
            self.active_transaction_id,
        )
        applied = await self.set_charge_rate(
            limit_amps=0,
            limit_watts=0,
            profile_id=PRICE_PAUSE_PROFILE_ID,
        )
        self._restore_active_session_snapshot(snapshot)
        if applied:
            self._cancel_auto_stop()
            self._price_pause_profile_applied = True
            if hasattr(self, "central") and hasattr(self, "hass"):
                self.hass.async_create_task(self.central.update(self.central.cpid))
        return applied

    async def resume_price_optimized_charging(self, force: bool = False):
        """Resume charging by clearing the price-optimization pause profile."""
        if not force and not getattr(self, "_price_pause_profile_applied", False):
            return True
        snapshot = self._active_session_snapshot()
        _LOGGER.info("%s resumes charging for price-optimized mode", self.id)
        cleared = await self.clear_profile(profile_id=PRICE_PAUSE_PROFILE_ID)
        self._restore_active_session_snapshot(snapshot)
        if cleared:
            self._price_pause_profile_applied = False
            if hasattr(self, "central") and hasattr(self, "hass"):
                self.hass.async_create_task(self.central.update(self.central.cpid))
        return cleared

    async def reconcile_charge_control_state(self) -> None:
        """Apply the persisted charging intent after connect or reconnect."""
        if prof.SMART not in self._attr_supported_features:
            return
        mode = self.central.get_charge_mode(self.central.cpid)
        allowed = self.central.get_price_optimized_charging_allowed(self.central.cpid)
        if (
            mode == PRICE_OPTIMIZED_CHARGE_MODE_OPTIMIZED
            and not allowed
            and self.active_transaction_id != 0
        ):
            await self.pause_price_optimized_charging()
            return
        await self.resume_price_optimized_charging(force=True)

    async def set_availability(self, state: bool = True):
        """Change availability."""
        if state is True:
            typ = AvailabilityType.operative.value
        else:
            typ = AvailabilityType.inoperative.value

        req = call.ChangeAvailability(connector_id=0, type=typ)
        resp = await self.call(req)
        if resp.status == AvailabilityStatus.accepted:
            return True
        else:
            _LOGGER.warning("Failed with response: %s", resp.status)
            await self.notify_ha(
                f"Warning: Set availability failed with response {resp.status}"
            )
            return False

    async def start_transaction(self, id_tag: str | None = None):
        """
        Remote start a transaction.

        Check if authorisation enabled, if it is disable it before remote start
        """
        resp = await self.get_configuration(ckey.authorize_remote_tx_requests.value)
        if str(resp).strip().lower() == "true":
            await self.configure(ckey.authorize_remote_tx_requests.value, "false")
        if id_tag is None:
            id_tag = self._metrics[cdet.identifier.value].value[:20]
        req = call.RemoteStartTransaction(connector_id=1, id_tag=str(id_tag)[:20])
        resp = await self.call(req)
        if resp.status == RemoteStartStopStatus.accepted:
            self._schedule_remote_start_cleanup(str(id_tag)[:20])
            return True
        else:
            _LOGGER.warning("Failed with response: %s", resp.status)
            await self.notify_ha(
                f"Warning: Start transaction failed with response {resp.status}"
            )
            return False

    async def stop_transaction(self):
        """
        Request remote stop of current transaction.

        Leaves charger in finishing state until unplugged.
        Use reset() to make the charger available again for remote start
        """
        if self.active_transaction_id == 0:
            return True
        req = call.RemoteStopTransaction(transaction_id=self.active_transaction_id)
        resp = await self.call(req)
        if resp.status == RemoteStartStopStatus.accepted:
            return True
        else:
            _LOGGER.warning("Failed with response: %s", resp.status)
            await self.notify_ha(
                f"Warning: Stop transaction failed with response {resp.status}"
            )
            return False

    def _cancel_auto_stop(self):
        """Cancel a pending EVSE-suspend auto-stop task."""
        task = getattr(self, "_auto_stop_task", None)
        if task is not None and not task.done():
            self._auto_stop_task.cancel()
        self._auto_stop_task = None

    def _cancel_remote_start_cleanup(self):
        """Cancel pending remote-start cleanup."""
        task = getattr(self, "_remote_start_cleanup_task", None)
        if task is not None and not task.done():
            task.cancel()
        self._remote_start_cleanup_task = None
        self._remote_start_cleanup_id_tag = None

    def _has_active_import(self):
        """Return whether the charger currently reports meaningful import."""
        for metric in (
            Measurand.power_active_import.value,
            Measurand.current_import.value,
        ):
            value = self._metrics[metric].value
            if value is not None and float(value) > 0.05:
                return True
        return False

    def _is_waiting_for_price_optimized_charge_window(self) -> bool:
        """Return whether a pending session is intentionally waiting for price."""
        if not hasattr(self, "central"):
            return False
        get_charge_mode = getattr(self.central, "get_charge_mode", None)
        get_price_optimized_charging_allowed = getattr(
            self.central, "get_price_optimized_charging_allowed", None
        )
        if get_charge_mode is None or get_price_optimized_charging_allowed is None:
            return False
        return get_charge_mode(
            self.central.cpid
        ) == PRICE_OPTIMIZED_CHARGE_MODE_OPTIMIZED and not get_price_optimized_charging_allowed(
            self.central.cpid
        )

    def _schedule_auto_stop_on_evse_suspended(self, reason: str):
        """Schedule remote stop after EVSE-side suspension if still idle."""
        if not self.auto_stop_on_evse_suspended:
            return
        if self.active_transaction_id == 0:
            return
        if getattr(self, "_price_pause_profile_applied", False):
            _LOGGER.debug(
                "%s skips EVSE auto-stop while price optimized charging is paused",
                self.id,
            )
            return
        if self._auto_stop_task is not None and not self._auto_stop_task.done():
            return

        transaction_id = self.active_transaction_id
        delay = max(0, float(self.auto_stop_delay or 0))
        _LOGGER.info(
            "%s schedules RemoteStopTransaction for transaction %s in %.1f seconds: %s",
            self.id,
            transaction_id,
            delay,
            reason,
        )
        self._auto_stop_task = self.hass.async_create_task(
            self._auto_stop_after_evse_suspended(delay, transaction_id, reason)
        )

    async def _auto_stop_after_evse_suspended(
        self, delay: float, transaction_id: int, reason: str
    ):
        """Remote stop a transaction if EVSE-side suspension persists."""
        try:
            await asyncio.sleep(delay)
            if not self.auto_stop_on_evse_suspended:
                return
            if self.active_transaction_id != transaction_id:
                return
            if getattr(self, "_price_pause_profile_applied", False):
                return
            if self._has_active_import():
                return
            status = self._metrics[cstat.status_connector.value].value
            if status not in (
                ChargePointStatus.suspended_evse.value,
                ChargePointStatus.suspended_ev.value,
            ):
                return

            _LOGGER.info(
                "%s sends RemoteStopTransaction for transaction %s after %s",
                self.id,
                transaction_id,
                reason,
            )
            if await self.stop_transaction():
                await self.trigger_status_notification()
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception(
                "%s failed to auto-stop transaction %s after EVSE suspension",
                self.id,
                transaction_id,
            )
        finally:
            if self._auto_stop_task is asyncio.current_task():
                self._auto_stop_task = None

    def _schedule_remote_start_cleanup(
        self, id_tag: str | None, reset_existing: bool = True
    ):
        """Schedule cleanup when a remote start never becomes a transaction."""
        task = getattr(self, "_remote_start_cleanup_task", None)
        if task is not None and not task.done() and not reset_existing:
            return

        self._cancel_remote_start_cleanup()
        self._remote_start_cleanup_id_tag = id_tag
        self._remote_start_cleanup_task = self.hass.async_create_task(
            self._cleanup_pending_remote_start(id_tag)
        )

    def _clear_pending_session_metrics(self):
        """Clear local session attribution for a pending start without transaction."""
        self._metrics[cstat.id_tag.value].value = None
        self._metrics[csess.current_user.value].value = None
        self._metrics[csess.current_user.value].extra_attr = {}

    async def _cleanup_pending_remote_start(self, id_tag: str | None):
        """Recover from a remote start that stayed pending without a transaction."""
        try:
            await asyncio.sleep(REMOTE_START_CLEANUP_DELAY)
            if self.active_transaction_id != 0:
                return
            status = self._metrics[cstat.status_connector.value].value
            if status not in (
                ChargePointStatus.preparing.value,
                ChargePointStatus.suspended_ev.value,
                ChargePointStatus.suspended_evse.value,
            ):
                return
            if self._is_waiting_for_price_optimized_charge_window():
                _LOGGER.debug(
                    "%s keeps pending session user while waiting for price-optimized charging window",
                    self.id,
                )
                return

            _LOGGER.info(
                "%s cleans up pending RemoteStartTransaction for idTag %s after %.1f seconds in %s",
                self.id,
                id_tag or "unknown",
                REMOTE_START_CLEANUP_DELAY,
                status,
            )
            self._clear_pending_session_metrics()
            await self.unlock()
            await self.trigger_status_notification()
            await asyncio.sleep(STALE_CONNECTOR_RESET_DELAY)
            if self.active_transaction_id != 0:
                return
            status = self._metrics[cstat.status_connector.value].value
            if status not in (
                ChargePointStatus.preparing.value,
                ChargePointStatus.suspended_ev.value,
                ChargePointStatus.suspended_evse.value,
            ):
                return

            _LOGGER.warning(
                "%s is still stuck in %s without a transaction after pending-start cleanup; sending soft reset",
                self.id,
                status,
            )
            await self.reset(ResetType.soft)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("%s failed to clean up pending remote start", self.id)
        finally:
            if self._remote_start_cleanup_task is asyncio.current_task():
                self._remote_start_cleanup_task = None
                self._remote_start_cleanup_id_tag = None

    async def reset(self, typ: str = ResetType.hard):
        """Hard reset charger unless soft reset requested."""
        self._metrics[cstat.reconnects.value].value = 0
        req = call.Reset(typ)
        resp = await self.call(req)
        if resp.status == ResetStatus.accepted:
            return True
        else:
            _LOGGER.warning("Failed with response: %s", resp.status)
            await self.notify_ha(f"Warning: Reset failed with response {resp.status}")
            return False

    async def unlock(self, connector_id: int = 1):
        """Unlock charger if requested."""
        req = call.UnlockConnector(connector_id)
        resp = await self.call(req)
        if resp.status == UnlockStatus.unlocked:
            return True
        else:
            _LOGGER.warning("Failed with response: %s", resp.status)
            await self.notify_ha(f"Warning: Unlock failed with response {resp.status}")
            return False

    async def update_firmware(self, firmware_url: str, wait_time: int = 0):
        """Update charger with new firmware if available."""
        """where firmware_url is the http or https url of the new firmware"""
        """and wait_time is hours from now to wait before install"""
        if prof.FW in self._attr_supported_features:
            schema = vol.Schema(vol.Url())
            try:
                url = schema(firmware_url)
            except vol.MultipleInvalid as e:
                _LOGGER.warning("Failed to parse firmware URL: %s", e)
                return False
            update_time = (
                datetime.now(tz=timezone.utc) + timedelta(hours=wait_time)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            req = call.UpdateFirmware(location=url, retrieve_date=update_time)
            resp = await self.call(req)
            _LOGGER.info("Response: %s", resp)
            return True
        else:
            _LOGGER.warning("Charger does not support ocpp firmware updating")
            return False

    async def get_diagnostics(self, upload_url: str):
        """Upload diagnostic data to server from charger."""
        if prof.FW in self._attr_supported_features:
            schema = vol.Schema(vol.Url())
            try:
                url = schema(upload_url)
            except vol.MultipleInvalid as e:
                _LOGGER.warning("Failed to parse url: %s", e)
                return False
            req = call.GetDiagnostics(location=url)
            resp = await self.call(req)
            _LOGGER.info("Response: %s", resp)
            return True
        else:
            _LOGGER.warning("Charger does not support ocpp diagnostics uploading")
            return False

    async def data_transfer(self, vendor_id: str, message_id: str = "", data: str = ""):
        """Request vendor specific data transfer from charger."""
        req = call.DataTransfer(vendor_id=vendor_id, message_id=message_id, data=data)
        resp = await self.call(req)
        if resp.status == DataTransferStatus.accepted:
            _LOGGER.info(
                "Data transfer [vendorId(%s), messageId(%s), data(%s)] response: %s",
                vendor_id,
                message_id,
                data,
                resp.data,
            )
            self._metrics[cdet.data_response.value].value = datetime.now(
                tz=timezone.utc
            )
            self._metrics[cdet.data_response.value].extra_attr = {message_id: resp.data}
            return True
        else:
            _LOGGER.warning("Failed with response: %s", resp.status)
            await self.notify_ha(
                f"Warning: Data transfer failed with response {resp.status}"
            )
            return False

    async def get_configuration(self, key: str = ""):
        """Get Configuration of charger for supported keys else return None."""
        if key == "":
            req = call.GetConfiguration()
        else:
            req = call.GetConfiguration(key=[key])
        resp = await self.call(req)
        if resp.configuration_key is not None:
            value = resp.configuration_key[0][om.value.value]
            _LOGGER.debug("Get Configuration for %s: %s", key, value)
            self._metrics[cdet.config_response.value].value = datetime.now(
                tz=timezone.utc
            )
            self._metrics[cdet.config_response.value].extra_attr = {key: value}
            return value
        if resp.unknown_key is not None:
            _LOGGER.warning("Get Configuration returned unknown key for: %s", key)
            await self.notify_ha(f"Warning: charger reports {key} is unknown")
            return None

    async def configure(self, key: str, value: str):
        """Configure charger by setting the key to target value.

        First the configuration key is read using GetConfiguration. The key's
        value is compared with the target value. If the key is already set to
        the correct value nothing is done.

        If the key has a different value a ChangeConfiguration request is issued.

        """
        req = call.GetConfiguration(key=[key])

        resp = await self.call(req)

        if resp.unknown_key is not None:
            if key in resp.unknown_key:
                _LOGGER.warning("%s is unknown (not supported)", key)
                return

        for key_value in resp.configuration_key:
            # If the key already has the targeted value we don't need to set
            # it.
            if key_value[om.key.value] == key and key_value[om.value.value] == value:
                return

            if key_value.get(om.readonly.name, False):
                _LOGGER.warning("%s is a read only setting", key)
                await self.notify_ha(f"Warning: {key} is read-only")
                return False

        req = call.ChangeConfiguration(key=key, value=value)

        resp = await self.call(req)

        if resp.status in [
            ConfigurationStatus.rejected,
            ConfigurationStatus.not_supported,
        ]:
            _LOGGER.warning("%s while setting %s to %s", resp.status, key, value)
            await self.notify_ha(
                f"Warning: charger reported {resp.status} while setting {key}={value}"
            )

        if resp.status == ConfigurationStatus.reboot_required:
            self._requires_reboot = True
            await self.notify_ha(f"A reboot is required to apply {key}={value}")
        return resp.status in (
            ConfigurationStatus.accepted,
            ConfigurationStatus.reboot_required,
        )

    async def _get_specific_response(self, unique_id, timeout):
        # The ocpp library silences CallErrors by default. See
        # https://github.com/mobilityhouse/ocpp/issues/104.
        # This code 'unsilences' CallErrors by raising them as exception
        # upon receiving.
        resp = await super()._get_specific_response(unique_id, timeout)

        if isinstance(resp, CallError):
            raise resp.to_exception()

        return resp

    async def monitor_connection(self):
        """Monitor the connection, by measuring the connection latency."""
        self._metrics[cstat.latency_ping.value].unit = "ms"
        self._metrics[cstat.latency_pong.value].unit = "ms"
        connection = self._connection
        timeout_counter = 0
        while connection.open:
            try:
                await asyncio.sleep(self.central.websocket_ping_interval)
                time0 = time.perf_counter()
                latency_ping = self.central.websocket_ping_timeout * 1000
                pong_waiter = await asyncio.wait_for(
                    connection.ping(), timeout=self.central.websocket_ping_timeout
                )
                time1 = time.perf_counter()
                latency_ping = round(time1 - time0, 3) * 1000
                latency_pong = self.central.websocket_ping_timeout * 1000
                await asyncio.wait_for(
                    pong_waiter, timeout=self.central.websocket_ping_timeout
                )
                timeout_counter = 0
                time2 = time.perf_counter()
                latency_pong = round(time2 - time1, 3) * 1000
                _LOGGER.debug(
                    f"Connection latency from '{self.central.csid}' to '{self.id}': ping={latency_ping} ms, pong={latency_pong} ms",
                )
                self._metrics[cstat.latency_ping.value].value = latency_ping
                self._metrics[cstat.latency_pong.value].value = latency_pong

            except asyncio.TimeoutError as timeout_exception:
                _LOGGER.debug(
                    f"Connection latency from '{self.central.csid}' to '{self.id}': ping={latency_ping} ms, pong={latency_pong} ms",
                )
                self._metrics[cstat.latency_ping.value].value = latency_ping
                self._metrics[cstat.latency_pong.value].value = latency_pong
                timeout_counter += 1
                if timeout_counter > self.central.websocket_ping_tries:
                    _LOGGER.debug(
                        f"Connection to '{self.id}' timed out after '{self.central.websocket_ping_tries}' ping tries",
                    )
                    raise timeout_exception
                else:
                    continue

    async def _handle_call(self, msg):
        try:
            await super()._handle_call(msg)
        except TypeConstraintViolationError:
            if truncate_status_notification_info(msg.action, msg.payload):
                _LOGGER.warning(
                    "%s sent StatusNotification.info longer than OCPP allows; "
                    "truncated to 50 characters",
                    self.id,
                )
                await super()._handle_call(msg)
                return
            raise
        except NotImplementedError as e:
            response = msg.create_call_error(e).to_json()
            await self._send(response)

    async def start(self):
        """Start charge point."""
        await self.run(
            [super().start(), self.post_connect(), self.monitor_connection()]
        )

    async def run(self, tasks):
        """Run a specified list of tasks."""
        self.tasks = [asyncio.ensure_future(task) for task in tasks]
        try:
            await asyncio.gather(*self.tasks)
        except asyncio.TimeoutError:
            pass
        except websockets.exceptions.WebSocketException as websocket_exception:
            _LOGGER.debug(f"Connection closed to '{self.id}': {websocket_exception}")
        except Exception as other_exception:
            _LOGGER.error(
                f"Unexpected exception in connection to '{self.id}': '{other_exception}'",
                exc_info=True,
            )
        finally:
            await self.stop()

    async def stop(self):
        """Close connection and cancel ongoing tasks."""
        self.status = STATE_UNAVAILABLE
        self._cancel_auto_stop()
        self._cancel_remote_start_cleanup()
        if self._connection.open:
            _LOGGER.debug(f"Closing websocket to '{self.id}'")
            await self._connection.close()
        if self.tasks is not None:
            for task in self.tasks:
                task.cancel()

    async def reconnect(self, connection: websockets.server.WebSocketServerProtocol):
        """Reconnect charge point."""
        _LOGGER.debug(f"Reconnect websocket to {self.id}")

        await self.stop()
        self.status = STATE_OK
        self._connection = connection
        self._metrics[cstat.reconnects.value].value += 1
        if self.post_connect_success is True:
            await self.run(
                [
                    super().start(),
                    self.monitor_connection(),
                    self._reconcile_after_reconnect(),
                ]
            )
        else:
            await self.run(
                [super().start(), self.post_connect(), self.monitor_connection()]
            )

    async def _reconcile_after_reconnect(self) -> None:
        """Reapply persisted charger controls once message handling is running."""
        await asyncio.sleep(0)
        try:
            await self.reconcile_charge_control_state()
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("%s failed to reconcile charger controls", self.id)

    async def async_update_device_info(self, boot_info: dict):
        """Update device info asynchronuously."""

        _LOGGER.debug("Updating device info %s: %s", self.central.cpid, boot_info)
        identifiers = {
            (DOMAIN, self.central.cpid),
            (DOMAIN, self.id),
        }
        serial = boot_info.get(om.charge_point_serial_number.name, None)
        if serial is not None:
            identifiers.add((DOMAIN, serial))

        registry = device_registry.async_get(self.hass)
        registry.async_get_or_create(
            config_entry_id=self.entry.entry_id,
            identifiers=identifiers,
            name=self.central.cpid,
            manufacturer=boot_info.get(om.charge_point_vendor.name, None),
            model=boot_info.get(om.charge_point_model.name, None),
            suggested_area="Garage",
            sw_version=boot_info.get(om.firmware_version.name, None),
        )

    def process_phases(self, data):
        """Process phase data from meter values ."""

        def average_of_nonzero(values):
            nonzero_values: list = [v for v in values if float(v) != 0.0]
            nof_values: int = len(nonzero_values)
            average = sum(nonzero_values) / nof_values if nof_values > 0 else 0
            return average

        measurand_data = {}
        for item in data:
            # create ordered Dict for each measurand, eg {"voltage":{"unit":"V","L1-N":"230"...}}
            measurand = item.get(om.measurand.value, None)
            phase = item.get(om.phase.value, None)
            value = item.get(om.value.value, None)
            unit = item.get(om.unit.value, None)
            context = item.get(om.context.value, None)
            if measurand is not None and phase is not None and unit is not None:
                if measurand not in measurand_data:
                    measurand_data[measurand] = {}
                measurand_data[measurand][om.unit.value] = unit
                measurand_data[measurand][phase] = float(value)
                self._metrics[measurand].unit = unit
                self._metrics[measurand].extra_attr[om.unit.value] = unit
                self._metrics[measurand].extra_attr[phase] = float(value)
                self._metrics[measurand].extra_attr[om.context.value] = context

        line_phases = [Phase.l1.value, Phase.l2.value, Phase.l3.value]
        line_to_neutral_phases = [Phase.l1_n.value, Phase.l2_n.value, Phase.l3_n.value]
        line_to_line_phases = [Phase.l1_l2.value, Phase.l2_l3.value, Phase.l3_l1.value]

        for metric, phase_info in measurand_data.items():
            metric_value = None
            if metric in [Measurand.voltage.value]:
                if not phase_info.keys().isdisjoint(line_to_neutral_phases):
                    # Line to neutral voltages are averaged
                    metric_value = average_of_nonzero(
                        [phase_info.get(phase, 0) for phase in line_to_neutral_phases]
                    )
                elif not phase_info.keys().isdisjoint(line_to_line_phases):
                    if (
                        not self._metrics[metric]
                        .extra_attr.keys()
                        .isdisjoint(line_to_neutral_phases)
                    ):
                        continue
                    # Line to line voltages are averaged and converted to line to neutral
                    metric_value = average_of_nonzero(
                        [phase_info.get(phase, 0) for phase in line_to_line_phases]
                    ) / sqrt(3)
                elif not phase_info.keys().isdisjoint(line_phases):
                    # Workaround for chargers that don't follow engineering convention
                    # Assumes voltages are line to neutral
                    metric_value = average_of_nonzero(
                        [phase_info.get(phase, 0) for phase in line_phases]
                    )
            else:
                if not phase_info.keys().isdisjoint(line_phases):
                    metric_value = sum(
                        phase_info.get(phase, 0) for phase in line_phases
                    )
                elif not phase_info.keys().isdisjoint(line_to_neutral_phases):
                    # Workaround for some chargers that erroneously use line to neutral for current
                    metric_value = sum(
                        phase_info.get(phase, 0) for phase in line_to_neutral_phases
                    )

            if metric_value is not None:
                metric_unit = phase_info.get(om.unit.value)
                _LOGGER.debug(
                    "process_phases: metric: %s, phase_info: %s value: %f unit :%s",
                    metric,
                    phase_info,
                    metric_value,
                    metric_unit,
                )
                if metric_unit == DEFAULT_POWER_UNIT:
                    self._metrics[metric].value = float(metric_value) / 1000
                    self._metrics[metric].unit = HA_POWER_UNIT
                elif metric_unit == DEFAULT_ENERGY_UNIT:
                    self._metrics[metric].value = float(metric_value) / 1000
                    self._metrics[metric].unit = HA_ENERGY_UNIT
                else:
                    self._metrics[metric].value = float(metric_value)
                    self._metrics[metric].unit = metric_unit

    @on(Action.meter_values)
    def on_meter_values(self, connector_id: int, meter_value: dict, **kwargs):
        """Request handler for MeterValues Calls."""

        transaction_id: int = kwargs.get(
            om.transaction_id.name, kwargs.get(om.transaction_id.value, 0)
        )
        contexts = set()

        # Restore transaction context before processing meter data. Never use the
        # current meter value as meter_start for an already active transaction.
        self._restore_active_transaction_id(transaction_id)
        self._restore_meter_start()
        self._restore_current_session_user()

        transaction_matches: bool = False
        # match is also false if no transaction is in progress ie active_transaction_id==transaction_id==0
        if transaction_id == self.active_transaction_id and transaction_id != 0:
            transaction_matches = True
        elif transaction_id != 0:
            _LOGGER.warning("Unknown transaction detected with id=%i", transaction_id)
        stopped_transaction = (
            transaction_id != 0
            and transaction_id != self.active_transaction_id
            and transaction_id in getattr(self, "_stopped_transaction_ids", set())
        )
        if stopped_transaction:
            _LOGGER.debug(
                "Processing delayed session energy for stopped transaction id=%s",
                transaction_id,
            )
            if self.active_transaction_id != 0 or transaction_id not in getattr(
                self, "_stopped_session_energy_transaction_ids", set()
            ):
                return call_result.MeterValues()
            for bucket in meter_value:
                sampled_values = bucket.get(
                    om.sampled_value.name,
                    bucket.get(om.sampled_value.value, []),
                )
                for sampled_value in sampled_values:
                    measurand = sampled_value.get(om.measurand.value, DEFAULT_MEASURAND)
                    if measurand != DEFAULT_MEASURAND:
                        continue
                    value = sampled_value.get(om.value.value)
                    if value is None:
                        continue
                    unit = sampled_value.get(om.unit.value, DEFAULT_ENERGY_UNIT)
                    if unit == DEFAULT_ENERGY_UNIT:
                        value = float(value) / 1000
                        unit = HA_ENERGY_UNIT
                    self._set_session_energy(float(value), unit)
            return call_result.MeterValues()

        for bucket in meter_value:
            unprocessed = bucket.get(om.sampled_value.name)
            if unprocessed is None:
                unprocessed = bucket.get(om.sampled_value.value, [])
            processed_keys = []
            for idx, sampled_value in enumerate(unprocessed):
                measurand = sampled_value.get(om.measurand.value, None)
                value = sampled_value.get(om.value.value, None)
                unit = sampled_value.get(om.unit.value, None)
                phase = sampled_value.get(om.phase.value, None)
                location = sampled_value.get(om.location.value, None)
                context = sampled_value.get(om.context.value, None)
                if context is not None:
                    contexts.add(context)

                if len(sampled_value.keys()) == 1:  # Backwards compatibility
                    measurand = DEFAULT_MEASURAND
                    unit = DEFAULT_ENERGY_UNIT

                if measurand == DEFAULT_MEASURAND and unit is None:
                    unit = DEFAULT_ENERGY_UNIT

                if self._metrics[csess.meter_start.value].value == 0:
                    # Charger reports Energy.Active.Import.Register directly as Session energy for transactions.
                    self._charger_reports_session_energy = True

                if phase is None:
                    if unit == DEFAULT_POWER_UNIT:
                        self._metrics[measurand].value = float(value) / 1000
                        self._metrics[measurand].unit = HA_POWER_UNIT
                    elif (
                        measurand == DEFAULT_MEASURAND
                        and self._charger_reports_session_energy
                    ):
                        if transaction_matches:
                            if unit == DEFAULT_ENERGY_UNIT:
                                value = float(value) / 1000
                                unit = HA_ENERGY_UNIT
                            self._set_session_energy(float(value), unit)
                        else:
                            if unit == DEFAULT_ENERGY_UNIT:
                                value = float(value) / 1000
                                unit = HA_ENERGY_UNIT
                            self._metrics[measurand].value = float(value)
                            self._metrics[measurand].unit = unit
                    elif unit == DEFAULT_ENERGY_UNIT:
                        value_kwh = float(value) / 1000
                        if transaction_matches:
                            meter_start = self._metrics[csess.meter_start.value].value
                            if (
                                measurand == DEFAULT_MEASURAND
                                and meter_start is not None
                                and value_kwh < float(meter_start)
                            ):
                                # Some chargers send an absolute StartTransaction
                                # meterStart but transaction MeterValues as a
                                # session-relative counter starting at zero.
                                self._charger_reports_session_energy = True
                                self._set_session_energy(value_kwh, HA_ENERGY_UNIT)
                            else:
                                self._metrics[measurand].value = value_kwh
                                self._metrics[measurand].unit = HA_ENERGY_UNIT
                        else:
                            self._metrics[measurand].value = value_kwh
                            self._metrics[measurand].unit = HA_ENERGY_UNIT
                    else:
                        value_float = float(value)
                        meter_start = self._metrics[csess.meter_start.value].value
                        if (
                            transaction_matches
                            and measurand == DEFAULT_MEASURAND
                            and unit == HA_ENERGY_UNIT
                            and meter_start is not None
                            and value_float < float(meter_start)
                        ):
                            self._charger_reports_session_energy = True
                            self._set_session_energy(value_float, unit)
                        else:
                            self._metrics[measurand].value = value_float
                            self._metrics[measurand].unit = unit
                    if location is not None:
                        self._metrics[measurand].extra_attr[
                            om.location.value
                        ] = location
                    if context is not None:
                        self._metrics[measurand].extra_attr[om.context.value] = context
                    processed_keys.append(idx)
            for idx in sorted(processed_keys, reverse=True):
                unprocessed.pop(idx)
            # _LOGGER.debug("Meter data not yet processed: %s", unprocessed)
            if unprocessed:
                self.process_phases(unprocessed)
        if transaction_matches:
            if "Interruption.Begin" in contexts:
                self._schedule_auto_stop_on_evse_suspended(
                    "MeterValues context=Interruption.Begin"
                )
            elif (
                self._metrics[cstat.status_connector.value].value
                == ChargePointStatus.suspended_evse.value
                and not self._has_active_import()
            ):
                self._schedule_auto_stop_on_evse_suspended(
                    "MeterValues while SuspendedEVSE"
                )
            elif self._has_active_import():
                self._cancel_auto_stop()
            self._metrics[csess.session_time.value].value = round(
                (
                    int(time.time())
                    - float(self._metrics[csess.transaction_id.value].value)
                )
                / 60
            )
            self._metrics[csess.session_time.value].unit = "min"
            if (
                self._metrics[csess.meter_start.value].value is not None
                and not self._charger_reports_session_energy
            ):
                self._set_session_energy(
                    float(self._metrics[DEFAULT_MEASURAND].value or 0)
                    - float(self._metrics[csess.meter_start.value].value)
                )
            if self.central.user_registry is not None:
                session_energy = self._metrics[csess.session_energy.value].value
                energy_price = None
                get_current_energy_price = getattr(
                    self.central, "get_current_energy_price", None
                )
                if get_current_energy_price is not None:
                    energy_price = get_current_energy_price(self.central.cpid)
                self.central.user_registry.record_session_energy(
                    transaction_id,
                    self.central.cpid,
                    float(session_energy) if session_energy is not None else None,
                    energy_price,
                )
        elif transaction_id != 0 and self.active_transaction_id == 0:
            for metric in (
                Measurand.current_import.value,
                Measurand.power_active_import.value,
                Measurand.power_reactive_import.value,
                Measurand.current_export.value,
                Measurand.power_active_export.value,
                Measurand.power_reactive_export.value,
            ):
                if metric in self._metrics:
                    self._metrics[metric].value = 0
        self.hass.async_create_task(self.central.update(self.central.cpid))
        return call_result.MeterValues()

    @on(Action.boot_notification)
    def on_boot_notification(self, **kwargs):
        """Handle a boot notification."""
        resp = call_result.BootNotification(
            current_time=datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            interval=3600,
            status=RegistrationStatus.accepted.value,
        )
        self.received_boot_notification = True
        _LOGGER.debug("Received boot notification for %s: %s", self.id, kwargs)
        # update metrics
        self._metrics[cdet.model.value].value = kwargs.get(
            om.charge_point_model.name, None
        )
        self._metrics[cdet.vendor.value].value = kwargs.get(
            om.charge_point_vendor.name, None
        )
        self._metrics[cdet.firmware_version.value].value = kwargs.get(
            om.firmware_version.name, None
        )
        self._metrics[cdet.serial.value].value = kwargs.get(
            om.charge_point_serial_number.name, None
        )

        self.hass.async_create_task(self.async_update_device_info(kwargs))
        self.hass.async_create_task(self.central.update(self.central.cpid))
        if self.triggered_boot_notification is False:
            self.hass.async_create_task(self.notify_ha(f"Charger {self.id} rebooted"))
            self.hass.async_create_task(
                self.post_connect(force=self.post_connect_success)
            )
        return resp

    @on(Action.status_notification)
    def on_status_notification(self, connector_id, error_code, status, **kwargs):
        """Handle a status notification."""

        if connector_id == 0 or connector_id is None:
            self._metrics[cstat.status.value].value = status
            self._metrics[cstat.error_code.value].value = error_code
        elif connector_id == 1:
            self._metrics[cstat.status_connector.value].value = status
            self._metrics[cstat.error_code_connector.value].value = error_code
            if (
                status == ChargePointStatus.available.value
                and self.active_transaction_id == 0
            ):
                self._cancel_remote_start_cleanup()
                self._clear_pending_session_metrics()
            elif (
                status
                in (
                    ChargePointStatus.preparing.value,
                    ChargePointStatus.suspended_ev.value,
                    ChargePointStatus.suspended_evse.value,
                )
                and self.active_transaction_id == 0
            ):
                self._schedule_remote_start_cleanup(
                    self._metrics[cstat.id_tag.value].value,
                    reset_existing=False,
                )
        if connector_id >= 1:
            self._metrics[cstat.status_connector.value].extra_attr[
                connector_id
            ] = status
            self._metrics[cstat.error_code_connector.value].extra_attr[
                connector_id
            ] = error_code
        if (
            status == ChargePointStatus.suspended_ev.value
            or status == ChargePointStatus.suspended_evse.value
        ):
            if Measurand.current_import.value in self._metrics:
                self._metrics[Measurand.current_import.value].value = 0
            if Measurand.power_active_import.value in self._metrics:
                self._metrics[Measurand.power_active_import.value].value = 0
            if Measurand.power_reactive_import.value in self._metrics:
                self._metrics[Measurand.power_reactive_import.value].value = 0
            if Measurand.current_export.value in self._metrics:
                self._metrics[Measurand.current_export.value].value = 0
            if Measurand.power_active_export.value in self._metrics:
                self._metrics[Measurand.power_active_export.value].value = 0
            if Measurand.power_reactive_export.value in self._metrics:
                self._metrics[Measurand.power_reactive_export.value].value = 0
        if status == ChargePointStatus.suspended_evse.value:
            self._schedule_auto_stop_on_evse_suspended(
                "StatusNotification SuspendedEVSE"
            )
        elif status in (
            ChargePointStatus.available.value,
            ChargePointStatus.charging.value,
            ChargePointStatus.finishing.value,
            ChargePointStatus.faulted.value,
            ChargePointStatus.unavailable.value,
        ):
            self._cancel_auto_stop()
            self._cancel_remote_start_cleanup()
        self.hass.async_create_task(self.central.update(self.central.cpid))
        return call_result.StatusNotification()

    @on(Action.firmware_status_notification)
    def on_firmware_status(self, status, **kwargs):
        """Handle firmware status notification."""
        self._metrics[cstat.firmware_status.value].value = status
        self.hass.async_create_task(self.central.update(self.central.cpid))
        self.hass.async_create_task(self.notify_ha(f"Firmware upload status: {status}"))
        return call_result.FirmwareStatusNotification()

    @on(Action.diagnostics_status_notification)
    def on_diagnostics_status(self, status, **kwargs):
        """Handle diagnostics status notification."""
        _LOGGER.info("Diagnostics upload status: %s", status)
        self.hass.async_create_task(
            self.notify_ha(f"Diagnostics upload status: {status}")
        )
        return call_result.DiagnosticsStatusNotification()

    @on(Action.security_event_notification)
    def on_security_event(self, type, timestamp, **kwargs):
        """Handle security event notification."""
        _LOGGER.info(
            "Security event notification received: %s at %s [techinfo: %s]",
            type,
            timestamp,
            kwargs.get(om.tech_info.name, "none"),
        )
        self.hass.async_create_task(
            self.notify_ha(f"Security event notification received: {type}")
        )
        return call_result.SecurityEventNotification()

    def get_authorization_status(self, id_tag):
        """Get the authorization status for an id_tag."""
        if self.central.user_registry is not None:
            auth_status = self.central.user_registry.get_authorization_status(id_tag)
            if auth_status is not None:
                _LOGGER.debug(
                    "id_tag='%s' found in user registry, authorization_status='%s'",
                    id_tag,
                    auth_status,
                )
                return auth_status

        # get the domain wide configuration
        config = self.hass.data[DOMAIN].get(CONFIG, {})
        # get the default authorization status. Use accept if not configured
        default_auth_status = AuthorizationStatus.accepted.value
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if CONF_DEFAULT_AUTH_STATUS in entry.options:
                default_auth_status = entry.options[CONF_DEFAULT_AUTH_STATUS]
                break
        else:
            default_auth_status = config.get(
                CONF_DEFAULT_AUTH_STATUS, AuthorizationStatus.accepted.value
            )
        # get the authorization list
        auth_list = config.get(CONF_AUTH_LIST, {})
        # search for the entry, based on the id_tag
        auth_status = None
        for auth_entry in auth_list:
            id_entry = auth_entry.get(CONF_ID_TAG, None)
            if id_tag == id_entry:
                # get the authorization status, use the default if not configured
                auth_status = auth_entry.get(CONF_AUTH_STATUS, default_auth_status)
                _LOGGER.debug(
                    f"id_tag='{id_tag}' found in auth_list, authorization_status='{auth_status}'"
                )
                break

        if auth_status is None:
            auth_status = default_auth_status
            _LOGGER.debug(
                f"id_tag='{id_tag}' not found in auth_list, default authorization_status='{auth_status}'"
            )
        return auth_status

    @staticmethod
    def current_month_period() -> str:
        """Return the current Home Assistant local month period."""
        return dt_util.now().strftime("%Y-%m")

    def ensure_current_monthly_energy(self) -> None:
        """Reset or restore the monthly wallbox energy counter."""
        period = self.current_month_period()
        metric = self._metrics[csess.monthly_energy.value]
        if metric.extra_attr.get("period") == period and metric.value is not None:
            return

        value = 0.0
        restored_period = self.get_ha_metric_attr(csess.monthly_energy.value, "period")
        restored_value = self.get_ha_metric(csess.monthly_energy.value)
        if restored_period == period and restored_value is not None:
            try:
                value = float(restored_value)
            except (TypeError, ValueError):
                value = 0.0

        metric.value = value
        metric.unit = UnitOfMeasure.kwh.value
        metric.extra_attr = {
            "period": period,
            "reset_cycle": "monthly",
        }

    def add_monthly_energy(self, session_energy_kwh: float | None) -> None:
        """Add one completed session to the monthly wallbox energy counter."""
        if session_energy_kwh is None or session_energy_kwh < 0:
            return
        self.ensure_current_monthly_energy()
        metric = self._metrics[csess.monthly_energy.value]
        metric.value = round(float(metric.value or 0.0) + session_energy_kwh, 6)

    def restore_current_user_from_session(self, transaction_id: int | str | None):
        """Restore current user state from the persisted registry session."""
        if self.central.user_registry is None:
            return
        get_session = getattr(self.central.user_registry, "get_session", None)
        if get_session is None:
            return
        session = get_session(self.central.cpid, transaction_id)
        if session is None:
            return
        user = self.central.user_registry.get_user(session["user_id"])
        if user is None:
            return

        id_tag = session.get("id_tag")
        self._metrics[cstat.id_tag.value].value = id_tag
        self._metrics[csess.current_user.value].value = user["name"]
        self._metrics[csess.current_user.value].extra_attr = {
            "id_tag": id_tag,
            "user_id": user["user_id"],
        }

    def restore_latest_active_session_from_registry(self) -> None:
        """Restore active transaction metadata from the latest persisted session."""
        if self.central.user_registry is None:
            return
        get_latest_session = getattr(
            self.central.user_registry, "get_latest_session", None
        )
        if get_latest_session is None:
            return
        session = get_latest_session(self.central.cpid)
        if session is None:
            return

        transaction_id = int(session["transaction_id"])
        self.active_transaction_id = transaction_id
        self._metrics[csess.transaction_id.value].value = transaction_id
        if session.get("meter_start_kwh") is not None:
            self._metrics[csess.meter_start.value].value = float(
                session["meter_start_kwh"]
            )
        self.restore_current_user_from_session(transaction_id)

    @on(Action.authorize)
    def on_authorize(self, id_tag, **kwargs):
        """Handle an Authorization request."""
        self._metrics[cstat.id_tag.value].value = id_tag
        auth_status = self.get_authorization_status(id_tag)
        return call_result.Authorize(id_tag_info={om.status.value: auth_status})

    @on(Action.start_transaction)
    def on_start_transaction(self, connector_id, id_tag, meter_start, **kwargs):
        """Handle a Start Transaction request."""

        auth_status = self.get_authorization_status(id_tag)
        if auth_status == AuthorizationStatus.accepted.value:
            user = None
            if self.central.user_registry is not None:
                user = self.central.user_registry.get_user_for_id_tag(id_tag)
            self._cancel_auto_stop()
            self._cancel_remote_start_cleanup()
            self.active_transaction_id = int(time.time())
            self._charger_reports_session_energy = False
            self._metrics[cstat.id_tag.value].value = id_tag
            self._metrics[cstat.stop_reason.value].value = ""
            self._metrics[csess.current_user.value].value = (
                user["name"] if user is not None else None
            )
            self._metrics[csess.current_user.value].extra_attr = {
                "id_tag": id_tag,
                "user_id": user["user_id"] if user is not None else None,
            }
            self._metrics[csess.transaction_id.value].value = self.active_transaction_id
            self._metrics[csess.session_energy.value].value = None
            meter_start_kwh = int(meter_start) / 1000
            self._metrics[csess.meter_start.value].value = meter_start_kwh
            if self.central.user_registry is not None:
                self.central.user_registry.record_start_transaction(
                    self.active_transaction_id,
                    id_tag,
                    self.central.cpid,
                    meter_start_kwh,
                )
            if (
                hasattr(self.central, "get_charge_mode")
                and self.central.get_charge_mode(self.central.cpid)
                == PRICE_OPTIMIZED_CHARGE_MODE_OPTIMIZED
                and not self.central.get_price_optimized_charging_allowed(
                    self.central.cpid
                )
            ):
                self.hass.async_create_task(self.pause_price_optimized_charging())
            result = call_result.StartTransaction(
                id_tag_info={om.status.value: AuthorizationStatus.accepted.value},
                transaction_id=self.active_transaction_id,
            )
        else:
            result = call_result.StartTransaction(
                id_tag_info={om.status.value: auth_status}, transaction_id=0
            )
        self.hass.async_create_task(self.central.update(self.central.cpid))
        return result

    @on(Action.stop_transaction)
    def on_stop_transaction(self, meter_stop, timestamp, transaction_id, **kwargs):
        """Stop the current transaction."""

        if transaction_id != self.active_transaction_id:
            _LOGGER.warning(
                "Ignoring StopTransaction for transaction id=%s while active id=%s",
                transaction_id,
                self.active_transaction_id,
            )
            stopped_transaction_ids = getattr(self, "_stopped_transaction_ids", set())
            stopped_transaction_ids.add(transaction_id)
            self._stopped_transaction_ids = stopped_transaction_ids
            return call_result.StopTransaction(
                id_tag_info={om.status.value: AuthorizationStatus.accepted.value}
            )
        stopped_transaction_ids = getattr(self, "_stopped_transaction_ids", set())
        stopped_transaction_ids.add(transaction_id)
        self._stopped_transaction_ids = stopped_transaction_ids
        self.active_transaction_id = 0
        self._cancel_auto_stop()
        self._cancel_remote_start_cleanup()
        self._price_pause_profile_applied = False
        meter_start = self._metrics[csess.meter_start.value].value
        charger_reports_session_energy = (
            self._charger_reports_session_energy or meter_start == 0
        )
        if charger_reports_session_energy:
            stopped_session_ids = getattr(
                self, "_stopped_session_energy_transaction_ids", set()
            )
            stopped_session_ids.add(transaction_id)
            self._stopped_session_energy_transaction_ids = stopped_session_ids
        self._metrics[cstat.id_tag.value].value = None
        self._metrics[csess.current_user.value].value = None
        self._metrics[csess.current_user.value].extra_attr = {}
        self._metrics[cstat.stop_reason.value].value = kwargs.get(om.reason.name, None)
        if (
            self._metrics[csess.meter_start.value].value is not None
            and not charger_reports_session_energy
        ):
            self._metrics[csess.session_energy.value].value = int(
                meter_stop
            ) / 1000 - float(self._metrics[csess.meter_start.value].value)
        session_energy = self._metrics[csess.session_energy.value].value
        self.add_monthly_energy(
            float(session_energy) if session_energy is not None else None
        )
        if Measurand.current_import.value in self._metrics:
            self._metrics[Measurand.current_import.value].value = 0
        if Measurand.power_active_import.value in self._metrics:
            self._metrics[Measurand.power_active_import.value].value = 0
        if Measurand.power_reactive_import.value in self._metrics:
            self._metrics[Measurand.power_reactive_import.value].value = 0
        if Measurand.current_export.value in self._metrics:
            self._metrics[Measurand.current_export.value].value = 0
        if Measurand.power_active_export.value in self._metrics:
            self._metrics[Measurand.power_active_export.value].value = 0
        if Measurand.power_reactive_export.value in self._metrics:
            self._metrics[Measurand.power_reactive_export.value].value = 0
        if self.central.user_registry is not None:
            energy_price = None
            get_current_energy_price = getattr(
                self.central, "get_current_energy_price", None
            )
            if get_current_energy_price is not None:
                energy_price = get_current_energy_price(self.central.cpid)
            self.central.user_registry.record_stop_transaction(
                transaction_id,
                self.central.cpid,
                int(meter_stop) / 1000,
                float(session_energy) if session_energy is not None else None,
                energy_price,
            )
        self._charger_reports_session_energy = False
        for metric in (
            csess.transaction_id.value,
            csess.meter_start.value,
            csess.session_time.value,
        ):
            self._metrics[metric].value = None
        self.hass.async_create_task(self.central.update(self.central.cpid))
        return call_result.StopTransaction(
            id_tag_info={om.status.value: AuthorizationStatus.accepted.value}
        )

    @on(Action.data_transfer)
    def on_data_transfer(self, vendor_id, **kwargs):
        """Handle a Data transfer request."""
        _LOGGER.debug("Data transfer received from %s: %s", self.id, kwargs)
        self._metrics[cdet.data_transfer.value].value = datetime.now(tz=timezone.utc)
        self._metrics[cdet.data_transfer.value].extra_attr = {vendor_id: kwargs}
        return call_result.DataTransfer(status=DataTransferStatus.accepted.value)

    @on(Action.heartbeat)
    def on_heartbeat(self, **kwargs):
        """Handle a Heartbeat."""
        now = datetime.now(tz=timezone.utc)
        self._metrics[cstat.heartbeat.value].value = now
        self.hass.async_create_task(self.central.update(self.central.cpid))
        return call_result.Heartbeat(current_time=now.strftime("%Y-%m-%dT%H:%M:%SZ"))

    @property
    def supported_features(self) -> int:
        """Flag of Ocpp features that are supported."""
        return self._attr_supported_features

    def get_metric(self, measurand: str):
        """Return last known value for given measurand."""
        return self._metrics[measurand].value

    def get_ha_metric(self, measurand: str):
        """Return last known value in HA for given measurand."""
        entity_id = "sensor." + "_".join(
            [self.central.cpid.lower(), measurand.lower().replace(".", "_")]
        )
        try:
            value = self.hass.states.get(entity_id).state
        except Exception as e:
            _LOGGER.debug(f"An error occurred when getting entity state from HA: {e}")
            return None
        if value == STATE_UNAVAILABLE or value == STATE_UNKNOWN:
            return None
        return value

    def get_ha_metric_attr(self, measurand: str, attr: str):
        """Return last known HA state attribute for given measurand."""
        entity_id = "sensor." + "_".join(
            [self.central.cpid.lower(), measurand.lower().replace(".", "_")]
        )
        try:
            state = self.hass.states.get(entity_id)
        except Exception as e:
            _LOGGER.debug(f"An error occurred when getting entity state from HA: {e}")
            return None
        if state is None:
            return None
        return state.attributes.get(attr)

    def get_extra_attr(self, measurand: str):
        """Return last known extra attributes for given measurand."""
        return self._metrics[measurand].extra_attr

    def get_unit(self, measurand: str):
        """Return unit of given measurand."""
        return self._metrics[measurand].unit

    def get_ha_unit(self, measurand: str):
        """Return home assistant unit of given measurand."""
        return self._metrics[measurand].ha_unit

    async def notify_ha(self, msg: str, title: str = "Ocpp integration"):
        """Notify user via HA web frontend."""
        # await self.hass.services.async_call(
        #    PN_DOMAIN,
        #    "create",
        #    service_data={
        #        "title": title,
        #        "message": msg,
        #    },
        #    blocking=False,
        # )

        # Send notification only to the log
        _LOGGER.info("Notification to HA skipped: %s", msg)

        return True


class Metric:
    """Metric class."""

    def __init__(self, value, unit):
        """Initialize a Metric."""
        self._value = value
        self._unit = unit
        self._extra_attr = {}

    @property
    def value(self):
        """Get the value of the metric."""
        return self._value

    @value.setter
    def value(self, value):
        """Set the value of the metric."""
        self._value = value

    @property
    def unit(self):
        """Get the unit of the metric."""
        return self._unit

    @unit.setter
    def unit(self, unit: str):
        """Set the unit of the metric."""
        self._unit = unit

    @property
    def ha_unit(self):
        """Get the home assistant unit of the metric."""
        return UNITS_OCCP_TO_HA.get(self._unit, self._unit)

    @property
    def extra_attr(self):
        """Get the extra attributes of the metric."""
        return self._extra_attr

    @extra_attr.setter
    def extra_attr(self, extra_attr: dict):
        """Set the unit of the metric."""
        self._extra_attr = extra_attr


def _service_charge_point(hass: HomeAssistant, service_call) -> ChargePoint | None:
    """Resolve one connected charger for a domain-level Home Assistant action."""
    requested_cp_id = service_call.data.get(CONF_CPID)
    candidates = []
    for runtime in hass.data.get(DOMAIN, {}).values():
        if not isinstance(runtime, CentralSystem):
            continue
        if requested_cp_id is not None and runtime.cpid != requested_cp_id:
            continue
        charge_point = runtime.charge_points.get(runtime.cpid)
        if charge_point is not None and charge_point.status != STATE_UNAVAILABLE:
            candidates.append(charge_point)

    if len(candidates) == 1:
        return candidates[0]
    if requested_cp_id is not None and not candidates:
        _LOGGER.warning("OCPP charger '%s' is not connected", requested_cp_id)
        return None
    if not candidates:
        _LOGGER.warning("No OCPP charger is connected")
        return None
    raise HomeAssistantError(
        "Multiple OCPP chargers are connected; specify the cpid field"
    )


async def async_setup_charge_point_services(hass: HomeAssistant) -> None:
    """Register charger actions once and route them by configured cpid."""
    if hass.services.has_service(DOMAIN, csvcs.service_configure.value):
        return

    async def handle_clear_profile(service_call):
        if (charge_point := _service_charge_point(hass, service_call)) is not None:
            await charge_point.clear_profile()

    async def handle_update_firmware(service_call):
        charge_point = _service_charge_point(hass, service_call)
        if charge_point is None:
            return
        await charge_point.update_firmware(
            service_call.data["firmware_url"],
            int(service_call.data.get("delay_hours", 0)),
        )

    async def handle_configure(service_call):
        charge_point = _service_charge_point(hass, service_call)
        if charge_point is None:
            return
        await charge_point.configure(
            service_call.data["ocpp_key"], service_call.data["value"]
        )

    async def handle_get_configuration(service_call):
        if (charge_point := _service_charge_point(hass, service_call)) is not None:
            await charge_point.get_configuration(service_call.data["ocpp_key"])

    async def handle_get_diagnostics(service_call):
        if (charge_point := _service_charge_point(hass, service_call)) is not None:
            await charge_point.get_diagnostics(service_call.data["upload_url"])

    async def handle_data_transfer(service_call):
        charge_point = _service_charge_point(hass, service_call)
        if charge_point is None:
            return
        await charge_point.data_transfer(
            service_call.data["vendor_id"],
            service_call.data.get("message_id", ""),
            service_call.data.get("data", ""),
        )

    async def handle_set_charge_rate(service_call):
        charge_point = _service_charge_point(hass, service_call)
        if charge_point is None:
            return
        connector_id = service_call.data.get("conn_id", 0)
        custom_profile = service_call.data.get("custom_profile")
        if isinstance(custom_profile, str):
            custom_profile = json.loads(custom_profile.replace("'", '"'))
        if custom_profile is not None:
            await charge_point.set_charge_rate(
                profile=custom_profile, conn_id=connector_id
            )
        elif "limit_watts" in service_call.data:
            await charge_point.set_charge_rate(
                limit_watts=service_call.data["limit_watts"],
                conn_id=connector_id,
            )
        elif "limit_amps" in service_call.data:
            await charge_point.set_charge_rate(
                limit_amps=service_call.data["limit_amps"],
                conn_id=connector_id,
            )
        else:
            raise HomeAssistantError(
                "Specify limit_amps, limit_watts, or custom_profile"
            )

    hass.services.async_register(
        DOMAIN,
        csvcs.service_configure.value,
        handle_configure,
        CONF_SERVICE_DATA_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        csvcs.service_get_configuration.value,
        handle_get_configuration,
        GCONF_SERVICE_DATA_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        csvcs.service_data_transfer.value,
        handle_data_transfer,
        TRANS_SERVICE_DATA_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, csvcs.service_clear_profile.value, handle_clear_profile
    )
    hass.services.async_register(
        DOMAIN,
        csvcs.service_set_charge_rate.value,
        handle_set_charge_rate,
        CHRGR_SERVICE_DATA_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        csvcs.service_update_firmware.value,
        handle_update_firmware,
        UFW_SERVICE_DATA_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        csvcs.service_get_diagnostics.value,
        handle_get_diagnostics,
        GDIAG_SERVICE_DATA_SCHEMA,
    )
