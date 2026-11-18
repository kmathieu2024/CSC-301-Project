"""Typed synchronous Network I/O through the existing engine and RBAC boundary."""

from copy import deepcopy
from dataclasses import replace
import inspect
import json
from typing import Any

import pytest

from src.core.process import Process, ProcessState
from src.core.simulation import SimulationEngine, SimulationExecutionError
from src.managers.memory import MemoryManager
from src.managers.network import (
    NetworkManager, NetworkUpdate, NodeConfig, OperationKind,
    PacketState,
)
from src.managers.security import SecurityManager
from tests.test_device_integration import manager as device_manager, spec as device_spec
from tests.test_network_manager import config, independent_config, spec


def engine_with_request(
    *, offset: int = 0, local: bool = False, **options: Any,
) -> tuple[SimulationEngine, Process]:
    """One two-unit CPU process, with a two-unit one-hop send by default."""
    process = Process("P", 0, 2, network_requests={
        offset: spec(destination="a" if local else "b"),
    })
    return SimulationEngine([process], network_manager=NetworkManager(config()),
                            **options), process


def wait_for_packets(engine: SimulationEngine, count: int) -> None:
    """Stop at a yielded process boundary with bounded progress."""
    for _ in range(30):
        engine.step()
        if len(engine._network_waiting) == count:
            return
    raise AssertionError("Expected blocked network processes")


def cpu_time(result: dict[str, Any]) -> float:
    return sum(item["end"] - item["start"] for item in result["timeline"]
               if item["pid"] not in ("IDLE", "CS"))


def test_process_mapping_is_keyword_only_copied_and_positional_compatible() -> None:
    requests = {0: spec()}
    p = Process("P", 0, 2, 1, {}, {}, {}, network_requests=requests)
    requests.clear()
    assert p.network_requests == {0: spec()}
    assert Process("other", 0, 1).network_requests == {}
    assert inspect.signature(Process).parameters["network_requests"].kind == (
        inspect.Parameter.KEYWORD_ONLY
    )
    with pytest.raises(ValueError):
        Process("P", 0, 1, network_requests=[])


@pytest.mark.parametrize("offset", [-1, True, 0.5, "0", 2])
def test_invalid_offsets_rejected_before_reset(offset: object) -> None:
    manager = NetworkManager(config())
    before = manager.runtime
    with pytest.raises(ValueError):
        SimulationEngine([Process("P", 0, 2, network_requests={offset: spec()})],
                         network_manager=manager)
    assert manager.runtime == before


@pytest.mark.parametrize("request_spec", ["send", spec(source="unknown"),
                                     spec(priority=True), spec(size_bytes=0)])
def test_invalid_request_specs_rejected(request_spec: object) -> None:
    with pytest.raises(ValueError):
        SimulationEngine([Process("P", 0, 2, network_requests={0: request_spec})],
                         network_manager=NetworkManager(config()))


def test_requests_require_configured_manager() -> None:
    with pytest.raises(ValueError, match="require a network manager"):
        SimulationEngine([Process("P", 0, 1, network_requests={0: spec()})])


@pytest.mark.parametrize("offset", [0, 1])
def test_delivery_blocks_and_resumes_at_offset(offset: int) -> None:
    engine, process = engine_with_request(offset=offset)
    result = engine.run()
    assert process.completion_time == 4
    assert process.blocked_time == 2
    assert result["network_metrics"]["delivered"] == 1
    assert cpu_time(result) == 2
    assert result["network_outcomes"]["packet-0"]["timestamp"] == offset + 2
    assert len([e for e in result["events"] if e["action"] == "network_interrupt"]) == 1
    creates = [e for e in result["network_events"]
               if e["event_type"] == "PACKET_CREATE"]
    assert len(creates) == 1


@pytest.mark.parametrize("destination,outcome,reason", [
    ("a", "DELIVERED", "DELIVERED"), ("d", "DROPPED", "NO_ROUTE"),
])
def test_immediate_outcomes_wake_once_after_cpu_stop(
    destination: str, outcome: str, reason: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = Process("P", 0, 2, network_requests={0: spec(destination=destination)})
    engine = SimulationEngine([process], network_manager=NetworkManager(config()))
    calls = []
    original = engine.scheduler.on_ready

    def ready(process: Process, now: float) -> None:
        calls.append((process.pid, now))
        original(process, now)

    monkeypatch.setattr(engine.scheduler, "on_ready", ready)
    result = engine.run()
    assert process.completion_time == 2 and process.blocked_time == 0
    assert calls == [("P", 0), ("P", 0)]  # arrival, then the one terminal interrupt
    assert result["network_outcomes"]["packet-0"]["outcome"] == outcome
    assert result["network_outcomes"]["packet-0"]["reason"] == reason


def test_other_process_executes_while_transport_progresses() -> None:
    first = Process("A", 0, 2, network_requests={0: spec()})
    second = Process("B", 0, 5)
    result = SimulationEngine(
        [first, second], network_manager=NetworkManager(config()),
    ).run()
    assert first.blocked_time == 2
    assert second.completion_time == 5
    assert first.completion_time == 7
    assert cpu_time(result) == 7
    assert not any(item["pid"] == "IDLE" for item in result["timeline"])
    interrupt = next(e for e in result["events"] if e["action"] == "network_interrupt")
    assert interrupt["timestamp"] == 2


def test_two_devices_and_multiple_network_links_operate_concurrently() -> None:
    first = Process("A", 0, 1, network_requests={0: spec()})
    second = Process("B", 0, 1, network_requests={0: spec(source="c", destination="d")})
    third = Process("C", 0, 1, device_requests={0: device_spec()})
    engine = SimulationEngine([first, second, third],
                              network_manager=NetworkManager(independent_config()),
                              device_manager=device_manager(service=3))
    result = engine.run()
    assert first.blocked_time == second.blocked_time == third.blocked_time == 3
    assert result["current_time"] == 6
    interrupts = [e for e in result["events"]
                  if e["action"] in ("network_interrupt", "device_interrupt")]
    assert [e["entity_id"] for e in interrupts] == ["A", "B", "C"]
    assert all(e["timestamp"] == 3 for e in interrupts)
    assert cpu_time(result) == 3


def test_queue_wait_contributes_to_actual_blocked_time_and_overflow_wakes() -> None:
    processes = [Process(f"P{i}", 0, 1, network_requests={0: spec()}) for i in range(3)]
    engine = SimulationEngine(
        processes, network_manager=NetworkManager(config(capacity=1)),
    )
    result = engine.run()
    assert [p.blocked_time for p in processes] == [2, 3, 0]
    assert result["network_metrics"]["delivered"] == 2
    assert result["network_metrics"]["dropped"] == 1
    assert result["network_outcomes"]["packet-2"]["reason"] == "QUEUE_FULL"


@pytest.mark.parametrize("algorithm", ["rr", "srtf"])
def test_scheduler_wakeup_rules_after_network_io(algorithm: str) -> None:
    first = Process("A", 0, 1, network_requests={0: spec()})
    second = Process("B", 0, 6)
    engine = SimulationEngine([first, second], algorithm=algorithm, quantum=2,
                              network_manager=NetworkManager(config()))
    result = engine.run()
    assert first.blocked_time == 2
    assert first.completion_time == 3
    assert second.completion_time == 7
    assert cpu_time(result) == 7
    assert any(e["action"] == "network_interrupt" and e["timestamp"] == 2
               for e in result["events"])


def test_network_completion_during_context_switching() -> None:
    first = Process("A", 0, 1, network_requests={0: spec()})
    second = Process("B", 0, 2)
    result = SimulationEngine([first, second], context_switch=3,
                              network_manager=NetworkManager(config())).run()
    assert first.blocked_time == 2
    assert first.completion_time == 4
    assert second.completion_time == 9
    assert cpu_time(result) == 3
    assert result["context_switch_overhead"] == 6


def test_all_manager_requests_at_shared_offset_keep_order_and_execute_once() -> None:
    process = Process("P", 0, 2, file_requests={0: "CREATE /once"},
                      blocking_requests={0: 1}, memory_references={0: 0},
                      device_requests={0: device_spec()}, network_requests={0: spec()})
    engine = SimulationEngine([process],
                              memory_manager=MemoryManager(1, fault_latency=2),
                              device_manager=device_manager(service=3),
                              network_manager=NetworkManager(config()))
    result = engine.run()
    assert process.blocked_time == 8 and process.completion_time == 10
    assert result["memory_metrics"]["references"] == 1
    assert result["device_metrics"]["devices"]["d0"]["submitted"] == 1
    assert result["network_metrics"]["submitted"] == 1
    assert len([e for e in result["events"] if e["manager"] == "file"]) == 1
    assert result["network_packets"]["packet-0"]["submitted_at"] == 6


@pytest.mark.parametrize("action", ["cancel", "fail_link", "fail_node"])
def test_external_actions_use_explicit_update_boundary_and_wake_once(
    action: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, process = engine_with_request()
    wait_for_packets(engine, 1)
    manager = engine.network_manager
    if action == "cancel":
        update = manager.cancel_packet("packet-0", engine.current_time)
    elif action == "fail_link":
        update = manager.fail_link("ab", engine.current_time)
    else:
        update = manager.fail_node("a", engine.current_time)
    ready_calls = []
    original = engine.scheduler.on_ready

    def ready(process: Process, now: float) -> None:
        ready_calls.append(process.pid)
        original(process, now)

    monkeypatch.setattr(engine.scheduler, "on_ready", ready)
    before = deepcopy(manager.__dict__)
    engine.results()
    engine.results()
    assert process.state == ProcessState.WAITING
    assert manager.__dict__ == before
    engine.apply_network_update(update)
    engine.apply_network_update(update)
    assert process.state == ProcessState.READY
    assert ready_calls == ["P"]
    result = engine.run()
    assert process.completion_time == 2
    assert result["network_metrics"]["terminal_notifications"] == 1
    assert ready_calls == ["P"]


@pytest.mark.parametrize("permission,allowed", [
    ("network:send", True), ("network:*", True),
    ("network:/nodes/a/to/b:send", True),
    ("network:/nodes/a/to/c:send", False), ("file:read", False),
])
def test_security_uses_existing_rbac_and_audit_once(
    permission: str, allowed: bool,
) -> None:
    security = SecurityManager()
    security.create_role("sender", [permission])
    security.add_user("P", ["sender"])
    engine, process = engine_with_request(security_manager=security)
    result = engine.run()
    records = list(security.security_events)
    assert len(records) == 1
    assert records[0]["resource_type"] == "network"
    assert records[0]["action"] == "send"
    assert records[0]["resource"] == "/nodes/a/to/b"
    assert process.blocked_time == (2 if allowed else 0)
    assert process.completion_time == (4 if allowed else 2)
    assert result["network_metrics"]["submitted"] == (1 if allowed else 0)
    assert len(result["network_outcomes"]) == 1
    if not allowed:
        assert result["security_alerts"]
        assert list(result["network_outcomes"].values())[0]["outcome"] == "DENIED"
        assert not engine._network_waiting
    count = len(security.audit_log)
    engine.reset()
    engine.run()
    assert len(security.audit_log) == count + 1


def test_security_target_cannot_be_changed_by_node_path_syntax() -> None:
    security = SecurityManager()
    target = SimulationEngine._network_target("../A", "b/c")
    assert target == "/nodes/%2E%2E%2FA/to/b%2Fc"
    security.create_role("sender", [f"network:{target}:send"])
    security.add_user("P", ["sender"])
    topology = replace(config(), nodes=(NodeConfig(node_id="../A"),
                                        NodeConfig(node_id="b/c")), links=())
    process = Process("P", 0, 1, network_requests={
        0: spec(source="../A", destination="b/c"),
    })
    result = SimulationEngine([process], network_manager=NetworkManager(topology),
                              security_manager=security).run()
    assert result["network_metrics"]["submitted"] == 1
    assert result["network_outcomes"]["packet-0"]["reason"] == "NO_ROUTE"
    assert security.security_events[-1]["resource"] == target


def test_unknown_security_user_is_denied_without_a_packet() -> None:
    engine, process = engine_with_request(security_manager=SecurityManager())
    result = engine.run()
    assert process.blocked_time == 0
    assert result["network_packets"] == {}
    assert result["security_alerts"]


def test_reset_replay_and_stale_callbacks_do_not_change_new_run() -> None:
    engine, process = engine_with_request()
    wait_for_packets(engine, 1)
    old = next(entry[4] for entry in engine.event_queue
               if entry[3] == "network_complete")
    engine.reset()
    assert process.state == ProcessState.NEW and engine._network_waiting == {}
    engine._complete_network_operation(old)
    first = engine.run()
    assert engine.step() == first
    assert engine.run() == first
    engine.reset()
    assert engine.run() == first


@pytest.mark.parametrize("field,bad", [("pid", "wrong"), ("source", "c"),
                                       ("timestamp", 1), ("generation", -1)])
def test_wrong_owner_notifications_are_rejected_without_wakeup(
    field: str, bad: object,
) -> None:
    engine, process = engine_with_request()
    wait_for_packets(engine, 1)
    manager = engine.network_manager
    update = manager.cancel_packet("packet-0", 0)
    snapshot = manager.snapshot(0)
    snapshot["notifications"]["packet-0"][field] = bad
    with pytest.raises(ValueError, match="does not match"):
        engine._deliver_network_notifications(snapshot)
    assert process.state == ProcessState.WAITING and process.blocked_time == 0
    engine.apply_network_update(update)
    assert process.state == ProcessState.READY


def test_terminal_process_cannot_be_revived() -> None:
    engine, process = engine_with_request()
    wait_for_packets(engine, 1)
    manager = engine.network_manager
    manager.cancel_packet("packet-0", 0)
    process.state = ProcessState.TERMINATED
    with pytest.raises(ValueError):
        engine._deliver_network_notifications(manager.snapshot(0))
    assert process.state == ProcessState.TERMINATED


def test_json_safe_detached_network_results_and_empty_compatibility() -> None:
    engine, _ = engine_with_request()
    wait_for_packets(engine, 1)
    before = deepcopy(engine.network_manager.__dict__)
    state = (len(engine.events), engine.event_queue.copy(),
             engine._network_waiting.copy())
    result = engine.results()
    json.dumps(result, allow_nan=False)
    assert engine.network_manager.__dict__ == before
    assert state == (len(engine.events), engine.event_queue, engine._network_waiting)
    text = json.dumps(result["network_packets"])
    assert "generation" not in text and "operation_token" not in text
    result["network_topology"]["links"].clear()
    assert engine.results()["network_topology"]["links"]
    empty = SimulationEngine([Process("P", 0, 2)]).run()
    assert empty["network_topology"] == empty["network_packets"] == {}
    assert empty["network_metrics"] is None
    assert empty["current_time"] == 2 and cpu_time(empty) == 2


@pytest.mark.parametrize("after_append", [False, True])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_initial_registration_failure_rolls_back_wait_and_requires_reset(
    monkeypatch: pytest.MonkeyPatch, after_append: bool, cleanup_fails: bool,
) -> None:
    engine, process = engine_with_request()
    original = engine.schedule_event
    primary = RuntimeError("initial network callback registration failed")

    def register(time: float, kind: str, payload: Any) -> None:
        if kind == "network_complete":
            if after_append:
                original(time, kind, payload)
            raise primary
        original(time, kind, payload)

    if cleanup_fails:
        def cleanup_error(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("secondary cleanup failure")
        monkeypatch.setattr(engine.network_manager, "_finish", cleanup_error)
    monkeypatch.setattr(engine, "schedule_event", register)
    with pytest.raises(SimulationExecutionError):
        engine.run()
    assert engine._execution_exception is primary
    assert process.state == ProcessState.RUNNING and process.blocked_time == 0
    assert engine._network_waiting == engine._network_submitted == {}
    assert engine.network_manager.runtime.operations == {}
    packet = engine.network_manager.runtime.packets["packet-0"]
    assert packet.state == PacketState.FAILED
    assert not any(e[3] == "network_complete" for e in engine.event_queue)
    if cleanup_fails:
        assert "secondary cleanup failure" in str(primary.__notes__)
    for action in (engine.step, engine.run):
        with pytest.raises(SimulationExecutionError, match="Reset required"):
            action()
    json.dumps(engine.results(), allow_nan=False)
    monkeypatch.undo()
    engine.reset()
    assert engine.run()["current_time"] == 4


@pytest.mark.parametrize("failed_index", [0, 1])
@pytest.mark.parametrize("cleanup_fails", [False, True])
@pytest.mark.parametrize("after_append", [False, True])
def test_split_batch_registration_preserves_prefix_and_independent_work(
    monkeypatch: pytest.MonkeyPatch, failed_index: int, cleanup_fails: bool,
    after_append: bool,
) -> None:
    first = Process("A", 0, 1, network_requests={0: spec()})
    second = Process("B", 0, 1, network_requests={0: spec()})
    other = Process("C", 0, 1, network_requests={0: spec(source="c", destination="d")})
    engine = SimulationEngine([first, second, other],
                              network_manager=NetworkManager(independent_config()))
    wait_for_packets(engine, 3)
    manager = engine.network_manager
    independent = next(e for e in engine.event_queue
                       if e[3] == "network_complete" and e[4].packet_id == "packet-2")
    original = engine.schedule_event
    primary = RuntimeError("split callback registration failed")
    selected = []
    ready = []
    on_ready = engine.scheduler.on_ready

    def ready_process(process: Process, now: float) -> None:
        ready.append((process.pid, now))
        on_ready(process, now)

    def register(time: float, kind: str, payload: Any) -> None:
        if kind == "network_complete":
            selected.append(payload)
            if len(selected) == failed_index + 1:
                if after_append:
                    original(time, kind, payload)
                raise primary
        original(time, kind, payload)

    if cleanup_fails:
        def cleanup_error(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("secondary batch cleanup failure")
        monkeypatch.setattr(manager, "_finish", cleanup_error)
    monkeypatch.setattr(engine, "schedule_event", register)
    monkeypatch.setattr(engine.scheduler, "on_ready", ready_process)
    with pytest.raises(SimulationExecutionError):
        engine.run()
    assert engine._execution_exception is primary
    assert selected[0].kind == OperationKind.PROPAGATION
    assert selected[0].packet_id == "packet-0"
    if failed_index == 1:
        assert selected[1].packet_id == "packet-1"
        assert selected[1].kind == OperationKind.TRANSMISSION
    manager.validate()
    assert independent in engine.event_queue
    assert manager.runtime.operations[independent[4].token] == independent[4]
    assert other.state == ProcessState.WAITING
    if failed_index == 1:
        assert first.state == ProcessState.WAITING
        assert first.blocked_time == 0
        assert manager.runtime.operations[selected[0].token] == selected[0]
        assert ready == [("B", 1)]
    else:
        assert first.state == ProcessState.READY and first.blocked_time == 1
        assert sorted(ready) == [("A", 1), ("B", 1)]
    assert second.state == ProcessState.READY and second.blocked_time == 1
    assert not any(e[3] == "network_complete" and e[4].packet_id == "packet-1"
                   for e in engine.event_queue)
    if cleanup_fails:
        assert "secondary batch cleanup failure" in str(primary.__notes__)
    before = ready.copy()
    engine.results()
    engine.results()
    assert ready == before
    for action in (engine.step, engine.run):
        with pytest.raises(SimulationExecutionError):
            action()
    monkeypatch.undo()
    engine.reset()
    assert engine.run()["network_metrics"]["delivered"] == 3


def test_reapplying_live_update_does_not_duplicate_callbacks() -> None:
    engine, _ = engine_with_request()
    wait_for_packets(engine, 1)
    operation = next(e[4] for e in engine.event_queue if e[3] == "network_complete")
    engine.apply_network_update(NetworkUpdate(operations=(operation,)))
    assert len([e for e in engine.event_queue if e[3] == "network_complete"]) == 1
    result = engine.run()
    engine._complete_network_operation(operation)
    assert engine.results() == result


@pytest.mark.parametrize("fault", ["history", "block_log", "submission_return"])
def test_submission_exceptions_never_leave_unregistered_orphaned_work(
    monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    engine, process = engine_with_request()
    primary = RuntimeError("submission boundary failed")
    manager = engine.network_manager
    if fault == "history":
        original = manager._event

        def fail_history(action: str, *args: Any, **kwargs: Any) -> None:
            if action == "PACKET_CREATE":
                raise primary
            original(action, *args, **kwargs)

        monkeypatch.setattr(manager, "_event", fail_history)
    elif fault == "block_log":
        original = engine.log

        def fail_log(process: Process, event: str, action: str, *args: Any) -> None:
            if action == "wait_for_network":
                raise primary
            original(process, event, action, *args)

        monkeypatch.setattr(engine, "log", fail_log)
    else:
        original = manager.submit_packet

        def fail_return(*args: Any, **kwargs: Any) -> str:
            original(*args, **kwargs)
            raise primary

        monkeypatch.setattr(manager, "submit_packet", fail_return)
    with pytest.raises(SimulationExecutionError):
        engine.run()
    assert engine._execution_exception is primary
    manager.validate()
    assert manager.runtime.operations == {}
    assert not engine._network_waiting
    assert process.state == ProcessState.RUNNING and process.blocked_time == 0


def test_network_request_near_end_and_multiple_offsets() -> None:
    p = Process("P", 0, 4, network_requests={0: spec(), 3: spec()})
    result = SimulationEngine([p], network_manager=NetworkManager(config())).run()
    assert p.blocked_time == 4 and p.completion_time == 8
    assert result["network_metrics"]["submitted"] == 2
    assert [outcome["request_offset"]
            for outcome in result["network_outcomes"].values()] == [0, 3]


def test_simultaneous_arrival_explicit_unblock_and_network_delivery() -> None:
    first = Process("A", 0, 1, network_requests={0: spec()})
    second = Process("B", 0, 1, blocking_requests={0: 2})
    third = Process("C", 2, 1)
    result = SimulationEngine([third, second, first],
                              network_manager=NetworkManager(config())).run()
    events = [e for e in result["events"]
              if e["timestamp"] == 2 and e["event_type"] in ("unblock", "arrival")]
    assert [e["entity_id"] for e in events] == ["A", "B", "C"]
    # FCFS preserves its existing input-order tie for equal original arrivals.
    assert [first.completion_time, second.completion_time,
            third.completion_time] == [4, 3, 5]


def test_security_authorization_error_is_persistent_but_does_not_block() -> None:
    class BrokenSecurity(SecurityManager):
        def authorize(self, *args: Any, **kwargs: Any) -> bool:
            raise RuntimeError("authorization backend failed")

    engine, process = engine_with_request(security_manager=BrokenSecurity())
    with pytest.raises(SimulationExecutionError, match="authorization backend failed"):
        engine.run()
    assert process.state == ProcessState.RUNNING
    assert engine.network_manager.runtime.packets == {}
    assert engine._network_waiting == {}


def test_registration_cleanup_entry_failure_still_invalidates_selected_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, process = engine_with_request()
    primary = RuntimeError("callback registration primary")
    original = engine.schedule_event

    def fail_registration(time: float, kind: str, payload: Any) -> None:
        if kind == "network_complete":
            raise primary
        original(time, kind, payload)

    def abort_unavailable(*args: Any, **kwargs: Any) -> NetworkUpdate:
        raise RuntimeError("abort entry unavailable")

    monkeypatch.setattr(engine, "schedule_event", fail_registration)
    monkeypatch.setattr(engine.network_manager, "abort_operations", abort_unavailable)
    with pytest.raises(SimulationExecutionError):
        engine.run()
    assert engine._execution_exception is primary
    assert "abort entry unavailable" in str(primary.__notes__)
    assert not engine.network_manager.runtime.operations
    assert not engine._network_waiting
    assert process.state == ProcessState.RUNNING


def test_multi_hop_boundaries_wake_only_on_delivery() -> None:
    process = Process("P", 0, 1, network_requests={0: spec(destination="c")})
    engine = SimulationEngine(
        [process], network_manager=NetworkManager(config(multi=True)),
    )
    result = engine.run()
    assert process.blocked_time == 4 and process.completion_time == 5
    assert result["network_packets"]["packet-0"]["path"] == ["ab", "bc"]
    assert [(entry["kind"], entry["start"], entry["end"])
            for entry in result["network_timeline"]] == [
        ("TRANSMISSION", 0, 1), ("PROPAGATION", 1, 2),
        ("TRANSMISSION", 2, 3), ("PROPAGATION", 3, 4),
    ]
    assert len([e for e in result["events"] if e["action"] == "network_interrupt"]) == 1


@pytest.mark.parametrize("destination", ["a", "b", "d"])
def test_zero_and_fractional_transport_under_rr(destination: str) -> None:
    process = Process("P", 0, 2, network_requests={0: spec(destination=destination)})
    engine = SimulationEngine([process], algorithm="rr", quantum=1,
                              network_manager=NetworkManager(config(
                                  delay=0, bandwidth=200,
                              )))
    result = engine.run()
    assert process.blocked_time == (0.5 if destination == "b" else 0)
    assert result["current_time"] == 2 + process.blocked_time
    assert cpu_time(result) == 2
    json.dumps(result, allow_nan=False)


def test_split_batch_abort_entry_failure_cannot_leave_orphaned_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = Process("A", 0, 1, network_requests={0: spec()})
    second = Process("B", 0, 1, network_requests={0: spec()})
    engine = SimulationEngine([first, second],
                              network_manager=NetworkManager(config(delay=2)))
    wait_for_packets(engine, 2)
    primary = RuntimeError("split registration primary")
    original = engine.schedule_event

    def register(time: float, kind: str, payload: Any) -> None:
        if kind == "network_complete":
            raise primary
        original(time, kind, payload)

    def unavailable(*args: Any, **kwargs: Any) -> NetworkUpdate:
        raise RuntimeError("abort entry unavailable")

    monkeypatch.setattr(engine, "schedule_event", register)
    monkeypatch.setattr(engine.network_manager, "abort_operations", unavailable)
    with pytest.raises(SimulationExecutionError):
        engine.run()
    assert engine._execution_exception is primary
    engine.network_manager.validate()
    assert engine.network_manager.runtime.operations == {}
    assert engine._network_waiting == {}
    assert first.state == second.state == ProcessState.READY
    assert first.blocked_time == second.blocked_time == 1


def test_initial_batch_preserves_current_submission_when_older_callback_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = Process("A", 0, 1, network_requests={0: spec()})
    second = Process("B", 0, 1, network_requests={0: spec()})
    submitting = Process("P", 0, 1, network_requests={
        0: spec(source="c", destination="d"),
    })
    engine = SimulationEngine([first, second, submitting],
                              network_manager=NetworkManager(independent_config()))
    wait_for_packets(engine, 2)
    # Fault fixture: free AB without dispatching B, so P's subsequent submission
    # publishes its own CD operation before scheduling the older AB waiter.
    engine.network_manager.cancel_packet("packet-0", 0)
    engine.check_arrivals()
    original = engine.schedule_event
    primary = RuntimeError("older B callback failed")
    registered = []

    def register(time: float, kind: str, payload: Any) -> None:
        if kind == "network_complete":
            if payload.packet_id == "packet-1":
                raise primary
            registered.append(payload)
        original(time, kind, payload)

    monkeypatch.setattr(engine, "schedule_event", register)
    with pytest.raises(SimulationExecutionError):
        engine.run()
    assert engine._execution_exception is primary
    assert registered and registered[0].packet_id == "packet-2"
    assert submitting.state == ProcessState.WAITING
    assert engine._network_waiting["packet-2"][0] is submitting
    operations = engine.network_manager.runtime.operations
    assert operations[registered[0].token] == registered[0]
    assert second.state == ProcessState.READY
    packet = engine.network_manager.runtime.packets["packet-1"]
    assert packet.state == PacketState.FAILED
    engine.network_manager.validate()


def test_failure_wakes_active_and_queued_processes_without_retries() -> None:
    first = Process("A", 0, 1, network_requests={0: spec()})
    second = Process("B", 0, 1, network_requests={0: spec()})
    engine = SimulationEngine([first, second], network_manager=NetworkManager(config()))
    wait_for_packets(engine, 2)
    engine.advance_time(0.5)
    engine.apply_network_update(engine.network_manager.fail_link("ab", 0.5))
    assert first.state == second.state == ProcessState.READY
    assert first.blocked_time == second.blocked_time == 0.5
    assert len(engine._network_outcomes) == 2
    engine.apply_network_update(engine.network_manager.recover_link("ab", 0.5))
    result = engine.run()
    assert result["current_time"] == 2.5
    assert result["network_metrics"]["submitted"] == 2
    assert result["network_metrics"]["failed"] == 1
    assert result["network_metrics"]["dropped"] == 1
    assert result["network_metrics"]["delivered"] == 0
    assert len([e for e in result["events"] if e["action"] == "network_interrupt"]) == 2


def test_failure_rerouting_registers_callbacks_and_retains_wait() -> None:
    from tests.test_network_manager import reroute_config

    first = Process("A", 0, 1, network_requests={0: spec()})
    second = Process("B", 0, 1, network_requests={0: spec()})
    engine = SimulationEngine([first, second],
                              network_manager=NetworkManager(reroute_config()))
    wait_for_packets(engine, 2)
    engine.advance_time(0.5)
    update = engine.network_manager.fail_link("ab", 0.5)
    assert update.operations[0].link_id == "ac"
    engine.apply_network_update(update)
    assert first.state == ProcessState.READY and second.state == ProcessState.WAITING
    result = engine.run()
    assert first.blocked_time == 0.5 and second.blocked_time == 2.5
    assert result["current_time"] == 3.5
    assert result["network_packets"]["packet-1"]["path"] == ["ac", "cb"]


@pytest.mark.parametrize("allow_network", [False, True])
def test_shared_offset_security_file_authorization_and_network_authorization_once(
    allow_network: bool,
) -> None:
    security = SecurityManager()
    permissions = ["file:create"] + (["network:send"] if allow_network else [])
    security.create_role("worker", permissions)
    security.add_user("P", ["worker"])
    process = Process("P", 0, 1, file_requests={0: "CREATE /once"},
                      blocking_requests={0: 1}, memory_references={0: 0},
                      device_requests={0: device_spec()}, network_requests={0: spec()})
    engine = SimulationEngine([process], security_manager=security,
                              memory_manager=MemoryManager(1, fault_latency=1),
                              device_manager=device_manager(service=1),
                              network_manager=NetworkManager(config()))
    result = engine.run()
    records = list(security.security_events)
    assert [(e["resource_type"], e["timestamp"]) for e in records] == [
        ("file", 0), ("network", 3),
    ]
    assert process.blocked_time == (5 if allow_network else 3)
    assert process.completion_time == (6 if allow_network else 4)
    assert result["network_metrics"]["submitted"] == int(allow_network)


def test_callback_metadata_filter_preserves_identically_named_topology_ids() -> None:
    topology = config()
    topology = replace(
        topology, nodes=(NodeConfig(node_id="generation"),
                         NodeConfig(node_id="token")),
        links=(replace(topology.links[0], link_id="token", source="generation",
                       destination="token"),),
    )
    process = Process("P", 0, 1, network_requests={
        0: spec(source="generation", destination="token"),
    })
    result = SimulationEngine([process], network_manager=NetworkManager(topology)).run()
    assert set(result["network_topology"]["nodes"]) == {"generation", "token"}
    assert set(result["network_topology"]["links"]) == {"token"}
    assert set(result["network_metrics"]["links"]) == {"token"}
    assert "generation" not in result["network_packets"]["packet-0"]


def test_old_notification_history_cannot_wake_reused_packet_id_after_reset() -> None:
    engine, process = engine_with_request()
    engine.run()
    old_snapshot = engine.network_manager.snapshot(engine.current_time)
    engine.reset()
    engine.network_manager.configure(config(delay=10), 0)
    wait_for_packets(engine, 1)
    engine.advance_time(2)
    assert process.state == ProcessState.WAITING
    with pytest.raises(ValueError, match="does not match"):
        engine._deliver_network_notifications(old_snapshot)
    assert process.state == ProcessState.WAITING and process.blocked_time == 0
    assert engine.run()["network_outcomes"]["packet-0"]["timestamp"] == 11
