"""Persistent OCPP user registry."""
from __future__ import annotations

import logging
import time
from typing import Any
from uuid import uuid4

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import CURRENCY_EURO, UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from ocpp.v16.enums import AuthorizationStatus

from .const import DATA_USERS_UPDATED, DOMAIN, STORAGE_USER_REGISTRY

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1


class OcppUserRegistry:
    """Store OCPP users, idTags and accumulated user energy."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the registry."""
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_USER_REGISTRY)
        self.users: dict[str, dict[str, Any]] = {}
        self.sessions: dict[str, dict[str, Any]] = {}
        self.deleted_id_tags: list[str] = []
        self._loaded = False

    async def async_load(self) -> None:
        """Load registry data from Home Assistant storage."""
        if self._loaded:
            return

        data = await self._store.async_load()
        if data is None:
            data = {}

        self.users = data.get("users", {})
        self.sessions = data.get("sessions", {})
        self.deleted_id_tags = data.get("deleted_id_tags", [])
        self._loaded = True

    async def async_save(self) -> None:
        """Persist registry data."""
        await self._store.async_save(
            {
                "users": self.users,
                "sessions": self.sessions,
                "deleted_id_tags": self.deleted_id_tags,
            }
        )

    @callback
    def schedule_save(self) -> None:
        """Schedule registry persistence."""
        self.hass.async_create_task(self.async_save())

    @staticmethod
    def normalize_id_tag(id_tag: str | None) -> str:
        """Normalize an OCPP idTag for matching."""
        return str(id_tag or "").strip()

    @staticmethod
    def parse_id_tags(id_tags: str | list[str]) -> list[str]:
        """Parse idTags from a comma or newline separated string."""
        if isinstance(id_tags, list):
            raw_tags = id_tags
        else:
            raw_tags = str(id_tags).replace("\n", ",").split(",")

        tags: list[str] = []
        for raw_tag in raw_tags:
            tag = OcppUserRegistry.normalize_id_tag(raw_tag)
            if tag and tag not in tags:
                tags.append(tag)
        return tags

    @staticmethod
    def current_month_period() -> str:
        """Return the current Home Assistant local month period."""
        return dt_util.now().strftime("%Y-%m")

    @callback
    def ensure_current_month(self, user: dict[str, Any]) -> None:
        """Reset a user's monthly counters when the local month changes."""
        period = self.current_month_period()
        if user.get("monthly_energy_period") != period:
            user["monthly_energy_period"] = period
            user["monthly_energy_kwh"] = 0.0
            user["monthly_cost"] = 0.0

    @callback
    def list_users(self) -> list[dict[str, Any]]:
        """Return users sorted by name."""
        return sorted(self.users.values(), key=lambda user: user["name"].lower())

    @callback
    def get_user(self, user_id: str) -> dict[str, Any] | None:
        """Return one user."""
        return self.users.get(user_id)

    @callback
    def get_user_for_id_tag(self, id_tag: str | None) -> dict[str, Any] | None:
        """Find a user by OCPP idTag."""
        normalized = self.normalize_id_tag(id_tag)
        for user in self.users.values():
            if normalized in user.get("id_tags", []):
                return user
        return None

    @callback
    def _block_deleted_id_tags(self, id_tags: list[str]) -> None:
        """Remember removed idTags so permissive defaults cannot allow them."""
        for id_tag in id_tags:
            if id_tag and id_tag not in self.deleted_id_tags:
                self.deleted_id_tags.append(id_tag)

    @callback
    def _unblock_id_tags(self, id_tags: list[str]) -> None:
        """Allow explicitly assigned idTags again."""
        if not id_tags:
            return
        self.deleted_id_tags = [
            id_tag for id_tag in self.deleted_id_tags if id_tag not in id_tags
        ]

    @callback
    def find_conflicting_id_tags(
        self, id_tags: str | list[str], exclude_user_id: str | None = None
    ) -> list[str]:
        """Return idTags already assigned to another user."""
        parsed_tags = self.parse_id_tags(id_tags)
        conflicts: list[str] = []
        for user in self.users.values():
            if exclude_user_id is not None and user["user_id"] == exclude_user_id:
                continue
            for tag in parsed_tags:
                if tag in user.get("id_tags", []) and tag not in conflicts:
                    conflicts.append(tag)
        return conflicts

    @callback
    def get_authorization_status(self, id_tag: str | None) -> str | None:
        """Return an authorization status for a managed idTag."""
        user = self.get_user_for_id_tag(id_tag)
        if user is None:
            if self.normalize_id_tag(id_tag) in self.deleted_id_tags:
                return AuthorizationStatus.blocked.value
            return None
        if user.get("active", True):
            return AuthorizationStatus.accepted.value
        return AuthorizationStatus.blocked.value

    @staticmethod
    def session_key(cp_id: str, transaction_id: int) -> str:
        """Return a unique key for a charger transaction."""
        return f"{cp_id}:{transaction_id}"

    @callback
    def get_session(
        self, cp_id: str, transaction_id: int | str | None
    ) -> dict[str, Any] | None:
        """Return an active charging session."""
        if transaction_id in (None, 0, "0"):
            return None
        return self.sessions.get(self.session_key(cp_id, int(transaction_id)))

    @callback
    def get_latest_session(self, cp_id: str) -> dict[str, Any] | None:
        """Return the latest active charging session for a charge point."""
        sessions = [
            session
            for session in self.sessions.values()
            if session.get("cp_id") == cp_id
        ]
        if not sessions:
            return None
        return max(sessions, key=lambda session: float(session.get("started_at", 0)))

    async def async_add_user(
        self, name: str, id_tags: str | list[str], active: bool = True
    ) -> str:
        """Add a managed OCPP user."""
        parsed_tags = self.parse_id_tags(id_tags)
        user_id = slugify(name) or uuid4().hex
        if user_id in self.users:
            user_id = f"{user_id}_{uuid4().hex[:8]}"

        self.users[user_id] = {
            "user_id": user_id,
            "name": str(name).strip(),
            "id_tags": parsed_tags,
            "active": bool(active),
            "energy_kwh": 0.0,
            "monthly_energy_kwh": 0.0,
            "monthly_cost": 0.0,
            "monthly_energy_period": self.current_month_period(),
            "created_at": time.time(),
        }
        self._unblock_id_tags(parsed_tags)
        await self.async_save()
        self.notify_updated()
        return user_id

    async def async_update_user(
        self,
        user_id: str,
        name: str | None = None,
        id_tags: str | list[str] | None = None,
        active: bool | None = None,
    ) -> None:
        """Update a managed OCPP user."""
        user = self.users[user_id]
        old_id_tags = user.get("id_tags", [])
        if name is not None:
            user["name"] = str(name).strip()
        if id_tags is not None:
            parsed_tags = self.parse_id_tags(id_tags)
            self._block_deleted_id_tags(
                [id_tag for id_tag in old_id_tags if id_tag not in parsed_tags]
            )
            self._unblock_id_tags(parsed_tags)
            user["id_tags"] = parsed_tags
        if active is not None:
            user["active"] = bool(active)
        await self.async_save()
        self.notify_updated()

    async def async_delete_user(self, user_id: str) -> None:
        """Delete a managed OCPP user."""
        user = self.users.pop(user_id)
        self._block_deleted_id_tags(user.get("id_tags", []))
        self.sessions = {
            session_key: session
            for session_key, session in self.sessions.items()
            if session.get("user_id") != user_id
        }
        await self.async_save()
        self.notify_updated()

    @callback
    def record_start_transaction(
        self,
        transaction_id: int,
        id_tag: str,
        cp_id: str,
        meter_start_kwh: float,
    ) -> None:
        """Record an active user charging session."""
        user = self.get_user_for_id_tag(id_tag)
        if user is None:
            return

        self.sessions[self.session_key(cp_id, transaction_id)] = {
            "transaction_id": transaction_id,
            "user_id": user["user_id"],
            "id_tag": self.normalize_id_tag(id_tag),
            "cp_id": cp_id,
            "meter_start_kwh": meter_start_kwh,
            "credited_energy_kwh": 0.0,
            "started_at": time.time(),
        }
        self.schedule_save()

    @callback
    def record_session_energy(
        self,
        transaction_id: int,
        cp_id: str,
        session_energy_kwh: float | None,
        energy_price: float | None = None,
    ) -> float:
        """Add the newly measured session energy delta to user totals."""
        if session_energy_kwh is None or session_energy_kwh < 0:
            return 0.0

        session = self.sessions.get(self.session_key(cp_id, transaction_id))
        if session is None:
            return 0.0

        user = self.users.get(session["user_id"])
        if user is None:
            return 0.0

        credited_energy = float(session.get("credited_energy_kwh", 0.0))
        delta_energy = round(float(session_energy_kwh) - credited_energy, 6)
        if delta_energy <= 0:
            return 0.0

        self.ensure_current_month(user)
        user["energy_kwh"] = round(
            float(user.get("energy_kwh", 0.0)) + delta_energy,
            6,
        )
        user["monthly_energy_kwh"] = round(
            float(user.get("monthly_energy_kwh", 0.0)) + delta_energy,
            6,
        )
        if energy_price is not None:
            user["monthly_cost"] = round(
                float(user.get("monthly_cost", 0.0))
                + (delta_energy * float(energy_price)),
                6,
            )
        session["credited_energy_kwh"] = round(credited_energy + delta_energy, 6)
        self.schedule_save()
        self.notify_updated()
        return delta_energy

    @callback
    def record_stop_transaction(
        self,
        transaction_id: int,
        cp_id: str,
        meter_stop_kwh: float,
        session_energy_kwh: float | None = None,
        energy_price: float | None = None,
    ) -> None:
        """Close a user charging session and add energy to the user total."""
        session = self.sessions.pop(self.session_key(cp_id, transaction_id), None)
        if session is None:
            return

        user = self.users.get(session["user_id"])
        if user is None:
            return

        if session_energy_kwh is None:
            session_energy_kwh = meter_stop_kwh - float(session["meter_start_kwh"])

        if session_energy_kwh < 0:
            _LOGGER.warning(
                "Ignoring negative OCPP user session energy for transaction %s",
                transaction_id,
            )
            return

        self.ensure_current_month(user)
        credited_energy = float(session.get("credited_energy_kwh", 0.0))
        delta_energy = round(float(session_energy_kwh) - credited_energy, 6)
        if delta_energy > 0:
            user["energy_kwh"] = round(
                float(user.get("energy_kwh", 0.0)) + delta_energy,
                6,
            )
            user["monthly_energy_kwh"] = round(
                float(user.get("monthly_energy_kwh", 0.0)) + delta_energy,
                6,
            )
            if energy_price is not None:
                user["monthly_cost"] = round(
                    float(user.get("monthly_cost", 0.0))
                    + (delta_energy * float(energy_price)),
                    6,
                )
        user["last_session_energy_kwh"] = round(float(session_energy_kwh), 6)
        user["last_session_finished_at"] = time.time()
        self.schedule_save()
        self.notify_updated()

    @callback
    def notify_updated(self) -> None:
        """Notify user entities that registry state changed."""
        async_dispatcher_send(self.hass, DATA_USERS_UPDATED)


async def async_get_user_registry(hass: HomeAssistant) -> OcppUserRegistry:
    """Return the shared OCPP user registry."""
    hass.data.setdefault(DOMAIN, {})
    registry = hass.data[DOMAIN].get("user_registry")
    if registry is None:
        registry = OcppUserRegistry(hass)
        hass.data[DOMAIN]["user_registry"] = registry
    await registry.async_load()
    return registry


USER_SENSOR_DEVICE_CLASS = SensorDeviceClass.ENERGY
USER_COST_SENSOR_DEVICE_CLASS = SensorDeviceClass.MONETARY
USER_SENSOR_STATE_CLASS = SensorStateClass.TOTAL_INCREASING
USER_MONTHLY_SENSOR_STATE_CLASS = SensorStateClass.TOTAL
USER_SENSOR_UNIT = UnitOfEnergy.KILO_WATT_HOUR
USER_COST_SENSOR_UNIT = CURRENCY_EURO
