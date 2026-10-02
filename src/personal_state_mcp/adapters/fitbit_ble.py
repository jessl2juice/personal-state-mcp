from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any, Callable

from ..models import HealthObservation, iso_utc, stable_hash, utc_now
from ..storage import StateStore
from ..watch_contract import FITBIT_BLE_ADAPTER_ID


HEART_RATE_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
HEART_RATE_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"
FITBIT_BLE_SOURCE_PACKAGE = "bluetooth.le.heart_rate_service"
FITBIT_BLE_ADAPTER_VERSION = "ble-hr-v1"
FITBIT_BLE_SCHEMA_VERSION = "personal-state-fitbit-ble/v1"


class FitbitBleUnavailable(RuntimeError):
    """Raised when the optional BLE runtime is not installed or unavailable."""


@dataclass(frozen=True)
class HeartRateMeasurement:
    bpm: int
    sensor_contact: str
    energy_expended_kj: int | None
    rr_intervals_ms: tuple[float, ...]
    raw_flags: int

    def public_dict(self) -> dict[str, Any]:
        return {
            "bpm": self.bpm,
            "sensor_contact": self.sensor_contact,
            "energy_expended_kj": self.energy_expended_kj,
            "rr_intervals_ms": [round(value, 3) for value in self.rr_intervals_ms],
            "raw_flags": self.raw_flags,
        }


def parse_heart_rate_measurement(data: bytes | bytearray | memoryview) -> HeartRateMeasurement:
    payload = bytes(data)
    if len(payload) < 2:
        raise ValueError("heart_rate_packet_too_short")
    flags = payload[0]
    offset = 1
    if flags & 0x01:
        if len(payload) < offset + 2:
            raise ValueError("heart_rate_packet_missing_uint16_value")
        bpm = int.from_bytes(payload[offset:offset + 2], "little")
        offset += 2
    else:
        bpm = payload[offset]
        offset += 1
    if not 0 <= bpm <= 500:
        raise ValueError("heart_rate_value_out_of_range")

    contact_supported = bool(flags & 0x04)
    contact_detected = bool(flags & 0x02)
    if not contact_supported:
        sensor_contact = "unsupported"
    else:
        sensor_contact = "detected" if contact_detected else "not_detected"

    energy_expended_kj = None
    if flags & 0x08:
        if len(payload) < offset + 2:
            raise ValueError("heart_rate_packet_missing_energy_value")
        energy_expended_kj = int.from_bytes(payload[offset:offset + 2], "little")
        offset += 2

    rr_intervals_ms: list[float] = []
    if flags & 0x10:
        remaining = len(payload) - offset
        if remaining % 2:
            raise ValueError("heart_rate_packet_bad_rr_interval_length")
        while offset < len(payload):
            rr_raw = int.from_bytes(payload[offset:offset + 2], "little")
            rr_intervals_ms.append((rr_raw / 1024.0) * 1000.0)
            offset += 2

    return HeartRateMeasurement(
        bpm=bpm,
        sensor_contact=sensor_contact,
        energy_expended_kj=energy_expended_kj,
        rr_intervals_ms=tuple(rr_intervals_ms),
        raw_flags=flags,
    )


def _device_identity_hash(device_address: str | None, device_name: str | None) -> str:
    identity = "|".join(part for part in (device_address, device_name) if part) or "unknown-fitbit-ble-device"
    return stable_hash(f"fitbit_ble_device|{identity}")


def heart_rate_observation_from_ble(
    measurement: HeartRateMeasurement,
    *,
    measured_at: datetime | None = None,
    received_at: datetime | None = None,
    device_address: str | None = None,
    device_name: str | None = None,
) -> HealthObservation:
    measured = (measured_at or utc_now()).astimezone(timezone.utc)
    received = (received_at or utc_now()).astimezone(timezone.utc)
    device_hash = _device_identity_hash(device_address, device_name)
    sample = {
        "time": iso_utc(measured),
        "value": measurement.bpm,
        "unit": "bpm",
        "sensor_contact": measurement.sensor_contact,
    }
    if measurement.rr_intervals_ms:
        sample["rr_intervals_ms"] = [round(value, 3) for value in measurement.rr_intervals_ms]
    payload = {"samples": [sample]}
    if measurement.energy_expended_kj is not None:
        payload["energy_expended_kj"] = measurement.energy_expended_kj
    source_record_basis = json.dumps(
        {
            "adapter": FITBIT_BLE_ADAPTER_ID,
            "device_hash": device_hash,
            "measured_at": measured.isoformat(),
            "bpm": measurement.bpm,
            "rr_intervals_ms": sample.get("rr_intervals_ms", []),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    source_record_hash = stable_hash(source_record_basis)
    label = device_name if device_name else "Bluetooth LE heart-rate device"
    return HealthObservation(
        id=stable_hash(f"fitbit_ble_observation|{source_record_hash}"),
        metric="vitals.heart_rate",
        category="vitals",
        record_kind="series",
        payload=payload,
        start_at=measured,
        end_at=measured,
        observed_by_companion_at=received,
        ingested_at_server=received,
        upstream_last_modified_at=received,
        source_package=FITBIT_BLE_SOURCE_PACKAGE,
        recording_method="active",
        attribution={
            "state": "external_device",
            "evidence": "Direct Bluetooth LE Heart Rate Service 0x180D measurement.",
            "device_type": "watch",
            "device_model": label,
        },
        installation_hash=device_hash,
        source_record_hash=source_record_hash,
        adapter_id=FITBIT_BLE_ADAPTER_ID,
        adapter_version=FITBIT_BLE_ADAPTER_VERSION,
        identity_namespace_id="fitbit-ble-local",
        schema_version=FITBIT_BLE_SCHEMA_VERSION,
    )


def _load_bleak():
    try:
        from bleak import BleakClient, BleakScanner  # type: ignore
    except Exception as exc:
        raise FitbitBleUnavailable(
            "Install the optional BLE runtime with `python -m pip install -e .[ble]`."
        ) from exc
    return BleakClient, BleakScanner


async def discover_heart_rate_devices(
    *,
    timeout_seconds: float = 8.0,
    name_contains: str | None = None,
) -> list[dict[str, Any]]:
    _client, scanner = _load_bleak()
    devices = await scanner.discover(timeout=timeout_seconds, service_uuids=[HEART_RATE_SERVICE_UUID])
    needle = name_contains.casefold() if name_contains else None
    result: list[dict[str, Any]] = []
    for device in devices:
        name = getattr(device, "name", None) or ""
        if needle and needle not in name.casefold():
            continue
        result.append(
            {
                "address": getattr(device, "address", None),
                "name": name or None,
                "details": str(getattr(device, "details", ""))[:160],
            }
        )
    return result


async def stream_heart_rate(
    store: StateStore,
    *,
    seconds: float = 300.0,
    address: str | None = None,
    name_contains: str | None = None,
    dry_run: bool = False,
    on_sample: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    client_class, scanner = _load_bleak()
    target = None
    if address:
        target = await scanner.find_device_by_address(address, timeout=10.0)
    if target is None:
        devices = await scanner.discover(timeout=10.0, service_uuids=[HEART_RATE_SERVICE_UUID])
        needle = name_contains.casefold() if name_contains else None
        for device in devices:
            name = getattr(device, "name", None) or ""
            if needle and needle not in name.casefold():
                continue
            target = device
            break
    if target is None:
        raise FitbitBleUnavailable("No Bluetooth LE Heart Rate Service device was found.")

    device_address = getattr(target, "address", None)
    device_name = getattr(target, "name", None)
    counts = {"samples_seen": 0, "observations_inserted": 0, "duplicates": 0}

    def handle_measurement(_sender: Any, data: bytearray) -> None:
        measurement = parse_heart_rate_measurement(data)
        observed = heart_rate_observation_from_ble(
            measurement,
            device_address=device_address,
            device_name=device_name,
        )
        counts["samples_seen"] += 1
        stored = {"inserted": 0, "duplicates": 0} if dry_run else store.import_health_observations([observed])
        counts["observations_inserted"] += int(stored["inserted"])
        counts["duplicates"] += int(stored["duplicates"])
        if on_sample is not None:
            on_sample(
                {
                    "sample": measurement.public_dict(),
                    "stored": stored,
                    "observation": observed.public_dict(),
                }
            )

    async with client_class(target) as client:
        await client.start_notify(HEART_RATE_MEASUREMENT_UUID, handle_measurement)
        try:
            await asyncio.sleep(max(1.0, seconds))
        finally:
            await client.stop_notify(HEART_RATE_MEASUREMENT_UUID)

    return {
        "adapter": FITBIT_BLE_ADAPTER_ID,
        "device": {"name": device_name, "address_hash": _device_identity_hash(device_address, None) if device_address else None},
        "dry_run": dry_run,
        **counts,
    }
