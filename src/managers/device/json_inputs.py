"""JSON-to-domain boundary shared by the Flask workload creation flow.

Canonical disk settings use ``disk``, matching DeviceConfig. Policies default to
FCFS, service time to 1, direction to UP, and seek time per cylinder to 1. Disk
bounds/head are required. Unknown keys are rejected to prevent silent input loss.
"""

import re
from typing import Any

from .models import (
    DeviceConfig, DeviceRequestSpec, DeviceType, DiskGeometry, ScanDirection,
    SchedulingPolicy,
)
from .validation import validate_device_configs, validate_request_offsets

MAX_SAFE_INTEGER = 9007199254740991


def _object(value: Any, fields: set[str], name: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    unknown = value.keys() - fields
    if unknown:
        raise ValueError(f"{name}: unsupported field {sorted(unknown)[0]}")
    return value


def _enum(enum: type, value: Any, name: str) -> Any:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be one of {', '.join(e.value for e in enum)}")
    try:
        return enum(value)
    except ValueError as error:
        raise ValueError(
            f"{name} must be one of {', '.join(e.value for e in enum)}"
        ) from error


def _safe_integer(value: Any, name: str) -> None:
    if (isinstance(value, bool) or not isinstance(value, int)
            or not 0 <= value <= MAX_SAFE_INTEGER):
        raise ValueError(f"{name} must be a nonnegative safe integer")


def parse_device_configs(value: Any) -> dict[str, DeviceConfig]:
    """Convert a JSON array to a validated unique configuration registry."""
    if not isinstance(value, list):
        raise ValueError("devices must be an array")
    configs = []
    for index, item in enumerate(value):
        name = f"devices[{index}]"
        item = _object(item, {
            "device_id", "device_type", "policy", "default_service_time", "disk",
        }, name)
        geometry = None
        if "disk" in item:
            disk = _object(item["disk"], {
                "min_cylinder", "max_cylinder", "initial_head", "initial_direction",
                "seek_time_per_cylinder",
            }, name + ".disk")
            for key in ("min_cylinder", "max_cylinder", "initial_head"):
                _safe_integer(disk.get(key), name + ".disk." + key)
            geometry = DiskGeometry(
                min_cylinder=disk["min_cylinder"], max_cylinder=disk["max_cylinder"],
                initial_head=disk["initial_head"],
                initial_direction=_enum(
                    ScanDirection, disk.get("initial_direction", "UP"),
                    name + ".disk.initial_direction",
                ), seek_time_per_cylinder=disk.get("seek_time_per_cylinder", 1),
            )
        configs.append(DeviceConfig(
            device_id=item.get("device_id"),
            device_type=_enum(
                DeviceType, item.get("device_type"), name + ".device_type",
            ),
            policy=_enum(
                SchedulingPolicy, item.get("policy", "FCFS"), name + ".policy",
            ),
            default_service_time=item.get("default_service_time", 1), disk=geometry,
        ))
    return validate_device_configs(configs)


def parse_device_requests(
    value: Any, burst_time: int | float, configs: dict[str, DeviceConfig],
) -> dict[int, DeviceRequestSpec]:
    """Parse canonical CPU offsets and build validated, frozen request inputs."""
    if not isinstance(value, dict):
        raise ValueError("device_requests must be an offset-to-spec object")
    requests = {}
    for key, item in value.items():
        if not isinstance(key, str) or not re.fullmatch(r"0|[1-9][0-9]*", key):
            raise ValueError("Device offset must be a canonical nonnegative integer")
        offset = int(key)
        _safe_integer(offset, "Device offset")
        if offset in requests:
            raise ValueError("Duplicate normalized device offset")
        item = _object(item, {
            "device_id", "operation", "cylinder", "service_time",
        }, f"device_requests[{key}]")
        if "cylinder" in item and item["cylinder"] is not None:
            _safe_integer(item["cylinder"], "cylinder")
        requests[offset] = DeviceRequestSpec(
            device_id=item.get("device_id"), operation=item.get("operation"),
            cylinder=item.get("cylinder"), service_time=item.get("service_time"),
        )
    validate_request_offsets(requests, burst_time, configs)
    return requests
