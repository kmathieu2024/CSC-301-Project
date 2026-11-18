"""FIFO/priority selection at stable boundaries; no transmission execution."""

from copy import deepcopy
from dataclasses import replace
from itertools import permutations

import pytest

from src.managers.network import (
    Availability, NetworkRequestSpec, NetworkRuntime, PacketState, QueuePolicy,
    create_packet_runtime, select_fifo, select_next, select_priority,
)
from tests.test_network_models import packet_runtime


def waiting(policy: QueuePolicy = QueuePolicy.FIFO) -> NetworkRuntime:
    """Three valid waiting packets with differing enqueue/creation order."""
    runtime = packet_runtime(PacketState.QUEUED)
    runtime.config = replace(runtime.config, links=(replace(
        runtime.config.links[0], queue_policy=policy, queue_capacity_packets=4,
    ),))
    link = runtime.links["ab"]
    link.config = runtime.config.links[0]
    first = runtime.packets["packet-0"]
    first.queued_at = 3
    first.spec = replace(first.spec, priority=0)
    for sequence, enqueue_time, priority in ((1, 2, 3), (2, 1, 2)):
        packet = create_packet_runtime(
            NetworkRequestSpec(source="a", destination="b", size_bytes=100,
                               priority=priority),
            runtime.config, pid=f"P{sequence}", sequence=sequence,
            generation=0, now=0,
        )
        packet.state = PacketState.QUEUED
        packet.link_id = "ab"
        packet.queued_at = enqueue_time
        packet.enqueue_sequence = sequence
        runtime.packets[packet.packet_id] = packet
        link.queue.append(packet.packet_id)
    runtime.next_packet_sequence = runtime.next_enqueue_sequence = 3
    return runtime


@pytest.mark.parametrize("policy,expected", [
    (QueuePolicy.FIFO, "packet-2"), (QueuePolicy.PRIORITY, "packet-0"),
])
def test_configured_policy_and_dedicated_selectors(
    policy: QueuePolicy, expected: str,
) -> None:
    runtime = waiting(policy)
    before = deepcopy(runtime)
    dedicated = select_fifo if policy == QueuePolicy.FIFO else select_priority
    for _ in range(3):
        assert select_next(runtime, "ab") == expected
        assert dedicated(runtime, "ab") == expected
    assert runtime == before


@pytest.mark.parametrize("policy", list(QueuePolicy))
def test_equal_priority_and_timestamp_use_enqueue_sequence(policy: QueuePolicy) -> None:
    runtime = waiting(policy)
    for packet in runtime.packets.values():
        packet.spec = replace(packet.spec, priority=1)
        packet.queued_at = 2
        packet.enqueue_sequence = 2 - packet.sequence
    assert select_next(runtime, "ab") == "packet-2"


def test_equal_priority_uses_enqueue_time_before_sequence() -> None:
    runtime = waiting(QueuePolicy.PRIORITY)
    for packet in runtime.packets.values():
        packet.spec = replace(packet.spec, priority=1)
    assert select_priority(runtime, "ab") == "packet-2"


@pytest.mark.parametrize("policy", list(QueuePolicy))
def test_empty_and_unavailable_links_return_none(policy: QueuePolicy) -> None:
    runtime = packet_runtime()
    runtime.config = replace(runtime.config, links=(replace(
        runtime.config.links[0], queue_policy=policy,
    ),))
    runtime.links["ab"].config = runtime.config.links[0]
    assert select_next(runtime, "ab") is None
    runtime.links["ab"].state = Availability.DOWN
    assert select_next(runtime, "ab") is None
    runtime.nodes["b"].state = Availability.DOWN
    assert select_next(runtime, "ab") is None


@pytest.mark.parametrize("policy", list(QueuePolicy))
def test_active_transmitter_is_not_preempted(policy: QueuePolicy) -> None:
    runtime = waiting(policy)
    active = packet_runtime(PacketState.TRANSMITTING)
    packet = active.packets["packet-0"]
    runtime.packets["packet-0"] = packet
    runtime.links["ab"].queue.remove("packet-0")
    runtime.links["ab"].transmitting_packet_id = packet.packet_id
    runtime.operations = active.operations
    runtime.next_operation_sequence = 1
    before = deepcopy(runtime)
    assert select_next(runtime, "ab") is None
    assert runtime == before


@pytest.mark.parametrize("policy", list(QueuePolicy))
def test_propagation_does_not_hold_the_transmitter(policy: QueuePolicy) -> None:
    runtime = waiting(policy)
    active = packet_runtime(PacketState.PROPAGATING)
    runtime.packets["packet-0"] = active.packets["packet-0"]
    runtime.links["ab"].queue.remove("packet-0")
    runtime.links["ab"].propagating_packet_ids.append("packet-0")
    runtime.operations = active.operations
    runtime.next_operation_sequence = 1
    assert select_next(runtime, "ab") == "packet-2"


@pytest.mark.parametrize("policy", list(QueuePolicy))
def test_queue_and_registry_insertion_orders_do_not_affect_selection(
    policy: QueuePolicy,
) -> None:
    runtime = waiting(policy)
    expected = "packet-2" if policy == QueuePolicy.FIFO else "packet-0"
    for ordering in permutations(runtime.links["ab"].queue):
        runtime.links["ab"].queue = list(ordering)
        runtime.packets = dict(reversed(tuple(runtime.packets.items())))
        before = deepcopy(runtime)
        assert select_next(runtime, "ab") == expected
        assert runtime == before


@pytest.mark.parametrize("problem", [
    "missing_packet", "duplicate_queue", "wrong_link", "wrong_node", "terminal",
    "transmitting", "stale_generation", "duplicate_sequence", "missing_time",
    "wrong_policy", "wrong_registry_key", "unavailable_with_work",
])
def test_inconsistent_waiting_state_raises_instead_of_selecting(problem: str) -> None:
    runtime = waiting()
    packet = runtime.packets["packet-0"]
    if problem == "missing_packet":
        runtime.packets.pop("packet-0")
    elif problem == "duplicate_queue":
        runtime.links["ab"].queue.append("packet-0")
    elif problem == "wrong_link":
        packet.link_id = "unknown"
    elif problem == "wrong_node":
        packet.current_node = "b"
    elif problem == "terminal":
        runtime.packets["packet-0"] = packet_runtime(PacketState.DELIVERED).packets[
            "packet-0"
        ]
    elif problem == "transmitting":
        runtime.packets["packet-0"] = packet_runtime(PacketState.TRANSMITTING).packets[
            "packet-0"
        ]
    elif problem == "stale_generation":
        packet.generation += 1
    elif problem == "duplicate_sequence":
        packet.enqueue_sequence = 1
    elif problem == "missing_time":
        packet.queued_at = None
    elif problem == "wrong_policy":
        runtime.links["ab"].config = replace(runtime.links["ab"].config,
                                             queue_policy="FIFO")
    elif problem == "wrong_registry_key":
        runtime.packets["other"] = runtime.packets.pop("packet-0")
    else:
        runtime.links["ab"].state = Availability.DOWN
    before = deepcopy(runtime)
    with pytest.raises(ValueError):
        select_next(runtime, "ab")
    assert runtime == before


@pytest.mark.parametrize("bad", ["unknown", "", True, None, [], {}])
def test_invalid_link_ids(bad: object) -> None:
    with pytest.raises(ValueError):
        select_next(waiting(), bad)


@pytest.mark.parametrize("policy", list(QueuePolicy))
def test_dedicated_selector_rejects_different_policy(policy: QueuePolicy) -> None:
    other = select_priority if policy == QueuePolicy.FIFO else select_fifo
    with pytest.raises(ValueError, match="policy"):
        other(waiting(policy), "ab")


def test_validation_still_runs_when_transmitter_is_busy() -> None:
    runtime = packet_runtime(PacketState.TRANSMITTING)
    runtime.links["ab"].queue.append("missing")
    with pytest.raises(ValueError):
        select_next(runtime, "ab")
