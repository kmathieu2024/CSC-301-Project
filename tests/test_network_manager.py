"""Standalone network execution, failure isolation, and exact accounting."""

from copy import deepcopy
from dataclasses import replace
import heapq
import json
import math
from typing import Any

import pytest

from src.managers.network import (
    Availability, LinkConfig, NetworkConfig, NetworkManager, NetworkOperation,
    NetworkRequestSpec, NetworkUpdate, NodeConfig, OperationKind, PacketState,
    QueuePolicy, TerminalReason,
)


def config(
    *, capacity: int = 4, policy: QueuePolicy = QueuePolicy.FIFO,
    delay: float = 1, bandwidth: float = 100, multi: bool = False,
) -> NetworkConfig:
    """Each 100-byte hop takes one transmission unit plus configurable delay."""
    links = [LinkConfig(
        link_id="ab", source="a", destination="b",
        bandwidth_bytes_per_time_unit=bandwidth, propagation_delay=delay,
        queue_capacity_packets=capacity, queue_policy=policy,
    )]
    if multi:
        links.append(replace(links[0], link_id="bc", source="b", destination="c"))
    return NetworkConfig(
        nodes=tuple(NodeConfig(node_id=name) for name in ("a", "b", "c", "d")),
        links=tuple(links),
    )


def spec(**changes: Any) -> NetworkRequestSpec:
    """A standard one-hop packet."""
    return replace(NetworkRequestSpec(source="a", destination="b", size_bytes=100),
                   **changes)


def execute(
    manager: NetworkManager, initial: NetworkUpdate, now: float = 0,
) -> tuple[float, list[Any], list[NetworkOperation]]:
    """Caller-owned event queue; equal timestamps retain instruction insertion order."""
    queue = []
    sequence = 0
    notices, completed = list(initial.notifications), []

    def register(update: NetworkUpdate) -> None:
        nonlocal sequence
        for operation in update.operations:
            heapq.heappush(queue, (operation.due_at, sequence, operation))
            sequence += 1

    register(initial)
    steps = 0
    while queue:
        now, _, operation = heapq.heappop(queue)
        update = manager.complete_operation(operation, now)
        completed.append(operation)
        notices.extend(update.notifications)
        register(update)
        manager.validate()
        steps += 1
        assert steps < 1000, "Zero-time callbacks failed to terminate"
    return now, notices, completed


def independent_config() -> NetworkConfig:
    """Two independent links, enabling isolation checks."""
    base = config(delay=2)
    return replace(base, links=base.links + (replace(
        base.links[0], link_id="cd", source="c", destination="d",
    ),))


def reroute_config(capacity: int = 1) -> NetworkConfig:
    """Preferred direct link and a more expensive two-hop alternate."""
    base = config(delay=0, capacity=4)
    return replace(base, links=base.links + (
        replace(base.links[0], link_id="ac", destination="c", routing_cost=2,
                queue_capacity_packets=capacity),
        replace(base.links[0], link_id="cb", source="c", routing_cost=2),
    ))


def test_hand_calculated_three_packet_two_hop_fixture() -> None:
    """P0/P1/P2 finish at 4/5/6; two links each serialize three time units."""
    manager = NetworkManager(config(multi=True))
    ids = [manager.submit_packet(f"P{i}", spec(destination="c"), 0) for i in range(3)]
    end, notices, operations = execute(manager, manager.schedule_network(0))
    assert end == 6
    assert [n.packet_id for n in notices] == ids
    assert [n.timestamp for n in notices] == [4, 5, 6]
    assert len(operations) == 12
    metrics = manager.metrics(end)
    assert metrics["delivered"] == metrics["submitted"] == 3
    assert metrics["delivered_payload_bytes"] == 300
    assert metrics["throughput"] == 0.5
    assert metrics["goodput"] == 50
    assert metrics["average_delivery_latency"] == 5
    assert metrics["average_queue_wait"] == 1
    assert metrics["packets"][ids[0]]["queue_wait_time"] == 0
    assert metrics["packets"][ids[1]]["queue_wait_time"] == 1
    assert metrics["packets"][ids[2]]["queue_wait_time"] == 2
    for key in ("ab", "bc"):
        link = metrics["links"][key]
        assert link["busy_time"] == link["idle_time"] == 3
        assert link["utilization"] == 0.5
        assert link["serialized_bytes"] == 300
    assert metrics["links"]["ab"]["queue_length_integral"] == 3
    assert metrics["links"]["ab"]["average_queue_length"] == 0.5
    assert metrics["links"]["bc"]["queue_length_integral"] == 0
    assert manager.snapshot(end)["packet_paths"] == {key: ["ab", "bc"] for key in ids}


@pytest.mark.parametrize("delay,expected", [(0, 1), (1, 2), (2.5, 3.5)])
def test_one_hop_exact_boundaries(delay: float, expected: float) -> None:
    manager = NetworkManager(config(delay=delay))
    key = manager.submit_packet("P", spec(), 0)
    update = manager.schedule_network(0)
    assert len(update.operations) == 1
    tx = update.operations[0]
    assert tx.kind == OperationKind.TRANSMISSION and tx.due_at == 1
    update = manager.complete_operation(tx, 1)
    assert update.notifications == ()
    assert update.operations[0].kind == OperationKind.PROPAGATION
    _, notices, _ = execute(manager, update, 1)
    assert notices[0].timestamp == expected
    assert notices[0].packet_id == key
    assert manager.metrics(expected)["packets"][key]["transmission_time"] == 1
    assert manager.metrics(expected)["packets"][key]["propagation_time"] == delay


def test_transmission_releases_link_while_previous_packet_propagates() -> None:
    manager = NetworkManager(config(delay=5))
    first = manager.submit_packet("P1", spec(), 0)
    second = manager.submit_packet("P2", spec(), 0)
    tx = manager.schedule_network(0).operations[0]
    update = manager.complete_operation(tx, 1)
    assert [op.kind for op in update.operations] == [
        OperationKind.PROPAGATION, OperationKind.TRANSMISSION,
    ]
    assert update.operations[0].packet_id == first
    assert update.operations[1].packet_id == second
    end, notices, _ = execute(manager, update, 1)
    assert end == 7
    assert [n.timestamp for n in notices] == [6, 7]
    assert manager.metrics(end)["links"]["ab"]["busy_time"] == 2


@pytest.mark.parametrize("policy,expected", [
    (QueuePolicy.FIFO, ["P0", "P1", "P2"]),
    (QueuePolicy.PRIORITY, ["P0", "P2", "P1"]),
])
def test_nonpreemptive_queue_contention(
    policy: QueuePolicy, expected: list[str],
) -> None:
    manager = NetworkManager(config(policy=policy, delay=0))
    for pid, priority in (("P0", 9), ("P1", 3), ("P2", 0)):
        manager.submit_packet(pid, spec(priority=priority), 0)
    end, notices, _ = execute(manager, manager.schedule_network(0))
    assert end == 3
    assert [n.pid for n in notices] == expected


@pytest.mark.parametrize("capacity", [0, 1, 2])
def test_waiting_only_capacity_and_drop_tail(capacity: int) -> None:
    manager = NetworkManager(config(capacity=capacity))
    ids = [manager.submit_packet(str(i), spec(), 0) for i in range(capacity + 2)]
    update = manager.schedule_network(0)
    assert len(update.notifications) == 1
    assert update.notifications[0].packet_id == ids[-1]
    assert update.notifications[0].reason == TerminalReason.QUEUE_FULL
    end, notices, _ = execute(manager, update)
    assert len(notices) == capacity + 2
    assert manager.metrics(end)["delivered"] == capacity + 1
    assert manager.metrics(end)["dropped"] == 1


def test_immediate_local_and_unreachable_packets_notify_once() -> None:
    manager = NetworkManager(config())
    local = manager.submit_packet("P0", spec(destination="a"), 0)
    unreachable = manager.submit_packet("P1", spec(destination="d"), 0)
    update = manager.schedule_network(0)
    assert update.operations == ()
    assert [(n.packet_id, n.reason) for n in update.notifications] == [
        (local, TerminalReason.DELIVERED), (unreachable, TerminalReason.NO_ROUTE),
    ]
    assert manager.schedule_network(0).notifications == ()
    assert manager.cancel_packet(local, 0).notifications == ()
    assert manager.drain_notifications(0) == ()
    assert manager.metrics(0)["throughput"] is None
    json.dumps(manager.snapshot(0), allow_nan=False)


def test_hop_limit_delivers_at_limit_but_drops_before_an_extra_hop() -> None:
    manager = NetworkManager(replace(config(multi=True), hop_limit=1))
    first = manager.submit_packet("P0", spec(destination="c"), 0)
    second = manager.submit_packet("P1", spec(), 0)
    end, notices, _ = execute(manager, manager.schedule_network(0))
    assert [(n.packet_id, n.reason) for n in notices] == [
        (first, TerminalReason.HOP_LIMIT), (second, TerminalReason.DELIVERED),
    ]
    assert end == 3
    assert manager.snapshot(end)["packet_paths"][first] == ["ab"]


@pytest.mark.parametrize("phase", ["queued", "transmission", "propagation"])
def test_cancellation_aborts_only_owned_work_and_invalidates_callbacks(
    phase: str,
) -> None:
    manager = NetworkManager(independent_config())
    first = manager.submit_packet("P0", spec(), 0)
    second = manager.submit_packet("P1", spec(), 0)
    other = manager.submit_packet("P2", spec(source="c", destination="d"), 0)
    update = manager.schedule_network(0)
    old = update.operations[0]
    target, now = (second, 0) if phase == "queued" else (first, 0.5)
    independent = next(op for op in update.operations if op.packet_id == other)
    if phase == "propagation":
        continuation = manager.complete_operation(old, 1)
        old = continuation.operations[0]
        now = 1
    update = manager.cancel_packet(target, now)
    assert len(update.notifications) == 1
    assert update.notifications[0].outcome == PacketState.CANCELLED
    assert manager.cancel_packet(target, now).notifications == ()
    if phase != "queued":
        assert manager.complete_operation(old, now).notifications == ()
    assert manager.runtime.operations[independent.token] == independent
    assert manager.runtime.packets[other].state == PacketState.TRANSMITTING
    manager.validate()


@pytest.mark.parametrize("phase", [
    OperationKind.TRANSMISSION, OperationKind.PROPAGATION,
])
def test_link_failure_in_each_phase_and_recovery(phase: OperationKind) -> None:
    manager = NetworkManager(config(delay=2))
    key = manager.submit_packet("P0", spec(), 0)
    old = manager.schedule_network(0).operations[0]
    now = 0.5
    if phase == OperationKind.PROPAGATION:
        old = manager.complete_operation(old, 1).operations[0]
        now = 2
    update = manager.fail_link("ab", now)
    assert update.notifications[0].reason == TerminalReason.LINK_FAILURE
    assert manager.complete_operation(old, now) == NetworkUpdate()
    metrics = manager.metrics(now)
    expected_tx = 0.5 if phase == OperationKind.TRANSMISSION else 1
    assert metrics["packets"][key]["transmission_time"] == expected_tx
    assert metrics["links"]["ab"]["busy_time"] == expected_tx
    assert manager.recover_link("ab", now).operations == ()
    assert manager.runtime.packets[key].state == PacketState.FAILED
    manager.submit_packet("new", spec(), now)
    _, notices, _ = execute(manager, manager.schedule_network(now), now)
    assert len(notices) == 1 and notices[0].pid == "new"


@pytest.mark.parametrize("capacity,delivered,dropped", [
    (0, 1, 2), (1, 2, 1), (2, 3, 0),
])
def test_reroute_order_and_destination_buffer_capacity(
    capacity: int, delivered: int, dropped: int,
) -> None:
    manager = NetworkManager(reroute_config(capacity))
    keys = [manager.submit_packet(f"P{i}", spec(), 0) for i in range(4)]
    old = manager.schedule_network(0).operations[0]
    update = manager.fail_link("ab", 0)
    assert manager.complete_operation(old, 0) == NetworkUpdate()
    end, notices, _ = execute(manager, update)
    metrics = manager.metrics(end)
    assert metrics["failed"] == 1
    assert metrics["delivered"] == delivered
    assert metrics["dropped"] == dropped
    delivered_ids = [n.packet_id for n in notices
                     if n.outcome == PacketState.DELIVERED]
    assert delivered_ids == keys[1:delivered + 1]
    assert all(manager.snapshot(end)["packet_paths"][key] == ["ac", "cb"]
               for key in keys[1:delivered + 1])


@pytest.mark.parametrize("failed_node", ["a", "b", "c"])
def test_node_failure_preserves_independent_devices_and_recovery(
    failed_node: str,
) -> None:
    manager = NetworkManager(independent_config())
    first = manager.submit_packet("P0", spec(), 0)
    manager.submit_packet("P1", spec(), 0)
    other = manager.submit_packet("P2", spec(source="c", destination="d"), 0)
    manager.schedule_network(0)
    update = manager.fail_node(failed_node, 0)
    manager.validate()
    assert manager.runtime.nodes[failed_node].state == Availability.DOWN
    unaffected = other if failed_node in ("a", "b") else first
    assert manager.runtime.packets[unaffected].state == PacketState.TRANSMITTING
    assert update.notifications
    assert manager.recover_node(failed_node, 0).notifications == ()
    assert manager.runtime.nodes[failed_node].state == Availability.UP


def test_simultaneous_independent_completions_and_no_duplicate_publish() -> None:
    manager = NetworkManager(independent_config())
    manager.submit_packet("P0", spec(), 0)
    manager.submit_packet("P1", spec(source="c", destination="d"), 0)
    update = manager.schedule_network(0)
    assert manager.schedule_network(0) == NetworkUpdate()
    end, notices, completed = execute(manager, update)
    assert end == 3
    assert [n.pid for n in notices] == ["P0", "P1"]
    for op in completed:
        assert manager.complete_operation(op, end) == NetworkUpdate()
    assert manager.metrics(end)["terminal_notifications"] == 2


def test_reset_invalidates_callbacks_and_replays_observable_results() -> None:
    manager = NetworkManager(config())
    manager.submit_packet("P", spec(), 0)
    old = manager.schedule_network(0).operations[0]
    manager.reset()
    assert manager.complete_operation(old, 0) == NetworkUpdate()
    assert manager.metrics(0)["submitted"] == 0
    snapshots = []
    for _ in range(2):
        manager.submit_packet("P", spec(), 0)
        end, _, _ = execute(manager, manager.schedule_network(0))
        snapshots.append((manager.metrics(end), manager.snapshot(end)["events"],
                          manager.snapshot(end)["hop_history"]))
        manager.reset()
    assert snapshots[0] == snapshots[1]
    assert manager.runtime.generation == 3


def test_partial_metrics_and_detached_read_only_snapshots() -> None:
    manager = NetworkManager(config(delay=2))
    first = manager.submit_packet("P0", spec(), 0)
    second = manager.submit_packet("P1", spec(), 0)
    manager.schedule_network(0)
    before = deepcopy(manager.__dict__)
    snapshot = manager.snapshot(0.5)
    metrics = snapshot["metrics"]
    assert metrics["links"]["ab"]["busy_time"] == 0.5
    assert metrics["links"]["ab"]["queue_length_integral"] == 0.5
    assert metrics["links"]["ab"]["average_queue_length"] == 1
    assert metrics["packets"][first]["transmission_time"] == 0.5
    assert metrics["packets"][second]["queue_wait_time"] == 0.5
    json.dumps(snapshot, allow_nan=False)
    snapshot["hop_history"][first].clear()
    snapshot["links"].clear()
    detached = manager.runtime
    detached.links.clear()
    assert manager.__dict__ == before


def test_unavailable_idle_and_busy_denominators() -> None:
    manager = NetworkManager(config())
    manager.fail_link("ab", 1)
    manager.recover_link("ab", 3)
    manager.submit_packet("P", spec(), 3)
    end, _, _ = execute(manager, manager.schedule_network(3), 3)
    metrics = manager.metrics(end)
    assert end == 5
    link = metrics["links"]["ab"]
    assert link["busy_time"] == 1
    assert link["unavailable_time"] == link["idle_time"] == 2
    assert link["utilization"] == 0.2


@pytest.mark.parametrize("size,bandwidth", [(1, 1e308), (10**308, 1)])
def test_extreme_finite_durations_are_strict_json_safe(
    size: int, bandwidth: float,
) -> None:
    manager = NetworkManager(config(delay=0, bandwidth=bandwidth))
    manager.submit_packet("P", spec(size_bytes=size), 0)
    initial = manager.schedule_network(0)
    half = initial.operations[0].due_at / 2
    json.dumps(manager.snapshot(half), allow_nan=False)
    end, _, _ = execute(manager, initial)
    json.dumps(manager.snapshot(end), allow_nan=False)
    assert manager.metrics(end)["delivered"] == 1


def test_unrepresentable_throughput_is_null_not_zero() -> None:
    manager = NetworkManager(config(delay=0, bandwidth=1e308))
    for i in range(2):
        manager.submit_packet(str(i), spec(size_bytes=1), 0)
    end, _, _ = execute(manager, manager.schedule_network(0))
    assert manager.metrics(end)["throughput"] == 1e308
    # Goodput overflow with many zero-hop packets over the smallest positive time.
    manager = NetworkManager(config())
    manager.submit_packet("P", spec(destination="a"), 0)
    manager.schedule_network(0)
    assert manager.metrics(5e-324)["throughput"] is None
    assert manager.metrics(5e-324)["goodput"] is None
    json.dumps(manager.snapshot(5e-324), allow_nan=False)


@pytest.mark.parametrize("now", [-1, math.inf, math.nan, True, "0"])
def test_invalid_time_and_request_rejection_do_not_mutate(now: object) -> None:
    manager = NetworkManager(config())
    before = deepcopy(manager.__dict__)
    with pytest.raises(ValueError):
        manager.submit_packet("P", spec(), now)
    assert manager.__dict__ == before
    with pytest.raises(ValueError):
        manager.submit_packet("P", spec(source="unknown"), 0)
    assert manager.__dict__ == before


def test_derived_overflow_is_rejected_before_acceptance() -> None:
    manager = NetworkManager(config(bandwidth=1e-308))
    before = deepcopy(manager.__dict__)
    with pytest.raises(ValueError):
        manager.submit_packet("P", spec(size_bytes=100), 0)
    assert manager.__dict__ == before


def test_time_cannot_skip_unpublished_or_due_operations() -> None:
    manager = NetworkManager(config())
    manager.submit_packet("P", spec(), 0)
    with pytest.raises(ValueError, match="Publish"):
        manager.snapshot(0.5)
    tx = manager.schedule_network(0).operations[0]
    with pytest.raises(ValueError, match="due"):
        manager.metrics(2)
    with pytest.raises(ValueError, match="due_at"):
        manager.complete_operation(tx, 0.5)
    with pytest.raises(ValueError):
        manager.cancel_packet("unknown", 0)
    manager.complete_operation(tx, 1)
    with pytest.raises(ValueError, match="precedes"):
        manager.metrics(0)


@pytest.mark.parametrize("index", [0, 1])
def test_partial_callback_registration_aborts_only_unregistered_suffix(
    index: int,
) -> None:
    manager = NetworkManager(config(delay=2))
    first = manager.submit_packet("P0", spec(), 0)
    second = manager.submit_packet("P1", spec(), 0)
    tx = manager.schedule_network(0).operations[0]
    batch = manager.complete_operation(tx, 1)
    assert [op.packet_id for op in batch.operations] == [first, second]
    registered = batch.operations[:index]
    cleanup = manager.abort_operations(batch.operations[index:], 1)
    expected_failed = [first, second][index:]
    assert [notice.packet_id for notice in cleanup.notifications] == expected_failed
    assert all(op.token in manager.runtime.operations for op in registered)
    for stale in batch.operations[index:]:
        assert manager.complete_operation(stale, 1) == NetworkUpdate()
    if registered:
        end, notices, _ = execute(manager, NetworkUpdate(operations=registered), 1)
        assert end == 3 and notices[0].packet_id == first
    manager.validate()


@pytest.mark.parametrize("independent", [False, True])
@pytest.mark.parametrize("fault", ["create", "send", "propagate"])
def test_internal_transition_failure_finalizes_affected_work_preserving_original(
    monkeypatch: pytest.MonkeyPatch, independent: bool, fault: str,
) -> None:
    manager = NetworkManager(independent_config())
    saved = None
    if independent:
        manager.submit_packet("other", spec(source="c", destination="d"), 0)
        saved = manager.schedule_network(0).operations[0]
    original_event = manager._event
    error = RuntimeError("original event failure")
    action = {"create": "PACKET_CREATE", "send": "PACKET_SEND",
              "propagate": "PACKET_PROPAGATE"}[fault]

    def broken(event: str, *args: Any, **kwargs: Any) -> None:
        if event == action:
            raise error
        original_event(event, *args, **kwargs)

    if fault == "propagate":
        manager.submit_packet("target", spec(), 0)
        tx = manager.schedule_network(0).operations[0]
    monkeypatch.setattr(manager, "_event", broken)
    with pytest.raises(RuntimeError) as caught:
        if fault == "propagate":
            manager.complete_operation(tx, 1)
        else:
            manager.submit_packet("target", spec(), 0)
    assert caught.value is error
    manager.validate()
    assert all(p.state in (PacketState.FAILED, PacketState.TRANSMITTING)
               for p in manager.runtime.packets.values())
    target = next(p for p in manager.runtime.packets.values() if p.pid == "target")
    assert target.state == PacketState.FAILED
    assert len(manager.drain_notifications(1 if fault == "propagate" else 0)) == 1
    if saved:
        assert manager.runtime.operations[saved.token] == saved
    monkeypatch.setattr(manager, "_event", original_event)
    manager.reset()
    manager.submit_packet("target", spec(), 0)
    _, notices, _ = execute(manager, manager.schedule_network(0))
    assert notices[0].outcome == PacketState.DELIVERED


def test_secondary_cleanup_error_is_not_primary_and_finalization_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = NetworkManager(config())
    original = RuntimeError("original admission failure")

    def fail_event(*args: Any, **kwargs: Any) -> None:
        raise original

    def fail_cleanup(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("secondary cleanup failure")

    monkeypatch.setattr(manager, "_event", fail_event)
    monkeypatch.setattr(manager, "_finish", fail_cleanup)
    with pytest.raises(RuntimeError) as caught:
        manager.submit_packet("P", spec(), 0)
    assert caught.value is original
    assert "secondary cleanup failure" in str(caught.value.__notes__)
    manager.validate()
    assert manager.runtime.packets["packet-0"].state == PacketState.FAILED
    assert manager.runtime.operations == {}
    manager.reset()
    assert manager.runtime.packets == {}


def test_failed_submission_preserves_an_unpublished_independent_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = NetworkManager(independent_config())
    other = manager.submit_packet("other", spec(source="c", destination="d"), 0)
    original_event = manager._event

    def fail_create(action: str, *args: Any, **kwargs: Any) -> None:
        if action == "PACKET_CREATE":
            raise RuntimeError("submission failure")
        original_event(action, *args, **kwargs)

    monkeypatch.setattr(manager, "_event", fail_create)
    with pytest.raises(RuntimeError, match="submission failure"):
        manager.submit_packet("target", spec(), 0)
    manager.validate()
    assert manager.runtime.packets[other].state == PacketState.TRANSMITTING
    update = manager.schedule_network(0)
    assert update.operations[0].packet_id == other
    assert update.notifications[0].pid == "target"


@pytest.mark.parametrize("same_link", [False, True])
def test_older_waiter_activation_failure_targets_the_selected_packet(
    monkeypatch: pytest.MonkeyPatch, same_link: bool,
) -> None:
    manager = NetworkManager(independent_config())
    active = manager.submit_packet("A", spec(), 0)
    queued = manager.submit_packet("B", spec(), 0)
    other = manager.submit_packet("independent", spec(source="c", destination="d"), 0)
    instructions = manager.schedule_network(0).operations
    saved = next(op for op in instructions if op.packet_id == other)
    manager.cancel_packet(active, 0)
    original = manager._operation
    error = RuntimeError("B operation preparation failed")
    selected = []

    def fail_b(packet: Any, *args: Any, **kwargs: Any) -> NetworkOperation:
        selected.append(packet.pid)
        if packet.pid == "B":
            raise error
        return original(packet, *args, **kwargs)

    monkeypatch.setattr(manager, "_operation", fail_b)
    with pytest.raises(RuntimeError) as caught:
        if same_link:
            manager.submit_packet("P", spec(), 0)
        else:
            manager.submit_packet("P", spec(source="c", destination="d"), 0)
            manager.schedule_network(0)
    assert caught.value is error
    assert selected == ["B"]
    manager.validate()
    assert manager.runtime.packets[queued].state == PacketState.FAILED
    assert manager.runtime.operations[saved.token] == saved
    assert manager.runtime.packets[other].state == PacketState.TRANSMITTING


def test_large_queue_integral_is_null_but_average_remains_representable() -> None:
    manager = NetworkManager(config(delay=0, bandwidth=1e-308))
    for i in range(5):
        manager.submit_packet(str(i), spec(size_bytes=1), 0)
    manager.schedule_network(0)
    before = deepcopy(manager.__dict__)
    metrics = manager.metrics(5e307)
    assert metrics["links"]["ab"]["queue_length_integral"] is None
    assert metrics["links"]["ab"]["average_queue_length"] == 4
    assert metrics["links"]["ab"]["utilization"] == 1
    json.dumps(manager.snapshot(5e307), allow_nan=False)
    assert manager.__dict__ == before


def test_failure_without_alternate_route_drops_waiters_once() -> None:
    manager = NetworkManager(config())
    manager.submit_packet("A", spec(), 0)
    manager.submit_packet("B", spec(), 0)
    manager.schedule_network(0)
    update = manager.fail_link("ab", 0)
    assert [n.reason for n in update.notifications] == [
        TerminalReason.LINK_FAILURE, TerminalReason.NO_ROUTE,
    ]
    assert manager.fail_link("ab", 0).notifications == ()
    assert manager.recover_link("ab", 0).operations == ()
    assert manager.metrics(1)["terminal_notifications"] == 2


def test_local_delivery_works_without_links_at_nonzero_reset_epoch() -> None:
    manager = NetworkManager(NetworkConfig(nodes=(NodeConfig(node_id="a"),)))
    manager.reset(7)
    manager.submit_packet("P", spec(destination="a"), 7)
    update = manager.schedule_network(7)
    assert update.notifications[0].timestamp == 7
    assert manager.metrics(7)["elapsed_time"] == 0
    assert manager.metrics(8)["throughput"] == 1


def test_configuration_validation_is_atomic_and_requires_pristine_run() -> None:
    manager = NetworkManager(config())
    before = deepcopy(manager.__dict__)
    with pytest.raises(ValueError):
        manager.configure(replace(config(), hop_limit=0), 0)
    assert manager.__dict__ == before
    manager.configure(independent_config(), 1)
    assert "cd" in manager.runtime.links
    manager.submit_packet("P", spec(), 1)
    with pytest.raises(ValueError, match="pristine"):
        manager.configure(config(), 1)
    manager.reset()
    manager.configure(config(), 0)
    manager.validate()


@pytest.mark.parametrize("failed_index", [0, 1])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_caller_registration_failure_preserves_primary_and_registered_prefix(
    monkeypatch: pytest.MonkeyPatch, failed_index: int, cleanup_fails: bool,
) -> None:
    manager = NetworkManager(independent_config())
    first = manager.submit_packet("A", spec(), 0)
    second = manager.submit_packet("B", spec(), 0)
    other = manager.submit_packet("independent", spec(source="c", destination="d"), 0)
    initial = manager.schedule_network(0)
    saved = next(op for op in initial.operations if op.packet_id == other)
    tx = next(op for op in initial.operations if op.packet_id == first)
    update = manager.complete_operation(tx, 1)
    registered = []
    primary = RuntimeError("callback registration failed")
    if cleanup_fails:
        def fail_cleanup(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("cleanup hook failed")
        monkeypatch.setattr(manager, "_finish", fail_cleanup)
    with pytest.raises(RuntimeError) as caught:
        for index, operation in enumerate(update.operations):
            try:
                if index == failed_index:
                    raise primary
                registered.append(operation)
            except Exception as error:
                try:
                    manager.abort_operations(update.operations[index:], 1)
                except Exception as cleanup_error:
                    error.add_note(f"Callback cleanup: {cleanup_error!r}")
                raise
    assert caught.value is primary
    if cleanup_fails:
        assert "cleanup hook failed" in str(caught.value.__notes__)
    manager.validate()
    assert manager.runtime.operations[saved.token] == saved
    for operation in registered:
        assert manager.runtime.operations[operation.token] == operation
    failed = [first, second][failed_index:]
    assert all(manager.runtime.packets[key].state == PacketState.FAILED
               for key in failed)
    assert not any(op.packet_id in failed for op in manager.runtime.operations.values())
    for operation in update.operations[failed_index:]:
        assert manager.complete_operation(operation, 1) == NetworkUpdate()
    monkeypatch.undo()
    manager.reset()
    manager.submit_packet("replay", spec(), 0)
    _, notices, _ = execute(manager, manager.schedule_network(0))
    assert notices[0].outcome == PacketState.DELIVERED


def test_cancellation_during_later_queue_preserves_completed_hop_history() -> None:
    manager = NetworkManager(config(multi=True, delay=0))
    blocker = manager.submit_packet("blocker", spec(source="b", destination="c",
                                                   size_bytes=1000), 0)
    target = manager.submit_packet("target", spec(destination="c"), 0)
    update = manager.schedule_network(0)
    tx = next(op for op in update.operations if op.packet_id == target)
    propagation = manager.complete_operation(tx, 1).operations[0]
    manager.complete_operation(propagation, 1)
    assert manager.runtime.packets[target].state == PacketState.QUEUED
    manager.cancel_packet(target, 2)
    assert manager.snapshot(2)["hop_history"][target][0]["outcome"] == "FORWARDED"
    assert manager.runtime.packets[blocker].state == PacketState.TRANSMITTING


def test_partial_wire_bytes_and_cancelled_packet_accounting() -> None:
    manager = NetworkManager(config())
    key = manager.submit_packet("P", spec(), 0)
    manager.schedule_network(0)
    assert manager.metrics(0.5)["links"]["ab"]["wire_bytes"] == 50
    manager.cancel_packet(key, 0.5)
    metrics = manager.metrics(1)
    assert metrics["links"]["ab"]["wire_bytes"] == 50
    assert metrics["links"]["ab"]["serialized_bytes"] == 0
    assert metrics["links"]["ab"]["busy_time"] == 0.5
    assert metrics["packets"][key]["elapsed_time"] == 0.5
    assert metrics["delivered_payload_bytes"] == 0


@pytest.mark.parametrize("field,value", [
    ("generation", True), ("sequence", False), ("packet_generation", False),
    ("token", []),
])
def test_malformed_callback_numbers_cannot_alias_valid_identity(
    field: str, value: object,
) -> None:
    manager = NetworkManager(config())
    manager.reset()
    manager.submit_packet("P", spec(), 0)
    operation = manager.schedule_network(0).operations[0]
    before = manager.runtime
    with pytest.raises(ValueError):
        manager.complete_operation(replace(operation, **{field: value}), 1)
    assert manager.runtime == before


def test_repeated_same_timestamp_cancel_replace_and_reset_callbacks() -> None:
    manager = NetworkManager(config(delay=0))
    first = manager.submit_packet("A", spec(), 0)
    old = manager.schedule_network(0).operations[0]
    manager.cancel_packet(first, 0)
    replacement = manager.submit_packet("B", spec(), 0)
    current = manager.schedule_network(0).operations[0]
    assert old.token != current.token
    assert manager.complete_operation(old, 0) == NetworkUpdate()
    assert manager.runtime.operations[current.token] == current
    manager.reset()
    assert manager.complete_operation(current, 0) == NetworkUpdate()
    assert manager.runtime.packets == {}
    assert replacement != first


@pytest.mark.parametrize("local", [False, True])
def test_failed_publication_retains_notifications_and_invalidates_unreturned_work(
    monkeypatch: pytest.MonkeyPatch, local: bool,
) -> None:
    manager = NetworkManager(independent_config())
    other = manager.submit_packet("other", spec(source="c", destination="d"), 0)
    saved = manager.schedule_network(0).operations[0]
    target = manager.submit_packet("target", spec(destination="a" if local else "b"), 0)
    original = manager._publish
    failure = RuntimeError("publication failed after preparing result")

    def fail_publish() -> NetworkUpdate:
        original()
        raise failure

    monkeypatch.setattr(manager, "_publish", fail_publish)
    with pytest.raises(RuntimeError) as caught:
        manager.schedule_network(0)
    assert caught.value is failure
    manager.validate()
    assert manager.runtime.operations[saved.token] == saved
    assert manager.runtime.packets[other].state == PacketState.TRANSMITTING
    notices = manager.drain_notifications(0)
    assert len(notices) == 1 and notices[0].packet_id == target
    expected = PacketState.DELIVERED if local else PacketState.FAILED
    assert notices[0].outcome == expected
    assert manager.drain_notifications(0) == ()


def test_zero_waiting_capacity_accepts_after_transmission_during_propagation() -> None:
    manager = NetworkManager(config(capacity=0, delay=2))
    manager.submit_packet("P0", spec(), 0)
    first = manager.schedule_network(0).operations[0]
    continuation = manager.complete_operation(first, 1)
    manager.submit_packet("P1", spec(), 1)
    next_batch = manager.schedule_network(1)
    end, notices, _ = execute(manager, NetworkUpdate(
        operations=continuation.operations + next_batch.operations,
    ), 1)
    assert end == 4
    assert [n.pid for n in notices] == ["P0", "P1"]
    assert manager.metrics(end)["dropped"] == 0


@pytest.mark.parametrize("completion_first", [False, True])
def test_failure_at_exact_completion_timestamp_uses_caller_order(
    completion_first: bool,
) -> None:
    manager = NetworkManager(config(delay=0))
    key = manager.submit_packet("P", spec(), 0)
    tx = manager.schedule_network(0).operations[0]
    if completion_first:
        update = manager.complete_operation(tx, 1)
        manager.complete_operation(update.operations[0], 1)
    update = manager.fail_link("ab", 1)
    state = manager.runtime.packets[key].state
    assert state == (PacketState.DELIVERED if completion_first else PacketState.FAILED)
    assert manager.metrics(1)["links"]["ab"]["busy_time"] == 1
    assert manager.complete_operation(tx, 1) == NetworkUpdate()
    assert len(update.notifications) == (0 if completion_first else 1)


def test_reroute_drop_tail_does_not_displace_earlier_admitted_waiter() -> None:
    topology = reroute_config(capacity=0)
    topology = replace(topology, links=tuple(
        replace(link, queue_policy=QueuePolicy.PRIORITY) for link in topology.links
    ))
    manager = NetworkManager(topology)
    manager.submit_packet("A", spec(), 0)
    earlier = manager.submit_packet("earlier", spec(priority=99), 0)
    later = manager.submit_packet("later", spec(priority=0), 0)
    manager.schedule_network(0)
    update = manager.fail_link("ab", 0)
    assert manager.runtime.packets[earlier].state == PacketState.TRANSMITTING
    assert manager.runtime.packets[later].reason == TerminalReason.QUEUE_FULL
    assert update.operations[0].packet_id == earlier


@pytest.mark.parametrize("boundary", ["attach", "hop_history"])
def test_partial_activation_failure_never_leaves_callback_or_link_ownership(
    monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    manager = NetworkManager(independent_config())
    other = manager.submit_packet("other", spec(source="c", destination="d"), 0)
    saved = manager.schedule_network(0).operations[0]
    failure = RuntimeError("activation boundary failure")
    original_start = manager._start

    class BrokenHistory(list):
        def append(self, value: object) -> None:
            raise failure

    def broken_attach(*args: Any, **kwargs: Any) -> None:
        raise failure

    def broken_history(packet: Any, *args: Any, **kwargs: Any) -> None:
        manager._hops[packet.packet_id] = BrokenHistory()
        original_start(packet, *args, **kwargs)

    monkeypatch.setattr(manager, "_attach" if boundary == "attach" else "_start",
                        broken_attach if boundary == "attach" else broken_history)
    with pytest.raises(RuntimeError) as caught:
        manager.submit_packet("target", spec(), 0)
    assert caught.value is failure
    manager.validate()
    assert manager.runtime.operations[saved.token] == saved
    assert manager.runtime.packets[other].state == PacketState.TRANSMITTING
    assert manager.runtime.packets["packet-1"].state == PacketState.FAILED
    assert manager.runtime.links["ab"].transmitting_packet_id is None
