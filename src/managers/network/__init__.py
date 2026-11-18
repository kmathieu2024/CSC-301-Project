"""Network models, pure selection, and execution used by SimulationEngine.

The manager remains usable standalone; the engine owns time, callback
registration, process transitions, and Security Manager authorization.
"""

from .models import (
    Availability, LinkConfig, LinkRuntime, NetworkConfig, NetworkNotification,
    NetworkOperation, NetworkRequestSpec, NetworkRuntime, NetworkUpdate,
    NodeConfig, NodeRuntime, OperationKind, PacketRuntime, PacketState,
    QueuePolicy, TerminalReason, callback_token, create_network_runtime,
    create_packet_runtime, packet_identifier, snapshot_network,
)
from .validation import (
    operation_due_at, transmission_duration, validate_link_config,
    validate_network_config, validate_node_config, validate_operation,
    validate_packet, validate_request_offsets, validate_request_spec,
    validate_runtime_state, validate_transition,
)
from .routing import next_hop, shortest_path
from .scheduler import select_fifo, select_next, select_priority
from .network_manager import NetworkManager
from .topology import NetworkTopologyEvent, TopologyAction, validate_topology_events

__all__ = [
    "Availability", "LinkConfig", "LinkRuntime", "NetworkConfig",
    "NetworkNotification", "NetworkOperation", "NetworkRequestSpec",
    "NetworkRuntime", "NetworkUpdate", "NodeConfig", "NodeRuntime",
    "OperationKind", "PacketRuntime", "PacketState", "QueuePolicy",
    "TerminalReason", "callback_token", "create_network_runtime",
    "create_packet_runtime", "packet_identifier", "snapshot_network",
    "operation_due_at", "transmission_duration", "validate_link_config",
    "validate_network_config", "validate_node_config", "validate_operation",
    "validate_packet", "validate_request_offsets", "validate_request_spec",
    "validate_runtime_state", "validate_transition",
    "shortest_path", "next_hop", "select_next", "select_fifo", "select_priority",
    "NetworkManager",
    "NetworkTopologyEvent", "TopologyAction", "validate_topology_events",
]
