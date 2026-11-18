"""Pure boundary validation. Invalid network contracts raise ValueError.

No coercion, routing, scheduling, authorization, or lifecycle mutation occurs.
Floating-point durations must remain finite and positive when required. A positive
duration must advance its start timestamp; zero propagation delay is permitted.
No arbitrary upper bound or rounding is imposed on ordinary simulation inputs.
"""

import math
from collections.abc import Mapping

from .models import (
    Availability, LinkConfig, LinkRuntime, NetworkConfig, NetworkNotification,
    NetworkOperation, NetworkRequestSpec, NetworkRuntime, NodeConfig,
    NodeRuntime, OperationKind, PacketRuntime, PacketState, QueuePolicy,
    TerminalReason, callback_token, packet_identifier,
)


_TERMINAL = frozenset({
    PacketState.DELIVERED, PacketState.DROPPED,
    PacketState.FAILED, PacketState.CANCELLED,
})
_REASONS = {
    PacketState.DELIVERED: {TerminalReason.DELIVERED},
    PacketState.DROPPED: {
        TerminalReason.QUEUE_FULL, TerminalReason.NO_ROUTE, TerminalReason.HOP_LIMIT,
    },
    PacketState.FAILED: {
        TerminalReason.LINK_FAILURE, TerminalReason.NODE_FAILURE,
        TerminalReason.CALLBACK_FAILURE,
    },
    PacketState.CANCELLED: {TerminalReason.CANCELLED},
}


def _text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{name} must be nonempty text without outer whitespace")


def _integer(value: object, name: str, *, positive: bool = False) -> None:
    if (isinstance(value, bool) or not isinstance(value, int)
            or value < (1 if positive else 0)):
        qualifier = "positive" if positive else "nonnegative"
        raise ValueError(f"{name} must be a {qualifier} integer")


def _number(value: object, name: str, *, positive: bool = False) -> None:
    valid = not isinstance(value, bool) and isinstance(value, (int, float))
    try:
        valid = valid and math.isfinite(value) and (
            value > 0 if positive else value >= 0
        )
    except OverflowError:
        valid = False
    if not valid:
        qualifier = "positive" if positive else "nonnegative"
        raise ValueError(f"{name} must be a finite {qualifier} number")


def operation_due_at(started_at: float, duration: float) -> float:
    """Validate a derived boundary, including overflow and lost time precision."""
    _number(started_at, "started_at")
    _number(duration, "duration")
    try:
        due_at = started_at + duration
    except OverflowError as error:
        raise ValueError("Derived due_at must be finite") from error
    _number(due_at, "derived due_at")
    if duration > 0 and due_at <= started_at:
        raise ValueError("Positive duration must advance simulated time")
    return due_at


def transmission_duration(size_bytes: int, link: LinkConfig) -> float:
    """Return finite positive serialization time; reject overflow/underflow."""
    _integer(size_bytes, "size_bytes", positive=True)
    validate_link_config(link)
    try:
        duration = size_bytes / link.bandwidth_bytes_per_time_unit
    except (OverflowError, ZeroDivisionError) as error:
        raise ValueError("Derived transmission duration must be finite") from error
    _number(duration, "derived transmission duration", positive=True)
    return duration


def validate_node_config(config: NodeConfig) -> None:
    """Validate node identity and optional display address without normalization."""
    if not isinstance(config, NodeConfig):
        raise ValueError("node must be NodeConfig")
    _text(config.node_id, "node_id")
    if config.address is not None:
        _text(config.address, "address")


def validate_link_config(config: LinkConfig) -> None:
    """Validate intrinsic directed-link fields; topology checks endpoints later."""
    if not isinstance(config, LinkConfig):
        raise ValueError("link must be LinkConfig")
    for name in ("link_id", "source", "destination"):
        _text(getattr(config, name), name)
    _number(config.bandwidth_bytes_per_time_unit, "bandwidth", positive=True)
    _number(config.routing_cost, "routing_cost", positive=True)
    _number(config.propagation_delay, "propagation_delay")
    _integer(config.queue_capacity_packets, "queue_capacity_packets")
    if not isinstance(config.queue_policy, QueuePolicy):
        raise ValueError("queue_policy must be QueuePolicy")


def validate_network_config(config: NetworkConfig) -> None:
    """Validate complete topology before registration; duplicate IDs are errors."""
    if not isinstance(config, NetworkConfig):
        raise ValueError("config must be NetworkConfig")
    _integer(config.hop_limit, "hop_limit", positive=True)
    nodes = set()
    for node in config.nodes:
        validate_node_config(node)
        if node.node_id in nodes:
            raise ValueError(f"Duplicate node_id: {node.node_id}")
        nodes.add(node.node_id)
    links = set()
    for link in config.links:
        validate_link_config(link)
        if link.link_id in links:
            raise ValueError(f"Duplicate link_id: {link.link_id}")
        links.add(link.link_id)
        if link.source not in nodes or link.destination not in nodes:
            raise ValueError(f"Link {link.link_id} references an unknown node")


def _request_spec(spec: NetworkRequestSpec, nodes: set[str]) -> None:
    if not isinstance(spec, NetworkRequestSpec):
        raise ValueError("spec must be NetworkRequestSpec")
    for name in ("source", "destination"):
        value = getattr(spec, name)
        _text(value, name)
        if value not in nodes:
            raise ValueError(f"Unknown packet {name}: {value}")
    _integer(spec.size_bytes, "size_bytes", positive=True)
    _integer(spec.priority, "priority")


def validate_request_spec(spec: NetworkRequestSpec, config: NetworkConfig) -> None:
    """Validate packet endpoints, positive byte size, and nonnegative priority.

    Disconnected endpoints are valid input; NO_ROUTE is an execution outcome.
    Derived timing is checked against the selected link at its actual boundary.
    """
    validate_network_config(config)
    _request_spec(spec, {node.node_id for node in config.nodes})


def validate_request_offsets(
    requests: Mapping[int, NetworkRequestSpec], burst_time: int | float,
    config: NetworkConfig,
) -> None:
    """Validate typed CPU offsets/specs at parser and engine boundaries."""
    validate_network_config(config)
    _number(burst_time, "burst_time", positive=True)
    if not isinstance(requests, Mapping):
        raise ValueError("requests must be an offset-to-spec mapping")
    nodes = {node.node_id for node in config.nodes}
    for offset, spec in requests.items():
        _integer(offset, "offset")
        if offset >= burst_time:
            raise ValueError("offset must be less than burst_time")
        _request_spec(spec, nodes)


def validate_transition(previous: PacketState, following: PacketState) -> None:
    """Check permitted state edges only; mutation/history belong to the manager.

    Terminal cancellation/completion is handled as an idempotent no-op by the
    manager, never another transition or another notification.
    """
    if not isinstance(previous, PacketState) or not isinstance(following, PacketState):
        raise ValueError("Transitions require PacketState values")
    allowed = {
        PacketState.AT_NODE: {
            PacketState.QUEUED, PacketState.TRANSMITTING, PacketState.DELIVERED,
            PacketState.DROPPED, PacketState.FAILED, PacketState.CANCELLED,
        },
        PacketState.QUEUED: {
            PacketState.AT_NODE, PacketState.TRANSMITTING,
            PacketState.DROPPED, PacketState.FAILED, PacketState.CANCELLED,
        },
        PacketState.TRANSMITTING: {
            PacketState.PROPAGATING, PacketState.FAILED, PacketState.CANCELLED,
        },
        PacketState.PROPAGATING: {
            PacketState.AT_NODE, PacketState.DELIVERED,
            PacketState.FAILED, PacketState.CANCELLED,
        },
    }
    if following not in allowed.get(previous, set()):
        raise ValueError(
            f"Invalid packet transition: {previous.value}->{following.value}"
        )


def _packet(packet: PacketRuntime, config: NetworkConfig, nodes: set[str]) -> None:
    if not isinstance(packet, PacketRuntime):
        raise ValueError("packet must be PacketRuntime")
    _integer(packet.sequence, "sequence")
    if packet.packet_id != packet_identifier(packet.sequence):
        raise ValueError("packet_id must match deterministic sequence")
    _integer(packet.generation, "generation")
    _integer(packet.operation_generation, "operation_generation")
    _text(packet.pid, "pid")
    _request_spec(packet.spec, nodes)
    _number(packet.submitted_at, "submitted_at")
    _text(packet.current_node, "current_node")
    if packet.current_node not in nodes:
        raise ValueError("current_node is unknown")
    if not isinstance(packet.state, PacketState):
        raise ValueError("state must be PacketState")
    _integer(packet.completed_hops, "completed_hops")
    if packet.completed_hops > config.hop_limit:
        raise ValueError("completed_hops exceeds hop_limit")
    for name in ("queue_wait_time", "transmission_time", "propagation_time"):
        _number(getattr(packet, name), name)
    if packet.completed_hops == 0 and packet.current_node != packet.spec.source:
        raise ValueError("Unforwarded packet must remain at its source")
    terminal = packet.state in _TERMINAL
    if terminal:
        _number(packet.finished_at, "finished_at")
        if packet.finished_at < packet.submitted_at:
            raise ValueError("finished_at precedes submission")
        if (not isinstance(packet.reason, TerminalReason)
                or packet.reason not in _REASONS[packet.state]):
            raise ValueError("Terminal reason must match packet outcome")
    elif packet.finished_at is not None or packet.reason is not None:
        raise ValueError("Live packets prohibit terminal fields")
    if packet.state == PacketState.DELIVERED:
        if packet.current_node != packet.spec.destination:
            raise ValueError("Delivered packet must be at its destination")
    if packet.state == PacketState.QUEUED:
        _number(packet.queued_at, "queued_at")
        _integer(packet.enqueue_sequence, "enqueue_sequence")
        if packet.queued_at < packet.submitted_at:
            raise ValueError("Queue entry precedes submission")
    elif packet.queued_at is not None or packet.enqueue_sequence is not None:
        raise ValueError("Only queued packets carry queue-entry fields")
    if packet.state in (
        PacketState.QUEUED, PacketState.TRANSMITTING, PacketState.PROPAGATING,
    ):
        _text(packet.link_id, "link_id")
        link = next((item for item in config.links if item.link_id == packet.link_id),
                    None)
        if link is None or link.source != packet.current_node:
            raise ValueError("Packet egress must exist and start at current_node")
        if packet.completed_hops >= config.hop_limit:
            raise ValueError("Packet has no remaining hop budget")
    elif packet.link_id is not None:
        raise ValueError("Node/terminal packets prohibit link_id")
    if packet.state in (PacketState.TRANSMITTING, PacketState.PROPAGATING):
        _text(packet.operation_token, "operation_token")
    elif packet.operation_token is not None:
        raise ValueError("Only in-flight packets carry an operation token")


def validate_packet(packet: PacketRuntime, config: NetworkConfig) -> None:
    """Validate one packet's fields; registry ownership is checked separately."""
    validate_network_config(config)
    _packet(packet, config, {node.node_id for node in config.nodes})


def validate_operation(
    operation: NetworkOperation, packet: PacketRuntime, link: LinkConfig,
) -> None:
    """Check callback identity, packet generation, phase, and modeled due time."""
    if not isinstance(operation, NetworkOperation):
        raise ValueError("operation must be NetworkOperation")
    if not isinstance(packet, PacketRuntime):
        raise ValueError("packet must be PacketRuntime")
    validate_link_config(link)
    for name in ("generation", "sequence", "packet_generation"):
        _integer(getattr(operation, name), name)
    if operation.token != callback_token(operation.generation, operation.sequence):
        raise ValueError("Operation token must match generation and sequence")
    if (operation.generation != packet.generation
            or operation.packet_generation != packet.operation_generation):
        raise ValueError("Stale operation generation")
    if (operation.packet_id != packet.packet_id
            or operation.link_id != link.link_id or packet.link_id != link.link_id
            or packet.operation_token != operation.token):
        raise ValueError("Operation does not own the packet/link callback")
    expected_state = {
        OperationKind.TRANSMISSION: PacketState.TRANSMITTING,
        OperationKind.PROPAGATION: PacketState.PROPAGATING,
    }
    if not isinstance(operation.kind, OperationKind):
        raise ValueError("kind must be OperationKind")
    if packet.state != expected_state[operation.kind]:
        raise ValueError("Operation kind must match packet phase")
    _number(operation.started_at, "started_at")
    _number(operation.due_at, "due_at")
    _number(packet.submitted_at, "submitted_at")
    if operation.started_at < packet.submitted_at:
        raise ValueError("Operation precedes submission")
    if not isinstance(packet.spec, NetworkRequestSpec):
        raise ValueError("spec must be NetworkRequestSpec")
    duration = (
        transmission_duration(packet.spec.size_bytes, link)
        if operation.kind == OperationKind.TRANSMISSION else link.propagation_delay
    )
    if operation.due_at != operation_due_at(operation.started_at, duration):
        raise ValueError("due_at must match modeled duration")


def validate_runtime_state(runtime: NetworkRuntime) -> None:
    """Validate a stable network boundary, cross-checking all owning indexes.

    Each in-flight packet has exactly one live operation; queues, transmitters,
    and propagation lists are disjoint. Every terminal packet has exactly one
    matching notification, acknowledged at most once through a set of IDs.
    This does not prove transition history or implement delivery/rollback.
    """
    if not isinstance(runtime, NetworkRuntime):
        raise ValueError("runtime must be NetworkRuntime")
    validate_network_config(runtime.config)
    for name in ("generation", "next_packet_sequence", "next_operation_sequence",
                 "next_enqueue_sequence"):
        _integer(getattr(runtime, name), name)
    for name in ("nodes", "links", "packets", "operations", "notifications"):
        if not isinstance(getattr(runtime, name), dict):
            raise ValueError(f"{name} must be a dict")
        for key in getattr(runtime, name):
            _text(key, f"{name} registry key")
    nodes = {node.node_id: node for node in runtime.config.nodes}
    links = {link.link_id: link for link in runtime.config.links}
    if set(runtime.nodes) != set(nodes) or set(runtime.links) != set(links):
        raise ValueError("Runtime registries must match configured topology")
    for key, node in runtime.nodes.items():
        if not isinstance(node, NodeRuntime) or node.config != nodes[key]:
            raise ValueError("Node registry/config mismatch")
        if not isinstance(node.state, Availability):
            raise ValueError("Node state must be Availability")
    owned: dict[str, tuple[str, PacketState]] = {}
    for key, link in runtime.links.items():
        if not isinstance(link, LinkRuntime) or link.config != links[key]:
            raise ValueError("Link registry/config mismatch")
        if not isinstance(link.state, Availability):
            raise ValueError("Link state must be Availability")
        if not isinstance(link.queue, list) or not isinstance(
            link.propagating_packet_ids, list,
        ):
            raise ValueError("Link queues and propagation IDs must be lists")
        if len(link.queue) > link.config.queue_capacity_packets:
            raise ValueError("Waiting queue exceeds capacity")
        members = [(pid, PacketState.QUEUED) for pid in link.queue]
        members += [(pid, PacketState.PROPAGATING)
                    for pid in link.propagating_packet_ids]
        if link.transmitting_packet_id is not None:
            members.append((link.transmitting_packet_id, PacketState.TRANSMITTING))
        unavailable = link.state == Availability.DOWN or any(
            runtime.nodes[node_id].state == Availability.DOWN
            for node_id in (link.config.source, link.config.destination)
        )
        if unavailable and members:
            raise ValueError("Unavailable link/endpoints prohibit resident work")
        for packet_id, state in members:
            _text(packet_id, "owned packet_id")
            if packet_id in owned:
                raise ValueError("Packet has duplicate queue/operation ownership")
            owned[packet_id] = key, state
    for packet in runtime.packets.values():
        _packet(packet, runtime.config, set(nodes))
    operation_packets = set()
    for token, operation in runtime.operations.items():
        if not isinstance(operation, NetworkOperation) or token != operation.token:
            raise ValueError("Operation registry key/type mismatch")
        _text(operation.packet_id, "operation packet_id")
        _text(operation.link_id, "operation link_id")
        if operation.packet_id not in runtime.packets or operation.link_id not in links:
            raise ValueError("Operation references unknown packet/link")
        packet = runtime.packets[operation.packet_id]
        validate_operation(operation, packet, links[operation.link_id])
        if operation.sequence >= runtime.next_operation_sequence:
            raise ValueError("Operation sequence has not been allocated")
        if operation.packet_id in operation_packets:
            raise ValueError("Packet has multiple active operations")
        operation_packets.add(operation.packet_id)
    enqueue_sequences = set()
    terminal_ids = set()
    for packet_id, packet in runtime.packets.items():
        if packet.packet_id != packet_id or packet.generation != runtime.generation:
            raise ValueError("Packet registry identity/generation mismatch")
        if packet.sequence >= runtime.next_packet_sequence:
            raise ValueError("Packet sequence has not been allocated")
        if packet.state in (PacketState.QUEUED, PacketState.TRANSMITTING,
                            PacketState.PROPAGATING):
            if owned.get(packet_id) != (packet.link_id, packet.state):
                raise ValueError("Packet state does not match link membership")
        elif packet_id in owned:
            raise ValueError("Node/terminal packet still has link ownership")
        if packet.state == PacketState.QUEUED:
            if (packet.enqueue_sequence >= runtime.next_enqueue_sequence
                    or packet.enqueue_sequence in enqueue_sequences):
                raise ValueError("Queue sequence is unallocated or duplicated")
            enqueue_sequences.add(packet.enqueue_sequence)
        in_flight = packet.state in (PacketState.TRANSMITTING, PacketState.PROPAGATING)
        if in_flight != (packet_id in operation_packets):
            raise ValueError("In-flight state must match live callback ownership")
        if (not in_flight and packet.state not in _TERMINAL
                and runtime.nodes[packet.current_node].state == Availability.DOWN):
            raise ValueError("Live packet is resident at a DOWN node")
        if packet.state in _TERMINAL:
            terminal_ids.add(packet_id)
    if set(owned) - set(runtime.packets):
        raise ValueError("Link references unknown packet")
    if set(runtime.notifications) != terminal_ids:
        raise ValueError("Terminal packets require exactly one notification")
    for packet_id, notice in runtime.notifications.items():
        packet = runtime.packets[packet_id]
        if not isinstance(notice, NetworkNotification):
            raise ValueError("Notification must be NetworkNotification")
        _integer(notice.generation, "notification generation")
        _number(notice.timestamp, "notification timestamp")
        if (notice.packet_id != packet_id or notice.generation != runtime.generation
                or notice.pid != packet.pid or notice.source != packet.spec.source
                or notice.destination != packet.spec.destination
                or notice.timestamp != packet.finished_at
                or notice.outcome != packet.state or notice.reason != packet.reason):
            raise ValueError("Notification does not match terminal packet/owner")
        if notice.message is not None:
            _text(notice.message, "notification message")
    if not isinstance(runtime.delivered_notification_ids, set):
        raise ValueError("delivered_notification_ids must be a set")
    if not runtime.delivered_notification_ids <= terminal_ids:
        raise ValueError("Acknowledgment references unknown/nonterminal packet")
