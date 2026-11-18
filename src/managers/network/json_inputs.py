"""Strict JSON-to-domain conversion, separate from execution and authorization.

Network is opt-in by presence of an object; enabled defaults to true. Disabled
settings are still fully validated, and cannot contain events or packet requests.
Link bandwidth is required; other defaults match the Phase 1 models. Unknown
fields and offset aliases are errors, never silently discarded. Integer workload
fields use the same JavaScript-safe upper bound as existing Device/Memory inputs.
Domain positivity, references, numeric/enum types, and topology invariants remain
owned by the existing validators, not duplicated here.
"""

from dataclasses import dataclass
import math
import re
from typing import Any

from .models import (
    LinkConfig, NetworkConfig, NetworkRequestSpec, NodeConfig, QueuePolicy,
)
from .topology import NetworkTopologyEvent, TopologyAction, validate_topology_events
from .validation import validate_network_config, validate_request_offsets

_MAX_SAFE_INTEGER = 9007199254740991


@dataclass(frozen=True, kw_only=True)
class NetworkSettings:
    """Parsed workload settings, keeping optional engine controls outside topology."""

    config: NetworkConfig
    events: tuple[NetworkTopologyEvent, ...] = ()
    enabled: bool = True


def validate_json_numbers(value: Any) -> None:
    """Reject nonstandard JSON floats anywhere in a Network workload candidate.

    Python's JSON loader accepts NaN/Infinity and overflowing exponents. Reject
    them before shared CPU settings can admit a nonfinite simulation clock.
    This boundary rule does not replace domain type/positivity validation.
    """
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("Network workload JSON numbers must be finite")


def _object(value: Any, allowed: set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{name} field names must be strings")
    unknown = value.keys() - allowed
    if unknown:
        raise ValueError(f"{name}: unsupported field {sorted(unknown)[0]}")
    return value


def _array(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return value


def _enum(model: type, value: Any, name: str) -> Any:
    choices = ", ".join(item.value for item in model)
    if not isinstance(value, str):
        raise ValueError(f"{name} must be one of {choices}")
    try:
        return model(value)
    except ValueError as error:
        raise ValueError(f"{name} must be one of {choices}") from error


def _safe_bound(value: int, name: str) -> None:
    # Call only after domain validation has established integer type and sign.
    if value > _MAX_SAFE_INTEGER:
        raise ValueError(f"{name} exceeds the JSON safe integer range")


def parse_network_settings(value: Any) -> NetworkSettings:
    """Convert and validate a present network object without allocating a manager."""
    value = _object(value, {"enabled", "nodes", "links", "hop_limit", "events"},
                    "network")
    enabled = value.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("network.enabled must be boolean")
    nodes = []
    for index, raw in enumerate(_array(value.get("nodes", []), "network.nodes")):
        item = _object(raw, {"node_id", "address"}, f"network.nodes[{index}]")
        nodes.append(NodeConfig(
            node_id=item.get("node_id"), address=item.get("address"),
        ))
    links = []
    for index, raw in enumerate(_array(value.get("links", []), "network.links")):
        name = f"network.links[{index}]"
        item = _object(raw, {
            "link_id", "source", "destination", "bandwidth_bytes_per_time_unit",
            "propagation_delay", "routing_cost", "queue_policy",
            "queue_capacity_packets",
        }, name)
        links.append(LinkConfig(
            link_id=item.get("link_id"), source=item.get("source"),
            destination=item.get("destination"),
            bandwidth_bytes_per_time_unit=item.get("bandwidth_bytes_per_time_unit"),
            propagation_delay=item.get("propagation_delay", 0),
            routing_cost=item.get("routing_cost", 1),
            queue_policy=_enum(QueuePolicy, item.get("queue_policy", "FIFO"),
                               name + ".queue_policy"),
            queue_capacity_packets=item.get("queue_capacity_packets", 0),
        ))
    config = NetworkConfig(nodes=nodes, links=links,
                           hop_limit=value.get("hop_limit", 16))
    validate_network_config(config)
    _safe_bound(config.hop_limit, "network.hop_limit")
    for link in config.links:
        _safe_bound(link.queue_capacity_packets, "queue_capacity_packets")
    events = []
    for index, raw in enumerate(_array(value.get("events", []), "network.events")):
        name = f"network.events[{index}]"
        item = _object(raw, {"timestamp", "action", "link_id", "node_id"}, name)
        event = NetworkTopologyEvent(
            timestamp=item.get("timestamp"),
            action=_enum(TopologyAction, item.get("action"), name + ".action"),
            link_id=item.get("link_id"), node_id=item.get("node_id"),
        )
        # Explicit null for the unused target is also rejected at the JSON
        # boundary: malformed fields should not silently disappear on import.
        target = "link_id" if event.action in (
            TopologyAction.LINK_DOWN, TopologyAction.LINK_UP,
        ) else "node_id"
        if set(item) != {"timestamp", "action", target}:
            raise ValueError(f"{name} requires timestamp, action, and only {target}")
        events.append(event)
    validated = validate_topology_events(events, config)
    if not enabled and validated:
        raise ValueError("network.events requires networking to be enabled")
    return NetworkSettings(config=config, events=validated, enabled=enabled)


def parse_network_requests(
    value: Any, burst_time: int | float, settings: NetworkSettings | None,
) -> dict[int, NetworkRequestSpec]:
    """Convert canonical offsets and validate typed specs against the same topology."""
    if not isinstance(value, dict):
        raise ValueError("network_requests must be an offset-to-spec object")
    if settings is None or not settings.enabled:
        if value:
            raise ValueError("network_requests requires networking to be enabled")
        return {}
    requests = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not re.fullmatch(r"0|[1-9][0-9]*", key):
            raise ValueError("Network offset must be a canonical nonnegative integer")
        if len(key) > len(str(_MAX_SAFE_INTEGER)):
            raise ValueError("Network offset exceeds the JSON safe integer range")
        offset = int(key)
        _safe_bound(offset, "Network offset")
        if offset in requests:
            raise ValueError("Duplicate normalized network offset")
        item = _object(raw, {"source", "destination", "size_bytes", "priority"},
                       f"network_requests[{key}]")
        requests[offset] = NetworkRequestSpec(
            source=item.get("source"), destination=item.get("destination"),
            size_bytes=item.get("size_bytes"), priority=item.get("priority", 1),
        )
    validate_request_offsets(requests, burst_time, settings.config)
    for spec in requests.values():
        _safe_bound(spec.size_bytes, "size_bytes")
        _safe_bound(spec.priority, "packet priority")
    return requests
