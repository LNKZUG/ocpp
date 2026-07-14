"""Sensor platform for ocpp."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

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

from .api import PRICE_OPTIMIZED_CHARGE_STATUS, CentralSystem
from .const import (
    CONF_CPID,
    DATA_UPDATED,
    DATA_USERS_UPDATED,
    DEFAULT_CLASS_UNITS_HA,
    DEFAULT_CPID,
    DEFAULT_DISABLED_METRICS,
    DOMAIN,
    ENTRY_TYPE,
    ENTRY_TYPE_USERS,
    ICON,
    USER_SENSOR_IDS,
    USER_SENSOR_SETUP_DONE,
    Measurand,
)
from .enums import HAChargerDetails, HAChargerSession, HAChargerStatuses
from .user_registry import (
    USER_COST_SENSOR_DEVICE_CLASS,
    USER_COST_SENSOR_UNIT,
    USER_MONTHLY_SENSOR_STATE_CLASS,
    USER_SENSOR_DEVICE_CLASS,
    USER_SENSOR_STATE_CLASS,
    USER_SENSOR_UNIT,
    async_get_user_registry,
)


@dataclass
class OcppSensorDescription(SensorEntityDescription):
    """Class to describe a Sensor entity."""

    metric: str | None = None
    enabled_default: bool = True


STATUS_TRANSLATION_KEYS = {
    "Available": "available",
    "Unavailable": "unavailable",
    "Finishing": "finishing",
    "Charging": "charging",
    "SuspendedEV": "suspended_ev",
    "SuspendedEVSE": "suspended_evse",
    "Preparing": "preparing",
    "Reserved": "reserved",
    "Faulted": "faulted",
    PRICE_OPTIMIZED_CHARGE_STATUS: "price_optimized_charging_paused",
}

STATUS_TRANSLATION_OPTIONS = list(STATUS_TRANSLATION_KEYS.values())


def metric_translation_key(metric: str) -> str:
    """Return the HA translation key for an OCPP metric."""
    return slugify(metric.replace(".", "_")).replace("-", "_")


def metric_display_name(metric: str) -> str:
    """Return a readable fallback name for an OCPP metric."""
    words = metric.replace(".", " ").replace("_", " ").split()
    replacements = {
        "id": "ID",
        "rpm": "RPM",
        "soc": "SoC",
    }
    return " ".join(replacements.get(word.lower(), word.title()) for word in words)


async def async_setup_entry(hass, entry, async_add_devices):
    """Configure the sensor platform."""
    if entry.data.get(ENTRY_TYPE) == ENTRY_TYPE_USERS:
        await async_setup_user_sensors(hass, entry, async_add_devices)
        return

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
                name=metric_display_name(metric),
                metric=metric,
                translation_key=metric_translation_key(metric),
                enabled_default=metric not in DEFAULT_DISABLED_METRICS,
            )
        )
    for metric in list(HAChargerStatuses) + list(HAChargerDetails):
        SENSORS.append(
            OcppSensorDescription(
                key=metric.lower(),
                name=metric_display_name(metric),
                metric=metric,
                translation_key=metric_translation_key(metric),
                entity_category=EntityCategory.DIAGNOSTIC,
                enabled_default=metric not in DEFAULT_DISABLED_METRICS,
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


async def async_setup_user_sensors(hass, entry, async_add_devices):
    """Set up global OCPP user sensors."""
    registry = await async_get_user_registry(hass)
    if not hass.data[DOMAIN].get(USER_SENSOR_SETUP_DONE):
        hass.data[DOMAIN][USER_SENSOR_SETUP_DONE] = True
        hass.data[DOMAIN][USER_SENSOR_IDS] = set()
        async_add_devices(
            [
                UserOverviewSensor(hass, registry),
                UserCountSensor(hass, registry, "active", "OCPP Benutzer aktiv"),
                UserCountSensor(hass, registry, "blocked", "OCPP Benutzer gesperrt"),
            ],
            False,
        )

        @callback
        def add_missing_user_sensors():
            known_user_ids = hass.data[DOMAIN][USER_SENSOR_IDS]
            new_entities = []
            for user in registry.list_users():
                user_id = user["user_id"]
                if user_id in known_user_ids:
                    continue
                known_user_ids.add(user_id)
                new_entities.extend(user_entities(hass, registry, user_id))
            if new_entities:
                async_add_devices(new_entities, False)

        add_missing_user_sensors()
        entry.async_on_unload(
            async_dispatcher_connect(hass, DATA_USERS_UPDATED, add_missing_user_sensors)
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
        self._attr_translation_key = self.entity_description.translation_key
        self._attr_entity_registry_enabled_default = (
            self.entity_description.enabled_default
        )
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
        attributes = (
            self.central_system.get_extra_attr(self.cp_id, self.metric)
            or self._extra_attr
            or {}
        )
        if self.metric == HAChargerSession.monthly_energy.value:
            period = homeassistant.util.dt.now().strftime("%Y-%m")
            if attributes.get("period") != period:
                return {
                    "period": period,
                    "reset_cycle": "monthly",
                }
        return attributes

    @property
    def state_class(self):
        """Return the state class of the sensor."""
        state_class = None
        if self.metric == HAChargerSession.monthly_energy.value:
            state_class = SensorStateClass.TOTAL
        elif self.device_class is SensorDeviceClass.ENERGY:
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
        if self.metric == HAChargerSession.current_user.value:
            device_class = None
        elif self.metric.lower().startswith("current."):
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
        if self.metric in (
            HAChargerStatuses.status.value,
            HAChargerStatuses.status_connector.value,
        ) and self.central_system.is_price_optimized_charging_paused(self.cp_id):
            self._attr_native_value = STATUS_TRANSLATION_KEYS[
                PRICE_OPTIMIZED_CHARGE_STATUS
            ]
            return self._attr_native_value
        if (
            self.metric
            in (
                HAChargerSession.current_user.value,
                HAChargerSession.transaction_id.value,
                HAChargerSession.meter_start.value,
                HAChargerSession.session_energy.value,
                HAChargerSession.session_time.value,
                HAChargerStatuses.id_tag.value,
            )
            and value is None
        ):
            self._attr_native_value = None
            return None
        if self.metric == HAChargerSession.monthly_energy.value:
            attributes = (
                self.central_system.get_extra_attr(self.cp_id, self.metric)
                or self._extra_attr
                or {}
            )
            period = homeassistant.util.dt.now().strftime("%Y-%m")
            if attributes.get("period") != period:
                self._attr_native_value = 0.0
                return self._attr_native_value
        if value is not None:
            if self.device_class is SensorDeviceClass.ENUM:
                value = STATUS_TRANSLATION_KEYS.get(value, value)
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
        if self.metric == HAChargerSession.monthly_energy.value:
            if restored_state := await self.async_get_last_state():
                self._extra_attr = dict(restored_state.attributes)

        async_dispatcher_connect(
            self._hass, DATA_UPDATED, self._schedule_immediate_update
        )

    @callback
    def _schedule_immediate_update(self):
        self.async_schedule_update_ha_state(True)


def user_entities(hass: HomeAssistant, registry, user_id: str) -> list[SensorEntity]:
    """Return all entities for one managed OCPP user."""
    return [
        UserEnergySensor(hass, registry, user_id),
        UserMonthlyEnergySensor(hass, registry, user_id),
        UserMonthlyCostSensor(hass, registry, user_id),
        UserStatusSensor(hass, registry, user_id),
        UserIdTagsSensor(hass, registry, user_id),
        UserLastSessionEnergySensor(hass, registry, user_id),
        UserLastSessionFinishedSensor(hass, registry, user_id),
    ]


class UserSensorBase(SensorEntity):
    """Base entity for one managed OCPP user."""

    _attr_has_entity_name = False

    def __init__(self, hass: HomeAssistant, registry, user_id: str):
        """Initialize a user sensor."""
        self._hass = hass
        self.registry = registry
        self.user_id = user_id

    @property
    def user(self):
        """Return the registry user for this sensor."""
        return self.registry.get_user(self.user_id)

    @property
    def name(self):
        """Return the entity name."""
        user = self.user
        suffix = getattr(self, "_name_suffix", "")
        if user is None:
            return f"OCPP unknown user {suffix}".strip()
        return f"OCPP {user['name']} {suffix}".strip()

    @property
    def available(self) -> bool:
        """Return whether the user still exists."""
        return self.user is not None

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


class UserEnergySensor(UserSensorBase):
    """Total OCPP charging energy for one managed user."""

    _attr_device_class = USER_SENSOR_DEVICE_CLASS
    _attr_icon = ICON
    _attr_native_unit_of_measurement = USER_SENSOR_UNIT
    _attr_state_class = USER_SENSOR_STATE_CLASS
    _name_suffix = "Ladeenergie"

    def __init__(self, hass: HomeAssistant, registry, user_id: str):
        """Initialize a user energy sensor."""
        super().__init__(hass, registry, user_id)
        self._attr_unique_id = f"{DOMAIN}_user_{user_id}_energy"

    @property
    def native_value(self):
        """Return accumulated charging energy."""
        user = self.user
        if user is None:
            return None
        return user.get("energy_kwh", 0.0)


class UserMonthlyEnergySensor(UserSensorBase):
    """Monthly OCPP charging energy for one managed user."""

    _attr_device_class = USER_SENSOR_DEVICE_CLASS
    _attr_icon = ICON
    _attr_native_unit_of_measurement = USER_SENSOR_UNIT
    _attr_state_class = USER_MONTHLY_SENSOR_STATE_CLASS
    _name_suffix = "Ladeenergie Monat"

    def __init__(self, hass: HomeAssistant, registry, user_id: str):
        """Initialize a user monthly energy sensor."""
        super().__init__(hass, registry, user_id)
        self._attr_unique_id = f"{DOMAIN}_user_{user_id}_monthly_energy"

    @property
    def native_value(self):
        """Return charging energy for the current local month."""
        user = self.user
        if user is None:
            return None
        if user.get("monthly_energy_period") != self.registry.current_month_period():
            return 0.0
        return user.get("monthly_energy_kwh", 0.0)

    @property
    def extra_state_attributes(self):
        """Return monthly billing period metadata."""
        user = self.user
        if user is None:
            return {}
        return {
            "period": self.registry.current_month_period(),
            "reset_cycle": "monthly",
        }


class UserMonthlyCostSensor(UserSensorBase):
    """Monthly OCPP charging cost for one managed user."""

    _attr_device_class = USER_COST_SENSOR_DEVICE_CLASS
    _attr_icon = "mdi:cash"
    _attr_native_unit_of_measurement = USER_COST_SENSOR_UNIT
    _attr_state_class = USER_MONTHLY_SENSOR_STATE_CLASS
    _name_suffix = "Ladekosten Monat"

    def __init__(self, hass: HomeAssistant, registry, user_id: str):
        """Initialize a user monthly cost sensor."""
        super().__init__(hass, registry, user_id)
        self._attr_unique_id = f"{DOMAIN}_user_{user_id}_monthly_cost"

    @property
    def native_value(self):
        """Return charging cost for the current local month."""
        user = self.user
        if user is None:
            return None
        if user.get("monthly_energy_period") != self.registry.current_month_period():
            return 0.0
        return user.get("monthly_cost", 0.0)

    @property
    def extra_state_attributes(self):
        """Return monthly billing period metadata."""
        user = self.user
        if user is None:
            return {}
        return {
            "period": self.registry.current_month_period(),
            "reset_cycle": "monthly",
        }


class UserStatusSensor(UserSensorBase):
    """Authorization status for one managed OCPP user."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_icon = "mdi:account-check"
    _attr_options = ["Accepted", "Blocked"]
    _name_suffix = "Status"

    def __init__(self, hass: HomeAssistant, registry, user_id: str):
        """Initialize a user status sensor."""
        super().__init__(hass, registry, user_id)
        self._attr_unique_id = f"{DOMAIN}_user_{user_id}_status"

    @property
    def native_value(self):
        """Return accepted or blocked status."""
        user = self.user
        if user is None:
            return None
        return "Accepted" if user.get("active", True) else "Blocked"


class UserIdTagsSensor(UserSensorBase):
    """Assigned idTags for one managed OCPP user."""

    _attr_icon = "mdi:identifier"
    _name_suffix = "idTags"

    def __init__(self, hass: HomeAssistant, registry, user_id: str):
        """Initialize a user idTags sensor."""
        super().__init__(hass, registry, user_id)
        self._attr_unique_id = f"{DOMAIN}_user_{user_id}_id_tags"

    @property
    def native_value(self):
        """Return assigned idTags as display text."""
        user = self.user
        if user is None:
            return None
        return ", ".join(user.get("id_tags", []))

    @property
    def extra_state_attributes(self):
        """Return assigned idTags as structured data."""
        user = self.user
        if user is None:
            return {}
        return {"id_tags": user.get("id_tags", [])}


class UserLastSessionEnergySensor(UserSensorBase):
    """Last session energy for one managed OCPP user."""

    _attr_device_class = USER_SENSOR_DEVICE_CLASS
    _attr_icon = ICON
    _attr_native_unit_of_measurement = USER_SENSOR_UNIT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _name_suffix = "letzte Ladung"

    def __init__(self, hass: HomeAssistant, registry, user_id: str):
        """Initialize a user last-session energy sensor."""
        super().__init__(hass, registry, user_id)
        self._attr_unique_id = f"{DOMAIN}_user_{user_id}_last_session_energy"

    @property
    def native_value(self):
        """Return last charging session energy."""
        user = self.user
        if user is None:
            return None
        return user.get("last_session_energy_kwh")


class UserLastSessionFinishedSensor(UserSensorBase):
    """Last session finish timestamp for one managed OCPP user."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-check-outline"
    _name_suffix = "letztes Ladeende"

    def __init__(self, hass: HomeAssistant, registry, user_id: str):
        """Initialize a user last-session finished sensor."""
        super().__init__(hass, registry, user_id)
        self._attr_unique_id = f"{DOMAIN}_user_{user_id}_last_session_finished"

    @property
    def native_value(self):
        """Return last charging session finish timestamp."""
        user = self.user
        if user is None:
            return None
        timestamp = user.get("last_session_finished_at")
        if timestamp is None:
            return None
        return datetime.fromtimestamp(float(timestamp), tz=timezone.utc)


class UserCountSensor(SensorEntity):
    """Count managed OCPP users by state."""

    _attr_has_entity_name = False
    _attr_icon = "mdi:account-multiple"

    def __init__(self, hass: HomeAssistant, registry, key: str, name: str):
        """Initialize a user count sensor."""
        self._hass = hass
        self.registry = registry
        self.key = key
        self._attr_name = name
        self._attr_unique_id = f"{DOMAIN}_users_{key}"

    @property
    def device_info(self):
        """Return device info for the user overview."""
        return DeviceInfo(
            identifiers={(DOMAIN, "users")},
            name="OCPP Benutzer",
            model="OCPP User Registry",
        )

    @property
    def native_value(self):
        """Return a count of users."""
        users = self.registry.list_users()
        if self.key == "active":
            return sum(1 for user in users if user.get("active", True))
        if self.key == "blocked":
            return sum(1 for user in users if not user.get("active", True))
        return len(users)

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


class UserOverviewSensor(SensorEntity):
    """Overview of all managed OCPP users."""

    _attr_has_entity_name = False
    _attr_icon = "mdi:account-group"
    _attr_name = "OCPP Benutzer"
    _attr_unique_id = f"{DOMAIN}_users_overview"

    def __init__(self, hass: HomeAssistant, registry):
        """Initialize the user overview sensor."""
        self._hass = hass
        self.registry = registry

    @property
    def native_value(self):
        """Return number of managed users."""
        return len(self.registry.list_users())

    @property
    def device_info(self):
        """Return device info for the user overview."""
        return DeviceInfo(
            identifiers={(DOMAIN, "users")},
            name="OCPP Benutzer",
            model="OCPP User Registry",
        )

    @property
    def extra_state_attributes(self):
        """Return compact user counts."""
        active_users = 0
        inactive_users = 0
        for user in self.registry.list_users():
            active = user.get("active", True)
            if active:
                active_users += 1
            else:
                inactive_users += 1
        return {
            "active_users": active_users,
            "inactive_users": inactive_users,
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
