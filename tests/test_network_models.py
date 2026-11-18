"""Standalone network data contracts and stable-boundary runtime invariants."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import json

import pytest

from src.managers.network import (
    Availability, LinkConfig, NetworkConfig, NetworkNotification, NetworkOperation,
    NetworkRequestSpec, NetworkRuntime, NetworkUpdate, NodeConfig, OperationKind,
    PacketState,
    QueuePolicy, TerminalReason, callback_token, create_network_runtime,
    create_packet_runtime, packet_identifier, snapshot_network,
    validate_runtime_state, validate_transition,
)


def topology(**changes: object) -> NetworkConfig:
    """Small directed network; no implicit reverse link."""
    return replace(NetworkConfig(
        nodes=(NodeConfig(node_id="a"), NodeConfig(node_id="b")),
        links=(LinkConfig(
            link_id="ab", source="a", destination="b",
            bandwidth_bytes_per_time_unit=100, propagation_delay=1,
            queue_capacity_packets=2,
        ),),
    ), **changes)


def packet_runtime(
    state: PacketState = PacketState.AT_NODE,
) -> NetworkRuntime:
    """Build a valid stable boundary directly, without executing a manager."""
    runtime = create_network_runtime(topology())
    packet = create_packet_runtime(
        NetworkRequestSpec(source="a", destination="b", size_bytes=100),
        runtime.config, pid="P1", sequence=0, generation=0, now=0,
    )
    runtime.packets[packet.packet_id] = packet
    runtime.next_packet_sequence = 1
    packet.state = state
    if state == PacketState.QUEUED:
        packet.link_id = "ab"
        packet.queued_at = 0
        packet.enqueue_sequence = 0
        runtime.next_enqueue_sequence = 1
        runtime.links["ab"].queue.append(packet.packet_id)
    elif state in (PacketState.TRANSMITTING, PacketState.PROPAGATING):
        packet.link_id = "ab"
        packet.operation_token = callback_token(0, 0)
        operation = NetworkOperation(
            token=packet.operation_token, generation=0, sequence=0,
            packet_generation=0, packet_id=packet.packet_id, link_id="ab",
            kind=(OperationKind.TRANSMISSION if state == PacketState.TRANSMITTING
                  else OperationKind.PROPAGATION),
            started_at=0, due_at=1,
        )
        runtime.operations[operation.token] = operation
        runtime.next_operation_sequence = 1
        if state == PacketState.TRANSMITTING:
            runtime.links["ab"].transmitting_packet_id = packet.packet_id
        else:
            runtime.links["ab"].propagating_packet_ids.append(packet.packet_id)
    elif state in (PacketState.DELIVERED, PacketState.DROPPED,
                   PacketState.FAILED, PacketState.CANCELLED):
        packet.finished_at = 2
        packet.reason = {
            PacketState.DELIVERED: TerminalReason.DELIVERED,
            PacketState.DROPPED: TerminalReason.NO_ROUTE,
            PacketState.FAILED: TerminalReason.LINK_FAILURE,
            PacketState.CANCELLED: TerminalReason.CANCELLED,
        }[state]
        if state == PacketState.DELIVERED:
            packet.current_node = "b"
            packet.completed_hops = 1
        runtime.notifications[packet.packet_id] = NetworkNotification(
            packet_id=packet.packet_id, generation=0, pid="P1", source="a",
            destination="b", timestamp=2, outcome=state, reason=packet.reason,
        )
    return runtime


@pytest.mark.parametrize("state", list(PacketState))
def test_valid_stable_packet_boundaries(state: PacketState) -> None:
    runtime = packet_runtime(state)
    before = deepcopy(runtime)
    validate_runtime_state(runtime)
    json.dumps(snapshot_network(runtime), allow_nan=False)
    assert runtime == before


def test_config_collections_are_copied_and_models_are_frozen() -> None:
    nodes = [NodeConfig(node_id="a")]
    links = []
    config = NetworkConfig(nodes=nodes, links=links)
    nodes.clear()
    links.append("invalid")
    assert config.nodes == (NodeConfig(node_id="a"),)
    assert config.links == ()
    for obj, name, value in (
        (config, "hop_limit", 1), (config.nodes[0], "node_id", "b"),
        (topology().links[0], "queue_policy", QueuePolicy.PRIORITY),
        (NetworkRequestSpec(source="a", destination="a", size_bytes=1),
         "size_bytes", 2),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(obj, name, value)


def test_operation_notification_and_batches_are_frozen_and_copied() -> None:
    operation = next(iter(packet_runtime(PacketState.TRANSMITTING).operations.values()))
    notice = next(iter(packet_runtime(PacketState.DELIVERED).notifications.values()))
    operations, notices = [operation], [notice]
    update = NetworkUpdate(operations=operations, notifications=notices)
    operations.clear()
    notices.clear()
    assert update.operations == (operation,)
    assert update.notifications == (notice,)
    for obj, name in ((operation, "due_at"), (notice, "timestamp"),
                      (update, "operations")):
        with pytest.raises(FrozenInstanceError):
            setattr(obj, name, None)
    with pytest.raises(ValueError, match="duplicate"):
        NetworkUpdate(operations=[operation, operation])
    with pytest.raises(ValueError, match="duplicate"):
        NetworkUpdate(notifications=[notice, notice])


def test_factories_allocate_independent_state_and_stable_ids() -> None:
    first = create_network_runtime(topology(), generation=4)
    second = create_network_runtime(topology(), generation=5)
    first.links["ab"].queue.append("packet-0")
    first.nodes["a"].state = Availability.DOWN
    assert second.links["ab"].queue == []
    assert second.nodes["a"].state == Availability.UP
    assert second.generation == 5
    assert second.packets == second.operations == second.notifications == {}
    assert packet_identifier(0) == packet_identifier(0) == "packet-0"
    assert callback_token(4, 0) != callback_token(5, 0)
    assert callback_token(4, 0) != callback_token(4, 1)
    for generation in (0, 1):
        packet = create_packet_runtime(
            NetworkRequestSpec(source="a", destination="b", size_bytes=10),
            topology(), pid="P1", sequence=2, generation=generation, now=3,
        )
        assert packet.packet_id == "packet-2"
        assert packet.generation == generation
        assert packet.submitted_at == 3
        assert packet.current_node == "a"
        assert packet.state == PacketState.AT_NODE


def test_snapshot_is_detached_and_hides_callback_tokens() -> None:
    runtime = packet_runtime(PacketState.TRANSMITTING)
    before = deepcopy(runtime)
    snapshot = snapshot_network(runtime)
    assert "token" not in snapshot["operations"][0]
    assert "operation_token" not in snapshot["packets"]["packet-0"]
    snapshot["config"]["nodes"][0]["node_id"] = "changed"
    snapshot["links"]["ab"]["queue"].append("changed")
    snapshot["packets"]["packet-0"]["pid"] = "changed"
    snapshot["operations"].clear()
    assert runtime == before
    assert snapshot_network(runtime) == snapshot_network(runtime)


def test_waiting_only_capacity_allows_concurrent_propagation_and_transmission() -> None:
    runtime = packet_runtime(PacketState.TRANSMITTING)
    runtime.config = replace(runtime.config, links=(replace(
        runtime.config.links[0], queue_capacity_packets=0,
    ),))
    runtime.links["ab"].config = runtime.config.links[0]
    for sequence in (1, 2):
        packet = create_packet_runtime(
            NetworkRequestSpec(source="a", destination="b", size_bytes=100),
            runtime.config, pid=f"P{sequence + 1}", sequence=sequence,
            generation=0, now=0,
        )
        packet.state = PacketState.PROPAGATING
        packet.link_id = "ab"
        packet.operation_token = callback_token(0, sequence)
        runtime.packets[packet.packet_id] = packet
        runtime.links["ab"].propagating_packet_ids.append(packet.packet_id)
        runtime.operations[packet.operation_token] = NetworkOperation(
            token=packet.operation_token, generation=0, sequence=sequence,
            packet_generation=0, packet_id=packet.packet_id, link_id="ab",
            kind=OperationKind.PROPAGATION, started_at=0, due_at=1,
        )
    runtime.next_packet_sequence = runtime.next_operation_sequence = 3
    validate_runtime_state(runtime)
    assert len(runtime.links["ab"].propagating_packet_ids) == 2


@pytest.mark.parametrize("terminal", [
    PacketState.DELIVERED, PacketState.DROPPED, PacketState.FAILED,
    PacketState.CANCELLED,
])
@pytest.mark.parametrize("following", list(PacketState))
def test_terminal_states_cannot_transition_or_revive(
    terminal: PacketState, following: PacketState,
) -> None:
    with pytest.raises(ValueError, match="transition"):
        validate_transition(terminal, following)


@pytest.mark.parametrize("previous,following", [
    (PacketState.AT_NODE, PacketState.QUEUED),
    (PacketState.AT_NODE, PacketState.TRANSMITTING),
    (PacketState.AT_NODE, PacketState.DELIVERED),
    (PacketState.QUEUED, PacketState.AT_NODE),
    (PacketState.QUEUED, PacketState.TRANSMITTING),
    (PacketState.TRANSMITTING, PacketState.PROPAGATING),
    (PacketState.PROPAGATING, PacketState.AT_NODE),
    (PacketState.PROPAGATING, PacketState.DELIVERED),
])
def test_permitted_lifecycle_edges(
    previous: PacketState, following: PacketState,
) -> None:
    validate_transition(previous, following)


@pytest.mark.parametrize("bad", [True, -1, 0.5, "1", None])
def test_identity_functions_reject_invalid_sequences(bad: object) -> None:
    with pytest.raises(ValueError):
        packet_identifier(bad)
    with pytest.raises(ValueError):
        callback_token(bad, 0)
    with pytest.raises(ValueError):
        callback_token(0, bad)


def test_zero_hop_delivery_and_notification_acknowledgment() -> None:
    runtime = packet_runtime(PacketState.DELIVERED)
    packet = runtime.packets["packet-0"]
    packet.spec = replace(packet.spec, destination="a")
    packet.current_node = "a"
    packet.completed_hops = 0
    packet.finished_at = 0
    runtime.notifications[packet.packet_id] = replace(
        runtime.notifications[packet.packet_id], destination="a", timestamp=0,
    )
    runtime.delivered_notification_ids.add(packet.packet_id)
    runtime.delivered_notification_ids.add(packet.packet_id)
    validate_runtime_state(runtime)
    assert len(runtime.delivered_notification_ids) == 1
