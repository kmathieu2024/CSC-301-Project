"""Pure network boundary validation, including malformed runtime indexes."""

from copy import deepcopy
from dataclasses import replace
import math

import pytest

from src.managers.network import (
    Availability, LinkConfig, NetworkConfig, NetworkRequestSpec, NodeConfig,
    PacketState, QueuePolicy, TerminalReason, create_network_runtime,
    create_packet_runtime, operation_due_at, snapshot_network,
    transmission_duration, validate_link_config, validate_network_config,
    validate_node_config, validate_packet, validate_request_offsets,
    validate_request_spec, validate_runtime_state, validate_transition,
)
from tests.test_network_models import packet_runtime, topology


@pytest.mark.parametrize("config", [
    NetworkConfig(), topology(),
    topology(links=()),
    NetworkConfig(nodes=[NodeConfig(node_id="a")], links=[LinkConfig(
        link_id="loop", source="a", destination="a",
        bandwidth_bytes_per_time_unit=1, queue_policy=QueuePolicy.PRIORITY,
    )]),
    topology(links=topology().links + (replace(topology().links[0], link_id="ab2"),)),
    topology(links=topology().links + (replace(
        topology().links[0], link_id="ba", source="b", destination="a",
    ),)),
])
def test_valid_topologies(config: NetworkConfig) -> None:
    validate_network_config(config)
    validate_runtime_state(create_network_runtime(config))


@pytest.mark.parametrize("field,value", [
    ("nodes", [NodeConfig(node_id="a"), NodeConfig(node_id="a")]),
    ("links", topology().links * 2),
    ("links", [replace(topology().links[0], source="unknown")]),
    ("links", [replace(topology().links[0], destination="unknown")]),
    ("nodes", ["a"]), ("links", ["ab"]),
])
def test_duplicate_ids_unknown_endpoints_and_wrong_models(
    field: str, value: object,
) -> None:
    with pytest.raises(ValueError):
        validate_network_config(replace(topology(), **{field: value}))


@pytest.mark.parametrize("bad", [True, False, -1, math.inf, -math.inf, math.nan,
                                 "1", None, [], {}, 10**400])
@pytest.mark.parametrize("field", [
    "bandwidth_bytes_per_time_unit", "routing_cost", "propagation_delay",
])
def test_invalid_link_numbers(
    field: str, bad: object,
) -> None:
    with pytest.raises(ValueError):
        validate_link_config(replace(topology().links[0], **{field: bad}))


@pytest.mark.parametrize("field", ["bandwidth_bytes_per_time_unit", "routing_cost"])
def test_positive_link_fields_reject_zero(field: str) -> None:
    with pytest.raises(ValueError):
        validate_link_config(replace(topology().links[0], **{field: 0}))


@pytest.mark.parametrize("bad", [True, False, -1, 0.5, math.inf, math.nan, "1", None])
def test_invalid_capacity(bad: object) -> None:
    with pytest.raises(ValueError):
        validate_link_config(replace(topology().links[0], queue_capacity_packets=bad))


@pytest.mark.parametrize("bad", [
    True, False, 0, -1, 0.5, math.inf, math.nan, "1", None,
])
def test_invalid_hop_limit(bad: object) -> None:
    with pytest.raises(ValueError):
        validate_network_config(topology(hop_limit=bad))


@pytest.mark.parametrize("bad", ["FIFO", "PRIORITY", "unknown", [], {}, True, None])
def test_policy_requires_actual_enum(bad: object) -> None:
    with pytest.raises(ValueError, match="QueuePolicy"):
        validate_link_config(replace(topology().links[0], queue_policy=bad))


@pytest.mark.parametrize("bad", ["", " a", "a ", True, None, [], {}])
def test_node_and_link_identifiers_require_nonempty_text(bad: object) -> None:
    with pytest.raises(ValueError):
        validate_node_config(NodeConfig(node_id=bad))
    with pytest.raises(ValueError):
        validate_link_config(replace(topology().links[0], link_id=bad))
    if bad is not None:
        with pytest.raises(ValueError):
            validate_node_config(NodeConfig(node_id="a", address=bad))


@pytest.mark.parametrize("field,bad", [
    ("source", "unknown"), ("destination", "unknown"),
    ("source", []), ("destination", None),
    ("size_bytes", True), ("size_bytes", 0), ("size_bytes", -1),
    ("size_bytes", 1.5), ("size_bytes", math.inf),
    ("priority", True), ("priority", -1), ("priority", 1.5),
    ("priority", math.nan), ("priority", "1"),
])
def test_invalid_request_fields(
    field: str, bad: object,
) -> None:
    spec = NetworkRequestSpec(source="a", destination="b", size_bytes=1)
    with pytest.raises(ValueError):
        validate_request_spec(replace(spec, **{field: bad}), topology())


def test_numeric_boundaries_and_disconnected_source_equal_destination() -> None:
    link = replace(topology().links[0], propagation_delay=0,
                   queue_capacity_packets=0, routing_cost=5e-324)
    validate_link_config(link)
    assert transmission_duration(1, link) == 0.01
    assert operation_due_at(0, 5e-324) == 5e-324
    assert operation_due_at(10**308, 0) == 10**308
    spec = NetworkRequestSpec(source="a", destination="a", size_bytes=1, priority=0)
    validate_request_spec(spec, topology(links=()))
    validate_request_spec(replace(spec, destination="b"), topology(links=()))


@pytest.mark.parametrize("start,duration", [
    (1e308, 1e308), (1e308, 1), (1, 5e-324),
    (True, 1), (0, True), (-1, 1), (0, -1),
    (math.inf, 0), (0, math.nan), (10**400, 0),
])
def test_invalid_or_unrepresentable_due_times(
    start: object, duration: object,
) -> None:
    with pytest.raises(ValueError):
        operation_due_at(start, duration)


@pytest.mark.parametrize("size,bandwidth", [
    (10**308, 1e-308), (1, 10**400), (10**400, 1),
    (1, 0), (True, 1), (0, 1),
])
def test_transmission_duration_overflow_and_invalid_inputs(
    size: object, bandwidth: object,
) -> None:
    with pytest.raises(ValueError):
        transmission_duration(size, replace(
            topology().links[0], bandwidth_bytes_per_time_unit=bandwidth,
        ))


@pytest.mark.parametrize("offset", [True, -1, 1.5, "0", "01", None, 4])
def test_invalid_offsets(offset: object) -> None:
    spec = NetworkRequestSpec(source="a", destination="b", size_bytes=1)
    with pytest.raises(ValueError):
        validate_request_offsets({offset: spec}, 4, topology())


def test_offsets_and_factories_are_pure_and_validate_before_creation() -> None:
    config = topology()
    spec = NetworkRequestSpec(source="a", destination="b", size_bytes=1)
    requests = {0: spec, 3: spec}
    before = deepcopy(requests)
    validate_request_offsets(requests, 4, config)
    assert requests == before
    for changes in ({"pid": ""}, {"generation": True}, {"now": math.nan},
                    {"sequence": -1}):
        arguments = {"pid": "P1", "generation": 0, "sequence": 0, "now": 0}
        with pytest.raises(ValueError):
            create_packet_runtime(spec, config, **(arguments | changes))
    with pytest.raises(ValueError):
        create_network_runtime(topology(hop_limit=0))


@pytest.mark.parametrize("field,bad", [
    ("packet_id", "other"), ("sequence", True), ("generation", -1),
    ("operation_generation", True), ("pid", ""), ("state", "AT_NODE"),
    ("submitted_at", math.inf), ("current_node", "unknown"),
    ("current_node", "b"), ("completed_hops", 17),
    ("queue_wait_time", math.nan), ("transmission_time", True),
    ("propagation_time", -1), ("finished_at", 1),
    ("reason", TerminalReason.NO_ROUTE), ("operation_token", "old"),
    ("queued_at", 0), ("enqueue_sequence", 0), ("link_id", "ab"),
])
def test_invalid_packet_lifecycle_fields(
    field: str, bad: object,
) -> None:
    runtime = packet_runtime()
    setattr(runtime.packets["packet-0"], field, bad)
    with pytest.raises(ValueError):
        validate_packet(runtime.packets["packet-0"], runtime.config)


@pytest.mark.parametrize("problem", [
    "missing_node", "missing_link", "unknown_queued", "duplicate_queued",
    "queue_capacity", "queue_state", "unknown_operation", "missing_operation",
    "stale_run", "stale_packet", "wrong_token", "wrong_due", "wrong_kind",
    "wrong_owner", "down_link", "down_node", "unallocated_packet",
    "unallocated_operation", "unallocated_enqueue", "queued_operation",
    "missing_notice", "wrong_notice_pid", "wrong_notice_outcome", "wrong_notice_time",
    "wrong_notice_generation", "unknown_ack", "duplicate_propagation",
    "cross_membership", "terminal_owned", "delivered_wrong_node", "live_notice",
    "terminal_wrong_reason", "terminal_before_submission", "unknown_spec",
])
def test_malformed_runtime_indexes_are_rejected_without_mutation(
    problem: str,
) -> None:
    runtime = packet_runtime(PacketState.TRANSMITTING)
    packet = runtime.packets["packet-0"]
    op = next(iter(runtime.operations.values()))
    link = runtime.links["ab"]
    if problem == "missing_node":
        runtime.nodes.pop("a")
    elif problem == "missing_link":
        runtime.links.clear()
    elif problem in ("unknown_queued", "duplicate_queued", "queue_capacity",
                     "queue_state", "unallocated_enqueue", "queued_operation"):
        runtime = packet_runtime(PacketState.QUEUED)
        if problem == "unknown_queued":
            runtime.links["ab"].queue.append("unknown")
        elif problem == "duplicate_queued":
            runtime.links["ab"].queue.append("packet-0")
        elif problem == "queue_capacity":
            runtime.config = replace(runtime.config, links=(replace(
                runtime.config.links[0], queue_capacity_packets=0,
            ),))
            runtime.links["ab"].config = runtime.config.links[0]
        elif problem == "queue_state":
            runtime.links["ab"].queue.clear()
        elif problem == "unallocated_enqueue":
            runtime.next_enqueue_sequence = 0
        else:
            runtime.operations[op.token] = op
    elif problem == "unknown_operation":
        runtime.operations[op.token] = replace(op, packet_id="unknown")
    elif problem == "missing_operation":
        runtime.operations.clear()
    elif problem == "stale_run":
        runtime.generation = 1
    elif problem == "stale_packet":
        packet.operation_generation = 1
    elif problem == "wrong_token":
        packet.operation_token = "old"
    elif problem == "wrong_due":
        runtime.operations[op.token] = replace(op, due_at=2)
    elif problem == "wrong_kind":
        runtime.operations[op.token] = replace(op, kind="TRANSMISSION")
    elif problem == "wrong_owner":
        link.transmitting_packet_id = None
    elif problem == "down_link":
        link.state = Availability.DOWN
    elif problem == "down_node":
        runtime.nodes["b"].state = Availability.DOWN
    elif problem == "unallocated_packet":
        runtime.next_packet_sequence = 0
    elif problem == "unallocated_operation":
        runtime.next_operation_sequence = 0
    elif problem == "unknown_ack":
        runtime.delivered_notification_ids.add("unknown")
    elif problem == "duplicate_propagation":
        runtime = packet_runtime(PacketState.PROPAGATING)
        runtime.links["ab"].propagating_packet_ids.append("packet-0")
    elif problem == "cross_membership":
        link.propagating_packet_ids.append("packet-0")
    elif problem == "unknown_spec":
        packet.spec = None
    elif problem == "live_notice":
        runtime.notifications = packet_runtime(PacketState.DELIVERED).notifications
    else:
        runtime = packet_runtime(PacketState.DELIVERED)
        packet = runtime.packets["packet-0"]
        notice = runtime.notifications["packet-0"]
        if problem == "missing_notice":
            runtime.notifications.clear()
        elif problem == "terminal_owned":
            runtime.links["ab"].queue.append("packet-0")
        elif problem == "delivered_wrong_node":
            packet.current_node = "a"
        elif problem == "terminal_wrong_reason":
            packet.reason = TerminalReason.NO_ROUTE
        elif problem == "terminal_before_submission":
            packet.finished_at = -1
        else:
            changes = {
                "wrong_notice_pid": {"pid": "P2"},
                "wrong_notice_outcome": {"outcome": PacketState.FAILED},
                "wrong_notice_time": {"timestamp": 1},
                "wrong_notice_generation": {"generation": 1},
            }[problem]
            runtime.notifications["packet-0"] = replace(notice, **changes)
    before = deepcopy(runtime)
    with pytest.raises(ValueError):
        validate_runtime_state(runtime)
    with pytest.raises(ValueError):
        snapshot_network(runtime)
    assert runtime == before


@pytest.mark.parametrize("previous,following", [
    ("AT_NODE", PacketState.QUEUED), (PacketState.QUEUED, "TRANSMITTING"),
    (PacketState.TRANSMITTING, PacketState.DELIVERED),
    (PacketState.QUEUED, PacketState.DELIVERED),
    (PacketState.PROPAGATING, PacketState.QUEUED),
])
def test_invalid_lifecycle_edges(
    previous: object, following: object,
) -> None:
    with pytest.raises(ValueError):
        validate_transition(previous, following)
