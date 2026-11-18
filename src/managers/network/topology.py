"""Immutable topology control instructions; the engine owns their event timing."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from .models import NetworkConfig
from .validation import _number, _text, validate_network_config


class TopologyAction(Enum):
    """Administrative availability actions, not new global event priorities."""

    LINK_DOWN = "LINK_DOWN"
    LINK_UP = "LINK_UP"
    NODE_DOWN = "NODE_DOWN"
    NODE_UP = "NODE_UP"


@dataclass(frozen=True, kw_only=True)
class NetworkTopologyEvent:
    """Absolute timestamp and exactly one action-appropriate target.

    Configuration order is preserved. The engine uses the existing queue tuple:
    (timestamp, target ID, insertion ID). Packet/process events use owner PID;
    no failure-before-completion priority is introduced. At equal time and ID,
    initial arrivals precede these configured events, which precede callbacks
    generated during execution. Equal-target controls retain configuration order.
    """

    timestamp: float
    action: TopologyAction
    link_id: str | None = None
    node_id: str | None = None


def validate_topology_events(
    events: Sequence[NetworkTopologyEvent], config: NetworkConfig,
) -> tuple[NetworkTopologyEvent, ...]:
    """Validate/copy all controls before any callbacks or manager mutations exist."""
    validate_network_config(config)
    if not isinstance(events, (list, tuple)):
        raise ValueError("network events must be a list or tuple")
    links = {link.link_id for link in config.links}
    nodes = {node.node_id for node in config.nodes}
    for event in events:
        if not isinstance(event, NetworkTopologyEvent):
            raise ValueError("network events must contain NetworkTopologyEvent")
        _number(event.timestamp, "network event timestamp")
        if not isinstance(event.action, TopologyAction):
            raise ValueError("network event action must be TopologyAction")
        is_link = event.action in (TopologyAction.LINK_DOWN, TopologyAction.LINK_UP)
        target = event.link_id if is_link else event.node_id
        unused = event.node_id if is_link else event.link_id
        _text(target, "network event target")
        if unused is not None:
            raise ValueError("network event must have only its action's target")
        if target not in (links if is_link else nodes):
            raise ValueError(f"Unknown network event target: {target}")
    return tuple(events)
