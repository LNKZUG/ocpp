"""Sensor platform for ocpp."""
from __future__ import annotations

from dataclasses import dataclass

import homeassistant
from homeassistant.components.sensor import (
    DOMAIN as SENSOR_DOMAIN,
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import CONF_MONITORED_VARIABLES
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.util import slugify

from .api import CentralSystem
from .const import (
    CONF_CPID,
    DATA_UPDATED,
    DATA_USERS_UPDATED,
    DEFAULT_CLASS_UNITS_HA,
    DEFAULT_CPID,
    DOMAIN,
    ICON,
    Measurand,
    USER_SENSOR_IDS,
    USER_SENSOR_SETUP_DONE,
)
from .enums import HAChargerDetails, HAChargerSession, HAChargerStatuses
from .user_registry import (
    USER_SENSOR_DEVICE_CLASS,
    USER_SENSOR_STATE_CLASS,
    USER_SENSOR_UNIT,
    async_get_user_registry,
)


@dataclass
class OcppSensorDescription(SensorEntityDescription):
    """Class to describe a Sensor entity."""

    metric: str | None = None


STATUS_TRANSLATION_OPTIONS = [
    "Available",
    "Unavailable",
    "Finishing",
    "Charging",
    "SuspendedEV",
    "SuspendedEVSE",
    "Preparing",
    "Reserved",
    "Faulted",
]


def metric_translation_key(metric: str) -> str:
    """Return the HA translation key for an OCPP metric."""
    return slugify(metric.replace(".", "_")).replace("-", "_")


async def async_setup_entry(hass, entry, async_add_devices):
    """Configure the sensor platform."""
    central_system = hass.data[DOMAIN][entry.entry_id]
    cp_id = entry.data.get(CONF_CPID, DEFAULT_CPID)
    entities = []
    SENSORS = []
    for metric in list(
        set(entry.data[CONF_MONITORED_VARIABLES].split(",") + list(HAChargerSession))
    ):
        SENSORS.append(
            OcppSensorDescription(
                key=metric.lower(),
                metric=metric,
                translation_key=metric_translation_key(metric),
            )
        )
    for metric in list(HAChargerStatuses) + list(HAChargerDetails):
        SENSORS.append(
            OcppSensorDescription(
                key=metric.lower(),
                metric=metric,
                translation_key=metric_translation_key(metric),
                entity_category=EntityCategory.DIAGNOSTIC,
            )
        )

    for ent in SENSORS:
        entities.append(
            ChargePointMetric(
                hass,
                central_system,
                cp_id,
                ent,
            )
        )

    async_add_devices(entities, False)

    registry = await async_get_user_registry(hass)
    if not hass.data[DOMAIN].get(USER_SENSOR_SETUP_DONE):
        hass.data[DOMAIN][USER_SENSOR_SETUP_DONE] = True
        hass.data[DOMAIN][USER_SENSOR_IDS] = set()

        @callback
        def add_missing_user_sensors():
            known_user_ids = hass.data[DOMAIN][USER_SENSOR_IDS]
            new_entities = []
            for user in registry.list_users():
                user_id = user["user_id"]
                if user_id in known_user_ids:
                    continue
                known_user_ids.add(user_id)
                new_entities.append(UserEnergySensor(hass, registry, user_id))
            if new_entities:
                async_add_devices(new_entities, False)

        add_missing_user_sensors()
        entry.async_on_unload(
            async_dispatcher_connect(
                hass, DATA_USERS_UPDATED, add_missing_user_sensors
            )
        )

        @callback
        def reset_user_sensor_setup():
            hass.data[DOMAIN].pop(USER_SENSOR_SETUP_DONE, None)
            hass.data[DOMAIN].pop(USER_SENSOR_IDS, None)

        entry.async_on_unload(reset_user_sensor_setup)


class ChargePointMetric(RestoreSensor, SensorEntity):
    """Individual sensor for charge point metrics."""

    _attr_has_entity_name = True
    entity_description: OcppSensorDescription

    def __init__(
        self,
        hass: HomeAssistant,
        central_system: CentralSystem,
        cp_id: str,
        description: OcppSensorDescription,
    ):
        """Instantiate instance of a ChargePointMetrics."""
        self.central_system = central_system
        self.cp_id = cp_id
        self.entity_description = description
        self.metric = self.entity_description.metric
        self._hass = hass
        self._extra_attr = {}
        self._last_reset = homeassistant.util.dt.utc_from_timestamp(0)
        self._attr_unique_id = ".".join(
            [DOMAIN, self.cp_id, self.entity_description.key, SENSOR_DOMAIN]
        )
        self._attr_name = self.entity_description.name
        self._attr_translation_key = self.entity_description.translation_key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.cp_id)},
            via_device=(DOMAIN, self.central_system.id),
        )
        self._attr_icon = ICON
        self._attr_native_unit_of_measurement = None

    @property
    def available(self) -> bool:
        """Return if sensor is available."""
        return self.central_system.get_available(self.cp_id)

    @property
    def should_poll(self):
        """Return True if entity has to be polled for state.

        False if entity pushes its state to HA.
        """
        return True

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return self.central_system.get_extra_attr(self.cp_id, self.metric)

    @property
    def state_class(self):
        """Return the state class of the sensor."""
        state_class = None
        if self.device_class is SensorDeviceClass.ENERGY:
            state_class = SensorStateClass.TOTAL_INCREASING
        elif self.device_class in [
            SensorDeviceClass.CURRENT,
            SensorDeviceClass.VOLTAGE,
            SensorDeviceClass.POWER,
            SensorDeviceClass.TEMPERATURE,
            SensorDeviceClass.BATTERY,
            SensorDeviceClass.FREQUENCY,
        ] or self.metric in [
            HAChargerStatuses.latency_ping.value,
            HAChargerStatuses.latency_pong.value,
        ]:
            state_class = SensorStateClass.MEASUREMENT

        return state_class

    @property
    def device_class(self):
        """Return the device class of the sensor."""
        device_class = None
        if self.metric.lower().startswith("current."):
            device_class = SensorDeviceClass.CURRENT
        elif self.metric.lower().startswith("voltage"):
            device_class = SensorDeviceClass.VOLTAGE
        elif self.metric.lower().startswith("energy."):
            device_class = SensorDeviceClass.ENERGY
        elif self.metric in [
            Measurand.frequency,
            Measurand.rpm,
        ] or self.metric.lower().startswith("frequency"):
            device_class = SensorDeviceClass.FREQUENCY
        elif self.metric.lower().startswith(tuple(["power.a", "power.o", "power.r"])):
            device_class = SensorDeviceClass.POWER
        elif self.metric.lower().startswith("temperature."):
            device_class = SensorDeviceClass.TEMPERATURE
        elif self.metric.lower().startswith("timestamp.") or self.metric in [
            HAChargerDetails.config_response.value,
            HAChargerDetails.data_response.value,
            HAChargerStatuses.heartbeat.value,
        ]:
            device_class = SensorDeviceClass.TIMESTAMP
        elif self.metric.lower().startswith("soc"):
            device_class = SensorDeviceClass.BATTERY
        elif self.metric in [
            HAChargerStatuses.status.value,
            HAChargerStatuses.status_connector.value,
        ]:
            device_class = SensorDeviceClass.ENUM
        return device_class

    @property
    def options(self):
        """Return possible enum states for translated status sensors."""
        if self.device_class is SensorDeviceClass.ENUM:
            return STATUS_TRANSLATION_OPTIONS
        return None

    @property
    def native_value(self):
        """Return the state of the sensor, rounding if a number."""
        value = self.central_system.get_metric(self.cp_id, self.metric)
        if value is not None:
            self._attr_native_value = value
        return self._attr_native_value

    @property
    def native_unit_of_measurement(self):
        """Return the native unit of measurement."""
        value = self.central_system.get_ha_unit(self.cp_id, self.metric)
        if value is not None:
            self._attr_native_unit_of_measurement = value
        else:
            self._attr_native_unit_of_measurement = DEFAULT_CLASS_UNITS_HA.get(
                self.device_class
            )
        return self._attr_native_unit_of_measurement

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()
        if restored := await self.async_get_last_sensor_data():
            self._attr_native_value = restored.native_value
            self._attr_native_unit_of_measurement = restored.native_unit_of_measurement

        async_dispatcher_connect(
            self._hass, DATA_UPDATED, self._schedule_immediate_update
        )

    @callback
    def _schedule_immediate_update(self):
        self.async_schedule_update_ha_state(True)


class UserEnergySensor(SensorEntity):
    """Total OCPP charging energy for one managed user."""

    _attr_has_entity_name = False
    _attr_device_class = USER_SENSOR_DEVICE_CLASS
    _attr_icon = ICON
    _attr_native_unit_of_measurement = USER_SENSOR_UNIT
    _attr_state_class = USER_SENSOR_STATE_CLASS

    def __init__(self, hass: HomeAssistant, registry, user_id: str):
        """Initialize a user energy sensor."""
        self._hass = hass
        self.registry = registry
        self.user_id = user_id
        self._attr_unique_id = f"{DOMAIN}_user_{user_id}_energy"

    @property
    def user(self):
        """Return the registry user for this sensor."""
        return self.registry.get_user(self.user_id)

    @property
    def name(self):
        """Return the entity name."""
        user = self.user
        if user is None:
            return "OCPP unknown user Ladeenergie"
        return f"OCPP {user['name']} Ladeenergie"

    @property
    def available(self) -> bool:
        """Return whether the user still exists."""
        return self.user is not None

    @property
    def native_value(self):
        """Return accumulated charging energy."""
        user = self.user
        if user is None:
            return None
        return user.get("energy_kwh", 0.0)

    @property
    def device_info(self):
        """Return device info for the managed user."""
        user = self.user
        user_name = user["name"] if user is not None else self.user_id
        return DeviceInfo(
            identifiers={(DOMAIN, f"user_{self.user_id}")},
            name=f"OCPP {user_name}",
            model="OCPP User",
        )

    @property
    def extra_state_attributes(self):
        """Return user metadata."""
        user = self.user
        if user is None:
            return {}
        return {
            "active": user.get("active", True),
            "id_tags": user.get("id_tags", []),
            "last_session_energy_kwh": user.get("last_session_energy_kwh"),
            "last_session_finished_at": user.get("last_session_finished_at"),
        }

    async def async_added_to_hass(self) -> None:
        """Handle entity addition."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self._hass, DATA_USERS_UPDATED, self._schedule_immediate_update
            )
        )

    @callback
    def _schedule_immediate_update(self):
        self.async_schedule_update_ha_state(True)
