"""Scheduled controls share engine ordering, ownership, and reset semantics."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from typing import Any

import pytest

from src.core.process import Process, ProcessState
from src.core.simulation import SimulationEngine, SimulationExecutionError
from src.managers.network import (
    NetworkManager, NetworkTopologyEvent, TopologyAction, validate_topology_events,
)
from tests.test_network_manager import config, reroute_config, spec


@pytest.mark.parametrize("bad", [True, -1, float("inf"), float("nan"), "1", None])
def test_control_timestamp_is_finite_nonnegative_and_not_boolean(bad: object) -> None:
    with pytest.raises(ValueError):
        validate_topology_events([NetworkTopologyEvent(
            timestamp=bad, action=TopologyAction.LINK_DOWN, link_id="ab",
        )], config())


@pytest.mark.parametrize("events", [
    None, {}, "LINK_DOWN", ["LINK_DOWN"],
    [NetworkTopologyEvent(timestamp=0, action="LINK_DOWN", link_id="ab")],
    [NetworkTopologyEvent(timestamp=0, action=TopologyAction.LINK_DOWN,
                          link_id="missing")],
    [NetworkTopologyEvent(timestamp=0, action=TopologyAction.NODE_UP,
                          node_id="missing")],
    [NetworkTopologyEvent(timestamp=0, action=TopologyAction.NODE_UP,
                          node_id="a", link_id="ab")],
])
def test_invalid_controls_rejected_before_engine_resets_any_state(events: Any) -> None:
    manager = NetworkManager(config())
    packet = manager.submit_packet("outside", spec(), 0)
    before = manager.runtime
    process = Process("P", 0, 1)
    with pytest.raises(ValueError):
        SimulationEngine([process], network_manager=manager, network_events=events)
    assert manager.runtime == before and packet in manager.runtime.packets
    assert process.state == ProcessState.NEW


def test_controls_require_manager_and_configuration_is_frozen_copied() -> None:
    event = NetworkTopologyEvent(timestamp=1, action=TopologyAction.LINK_DOWN,
                                 link_id="ab")
    with pytest.raises(ValueError):
        SimulationEngine([Process("P", 0, 1)], network_events=[event])
    controls = [event]
    engine = SimulationEngine([Process("P", 0, 1)],
                              network_manager=NetworkManager(config()),
                              network_events=controls)
    controls.clear()
    assert engine.network_events == (event,)
    with pytest.raises(FrozenInstanceError):
        event.timestamp = 2
    first = engine.run()
    engine.reset()
    assert engine.run() == first


@pytest.mark.parametrize("pid,link_id,delivered", [
    ("A", "z", True), ("z", "a", False), ("same", "same", False),
])
@pytest.mark.parametrize("propagation", [False, True])
def test_controls_and_completions_use_owner_target_id_then_insertion(
    pid: str, link_id: str, delivered: bool, propagation: bool,
) -> None:
    topology = config(delay=1 if propagation else 0)
    topology = replace(topology, links=(replace(topology.links[0], link_id=link_id),))
    due = 2 if propagation else 1
    engine = SimulationEngine(
        [Process(pid, 0, 1, network_requests={0: spec()})],
        network_manager=NetworkManager(topology),
        network_events=(NetworkTopologyEvent(
            timestamp=due, action=TopologyAction.LINK_DOWN, link_id=link_id,
        ),),
    )
    result = engine.run()
    assert result["network_metrics"]["delivered"] == int(delivered)
    assert result["network_metrics"]["failed"] == int(not delivered)
    assert len(result["network_outcomes"]) == 1
    assert result["network_metrics"]["links"][link_id]["busy_time"] == 1
    assert result["current_time"] == due + 1
    at_boundary = [e["event_type"] for e in result["network_events"]
                   if e["timestamp"] == due]
    if delivered:
        assert at_boundary.index("PACKET_DELIVERED") < at_boundary.index("LINK_DOWN")
    else:
        assert "PACKET_DELIVERED" not in at_boundary


@pytest.mark.parametrize("reverse", [False, True])
def test_equal_time_equal_target_controls_keep_configured_order(reverse: bool) -> None:
    actions = [TopologyAction.LINK_DOWN, TopologyAction.LINK_UP]
    if reverse:
        actions.reverse()
    engine = SimulationEngine([Process("P", 0, 0.25)],
                              network_manager=NetworkManager(config()),
                              network_events=tuple(NetworkTopologyEvent(
                                  timestamp=2, action=action, link_id="ab",
                              ) for action in actions))
    result = engine.run()
    assert result["current_time"] == 2  # All configured controls define the horizon.
    assert [e["event_type"] for e in result["network_events"]] == [
        action.value for action in actions
    ]
    assert result["network_topology"]["links"]["ab"]["state"] == (
        "DOWN" if reverse else "UP"
    )


def test_zero_time_control_is_applied_before_first_send_and_replays() -> None:
    process = Process("ab", 0, 1, network_requests={0: spec()})
    engine = SimulationEngine([process], network_manager=NetworkManager(config()),
                              network_events=(NetworkTopologyEvent(
                                  timestamp=0, action=TopologyAction.LINK_DOWN,
                                  link_id="ab",
                              ),))
    result = engine.run()
    assert result["network_outcomes"]["packet-0"]["reason"] == "NO_ROUTE"
    assert process.blocked_time == 0
    assert result["network_metrics"]["links"]["ab"]["busy_time"] == 0
    engine.reset()
    assert engine.run() == result


@pytest.mark.parametrize("after_append", [False, True])
def test_scheduled_rerouting_registration_failure_preserves_primary_and_isolation(
    monkeypatch: pytest.MonkeyPatch, after_append: bool,
) -> None:
    topology = reroute_config()
    topology = replace(topology, links=topology.links + (
        replace(topology.links[0], link_id="cd", source="c", destination="d"),
    ))
    first = Process("A", 0, 1, network_requests={0: spec()})
    second = Process("B", 0, 1, network_requests={0: spec()})
    other = Process("C", 0, 1, network_requests={0: spec(source="c", destination="d")})
    engine = SimulationEngine([first, second, other],
                              network_manager=NetworkManager(topology),
                              network_events=(NetworkTopologyEvent(
                                  timestamp=0.5, action=TopologyAction.LINK_DOWN,
                                  link_id="ab",
                              ),))
    original = engine.schedule_event
    primary = RuntimeError("reroute callback registration failed")

    def register(time: float, kind: str, payload: Any) -> None:
        if kind == "network_complete" and payload.link_id == "ac":
            if after_append:
                original(time, kind, payload)
            raise primary
        original(time, kind, payload)

    monkeypatch.setattr(engine, "schedule_event", register)
    with pytest.raises(SimulationExecutionError):
        engine.run()
    assert engine._execution_exception is primary
    engine.network_manager.validate()
    assert first.state == second.state == ProcessState.READY
    assert first.blocked_time == second.blocked_time == 0.5
    assert other.state == ProcessState.WAITING
    live = list(engine.network_manager.runtime.operations.values())
    assert len(live) == 1 and live[0].link_id == "cd"
    assert any(e[3] == "network_complete" and e[4] == live[0]
               for e in engine.event_queue)
    assert not any(e[3] == "network_complete" and e[4].link_id == "ac"
                   for e in engine.event_queue)
    with pytest.raises(SimulationExecutionError, match="Reset required"):
        engine.step()
    monkeypatch.undo()
    engine.reset()
    assert engine.run()["network_metrics"]["delivered"] == 2


def test_control_lifecycle_exception_delivers_cleanup_without_results_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = SimulationEngine(
        [Process("P", 0, 1, network_requests={0: spec()})],
        network_manager=NetworkManager(config()),
        network_events=(NetworkTopologyEvent(timestamp=0.5,
                                             action=TopologyAction.LINK_DOWN,
                                             link_id="ab"),),
    )
    original = engine.network_manager._event
    primary = RuntimeError("topology history failed")

    def history(action: str, *args: Any, **kwargs: Any) -> None:
        if action == "LINK_DOWN":
            raise primary
        original(action, *args, **kwargs)

    monkeypatch.setattr(engine.network_manager, "_event", history)
    with pytest.raises(SimulationExecutionError):
        engine.run()
    assert engine._execution_exception is primary
    assert engine.network_manager.runtime.operations == {}
    assert engine.processes[0].state == ProcessState.READY
    before = deepcopy(engine.network_manager.__dict__)
    first = engine.results()
    assert engine.results() == first
    assert engine.network_manager.__dict__ == before
