"""Device callbacks integrated with CPU scheduling and the shared simulation clock."""

import inspect
import json
from dataclasses import replace

import pytest

from src.core.process import Process, ProcessState
from src.core.simulation import SimulationEngine, SimulationExecutionError
from src.managers.device import (
    DeviceConfig, DeviceManager, DeviceRequestSpec, DeviceType,
    DiskGeometry,
    OperationKind, RequestState, ScanDirection, SchedulingPolicy,
)
from src.managers.memory import MemoryManager


def manager(
    service: float = 3, *, disk: bool = False,
    policy: SchedulingPolicy = SchedulingPolicy.FCFS, count: int = 1,
) -> DeviceManager:
    """Create deterministic peripherals, optionally using real disk movements."""
    return DeviceManager([
        DeviceConfig(
            device_id=f"d{i}",
            device_type=DeviceType.DISK if disk else DeviceType.KEYBOARD,
            policy=policy, default_service_time=service,
            disk=DiskGeometry(
                min_cylinder=0, max_cylinder=99, initial_head=50,
                initial_direction=ScanDirection.UP, seek_time_per_cylinder=1,
            ) if disk else None,
        ) for i in range(count)
    ])


def spec(device: str = "d0", cylinder: int | None = None) -> DeviceRequestSpec:
    """Build a typed device request; JSON parsing is outside this phase."""
    return DeviceRequestSpec(device_id=device, operation="READ", cylinder=cylinder)


def run_bounded(engine: SimulationEngine) -> dict:
    """Use bounded stepping to make an integration loop failure explicit."""
    for _ in range(150):
        result = engine.step()
        if engine.completed == len(engine.processes):
            return result
    raise AssertionError("Simulation did not finish")


def cpu_time(result: dict) -> float:
    """Sum actual CPU execution, excluding device, idle, and context-switch time."""
    return sum(item["end"] - item["start"] for item in result["timeline"]
               if item["pid"] not in ("IDLE", "CS"))


def test_constructor_compatibility_and_owned_mapping() -> None:
    """The new input is keyword-only and copies caller-owned mappings."""
    assert inspect.signature(Process).parameters["device_requests"].kind == (
        inspect.Parameter.KEYWORD_ONLY
    )
    requests = {1: spec()}
    process = Process("P1", 0, 3, 1, {}, {}, {}, device_requests=requests)
    requests.clear()
    assert process.device_requests == {1: spec()}
    other = Process("P2", 0, 1)
    assert other.device_requests == {}
    process.device_requests.clear()
    assert other.device_requests == {}


@pytest.mark.parametrize("offset", [-1, 3, True, 0.5, "1"])
def test_invalid_offsets_rejected_before_execution(offset: object) -> None:
    """Engine validation reuses Phase 1 CPU-offset and spec contracts."""
    with pytest.raises(ValueError):
        SimulationEngine([Process("P", 0, 3, device_requests={offset: spec()})],
                         device_manager=manager())


@pytest.mark.parametrize("request_spec", [spec("unknown"), spec(cylinder=60), "READ"])
def test_invalid_specs_rejected(request_spec: object) -> None:
    """Unknown devices, wrong geometry, and unparsed inputs fail explicitly."""
    with pytest.raises(ValueError):
        SimulationEngine([Process("P", 0, 3, device_requests={0: request_spec})],
                         device_manager=manager())


def test_requests_require_manager() -> None:
    """No device input is silently dropped when device support is absent."""
    with pytest.raises(ValueError, match="require a device manager"):
        SimulationEngine([Process("P", 0, 1, device_requests={0: spec()})])
    with pytest.raises(ValueError):
        Process("P", 0, 1, device_requests=[])


@pytest.mark.parametrize("offset", [0, 1, 3])
def test_block_idle_and_resume_at_cpu_offsets(offset: int) -> None:
    """Long bursts stop at device offsets, including zero and burst minus one."""
    process = Process("P", 0, 4, device_requests={offset: spec()})
    result = run_bounded(SimulationEngine([process], device_manager=manager()))
    assert process.completion_time == 7
    assert process.blocked_time == 3
    assert process.waiting_time == 0
    assert cpu_time(result) == 4
    assert result["cpu_utilization"] == pytest.approx(400 / 7)
    blocks = [e for e in result["events"] if e["action"] == "wait_for_device"]
    assert len(blocks) == 1
    assert blocks[0]["timestamp"] == offset
    assert result["device_metrics"]["devices"]["d0"]["completed"] == 1
    json.dumps(result, allow_nan=False)


def test_other_process_runs_during_io_and_completion_does_not_preempt_fcfs() -> None:
    """A completion makes a process ready without forcing CPU preemption."""
    first = Process("P1", 0, 2, device_requests={0: spec()})
    second = Process("P2", 0, 5)
    result = SimulationEngine([first, second], device_manager=manager()).run()
    assert first.blocked_time == 3
    assert second.completion_time == 5
    assert first.completion_time == 7
    assert first.waiting_time == 2
    assert cpu_time(result) == 7
    assert [(e["timestamp"], e["entity_id"]) for e in result["events"]
            if e["action"] == "device_interrupt"] == [(3, "P1")]


def test_same_device_queue_wait_is_blocked_time() -> None:
    """The second process waits for both the first service and its own service."""
    processes = [Process(f"P{i}", 0, 1, device_requests={0: spec()}) for i in (1, 2)]
    result = SimulationEngine(processes, device_manager=manager()).run()
    assert [p.blocked_time for p in processes] == [3, 6]
    assert [p.completion_time for p in processes] == [4, 7]
    costs = result["device_metrics"]["requests"]
    assert [value["queue_waiting_time"] for value in costs.values()] == [0, 3]
    assert cpu_time(result) == 2


def test_two_devices_and_simultaneous_completions() -> None:
    """Devices overlap and deterministic callbacks wake their own processes only."""
    processes = [Process(f"P{i}", 0, 1, device_requests={0: spec(f"d{i-1}")})
                 for i in (1, 2)]
    engine = SimulationEngine(processes, device_manager=manager(count=2))
    result = engine.run()
    assert [p.blocked_time for p in processes] == [3, 3]
    assert [p.completion_time for p in processes] == [4, 5]
    assert [e["entity_id"] for e in result["events"]
            if e["action"] == "device_interrupt"] == ["P1", "P2"]
    assert all(value["busy_time"] == 3
               for value in result["device_metrics"]["devices"].values())
    engine.reset()
    assert engine.run() == result


def test_simultaneous_arrival_unblock_and_device_event() -> None:
    """Legacy PID/insertion ordering extends to device-owned process callbacks."""
    processes = [
        Process("P1", 0, 1, device_requests={0: spec()}),
        Process("P2", 0, 1, blocking_requests={0: 3}),
        Process("P3", 3, 1),
    ]
    result = SimulationEngine(processes, algorithm="rr", quantum=1,
                              device_manager=manager()).run()
    admissions = [e["entity_id"] for e in result["events"] if e["manager"] == "process"
                  and e["timestamp"] == 3 and e["event_type"] in ("arrival", "unblock")]
    assert admissions == ["P1", "P2", "P3"]
    assert [p.completion_time for p in processes] == [4, 5, 6]


def test_rr_io_ready_callback_and_quantum_counts() -> None:
    """Blocking resets the RR turn and delivery enters READY exactly once."""
    first = Process("P1", 0, 3, device_requests={1: spec()})
    second = Process("P2", 0, 5)
    engine = SimulationEngine([first, second], algorithm="rr", quantum=1,
                              device_manager=manager(service=2))
    result = engine.run()
    assert first.blocked_time == 2
    assert cpu_time(result) == 8
    assert len([e for e in result["events"] if e["action"] == "device_interrupt"]) == 1
    assert result["processes"][0]["preemption_count"] == 2


def test_srtf_preempts_only_after_device_ready() -> None:
    """A shorter resumed process preempts through existing SRTF rules."""
    first = Process("P1", 0, 2, device_requests={0: spec()})
    second = Process("P2", 0, 10)
    result = SimulationEngine([first, second], algorithm="srtf",
                              device_manager=manager()).run()
    assert first.completion_time == 5
    assert second.completion_time == 12
    assert result["processes"][1]["preemption_count"] == 1
    assert cpu_time(result) == 12


def test_completion_during_context_switch_overhead() -> None:
    """Context switching dispatches device callbacks without counting them as CPU."""
    first = Process("P1", 0, 2, device_requests={0: spec()})
    second = Process("P2", 0, 4)
    result = SimulationEngine([first, second], context_switch=2,
                              device_manager=manager(service=1)).run()
    interrupt = next(e for e in result["events"] if e["action"] == "device_interrupt")
    assert interrupt["timestamp"] == 1
    assert first.blocked_time == 1
    assert result["context_switch_overhead"] == 4
    assert result["current_time"] == 10
    assert cpu_time(result) == 6


def test_shared_offset_operations_execute_once_in_existing_order() -> None:
    """File, explicit wait, memory fault, then device wait share offset zero."""
    process = Process(
        "P", 0, 2, file_requests={0: "CREATE /once"}, blocking_requests={0: 1},
        memory_references={0: 0}, device_requests={0: spec()},
    )
    result = SimulationEngine(
        [process], device_manager=manager(service=3),
        memory_manager=MemoryManager(1, fault_latency=2),
    ).run()
    assert process.completion_time == 8
    assert process.blocked_time == 6
    assert len([e for e in result["events"] if e["manager"] == "file"]) == 1
    assert result["memory_metrics"]["references"] == 1
    assert result["device_metrics"]["devices"]["d0"]["submitted"] == 1
    assert [e["timestamp"] for e in result["events"]
            if e["event_type"] == "device_request"] == [3]


@pytest.mark.parametrize("service", [0, 0.5])
@pytest.mark.parametrize("disk", [False, True])
def test_zero_and_fractional_operations(service: float, disk: bool) -> None:
    """Same-time callbacks neither repeat submissions nor lose RR CPU state."""
    device = manager(service, disk=disk)
    if disk:
        cfg = device.devices["d0"].config
        geometry = replace(cfg.disk, seek_time_per_cylinder=0)
        device.configure([replace(cfg, disk=geometry)], 0)
    process = Process("P", 0, 3, device_requests={
        0: spec(cylinder=60 if disk else None),
        2: spec(cylinder=20 if disk else None),
    })
    result = run_bounded(SimulationEngine([process], algorithm="rr", quantum=1,
                                         device_manager=device))
    assert process.completion_time == 3 + 2 * service
    assert process.blocked_time == 2 * service
    assert result["device_metrics"]["devices"]["d0"]["completed"] == 2
    assert cpu_time(result) == 3


def test_scan_endpoint_without_ready_process() -> None:
    """An endpoint boundary does not itself wake CPU work; idle advances again."""
    process = Process("P", 0, 1, device_requests={0: spec(cylinder=20)})
    result = run_bounded(SimulationEngine(
        [process], device_manager=manager(2, disk=True, policy=SchedulingPolicy.SCAN),
    ))
    assert process.blocked_time == 130
    assert process.completion_time == 131
    assert result["device_metrics"]["devices"]["d0"]["head_movement"] == 128
    assert [entry["kind"] for entry in result["device_timeline"]] == [
        "SCAN_ENDPOINT", "REQUEST_SEEK", "SERVICE",
    ]


def test_reset_during_io_clears_associations_callbacks_and_retains_files() -> None:
    """Fresh runs compare equal even though internal callback generations differ."""
    process = Process("P", 0, 2, device_requests={0: spec()})
    engine = SimulationEngine([process], device_manager=manager())
    engine.file_manager.create_file("/retained")
    engine.step()
    engine.step()
    assert process.state == ProcessState.WAITING
    old = next(entry[4] for entry in engine.event_queue
               if entry[3] == "device_complete")
    engine.reset()
    assert engine._device_submitted == engine._device_waiting == {}
    assert engine.file_manager.read_file("/retained") == ""
    assert not any(e[3] == "device_complete" for e in engine.event_queue)
    # Inject an old generation callback alongside the new run's eventual callback.
    engine.schedule_event(old[1].due_at, "device_complete", old)
    first = engine.run()
    assert process.blocked_time == 3
    engine.reset()
    assert engine.run() == first


def test_duplicate_completion_cannot_double_wake_or_charge() -> None:
    """Two queue entries for the same callback still deliver only once."""
    process = Process("P", 0, 2, device_requests={0: spec()})
    engine = SimulationEngine([process], device_manager=manager())
    engine.step()
    engine.step()
    event = next(entry for entry in engine.event_queue if entry[3] == "device_complete")
    engine.schedule_event(event[0], event[3], event[4])
    result = engine.run()
    assert process.blocked_time == 3
    assert len([e for e in result["events"] if e["action"] == "device_interrupt"]) == 1


@pytest.mark.parametrize("action", ["failure", "cancel"])
def test_failure_and_cancellation_notifications_wake_process(action: str) -> None:
    """External device lifecycle actions are delivered at the engine's clock."""
    process = Process("P", 0, 2, device_requests={0: spec()})
    device = manager(service=10)
    engine = SimulationEngine([process], device_manager=device)
    engine.step()
    engine.step()
    engine.advance_time(2)
    request_id = next(iter(device.requests))
    if action == "failure":
        device.fail_device("d0", engine.current_time, "broken")
    else:
        device.cancel_request(request_id, engine.current_time, "stopped")
    engine.check_arrivals()
    assert process.state == ProcessState.READY
    assert process.blocked_time == 2
    engine.check_arrivals()
    result = engine.run()
    assert process.completion_time == 4
    assert result["device_outcomes"][request_id]["outcome"] == (
        "FAILED" if action == "failure" else "CANCELLED"
    )
    assert len([e for e in result["events"] if e["action"] == "device_interrupt"]) == 1


def test_no_device_workload_retains_cpu_results() -> None:
    """Adding an unused optional manager changes no existing scheduling results."""
    def workload() -> list[Process]:
        return [Process("P1", 0, 3, blocking_requests={1: 2}), Process("P2", 0, 2)]

    legacy = SimulationEngine(workload(), algorithm="rr", quantum=1).run()
    enabled = SimulationEngine(workload(), algorithm="rr", quantum=1,
                               device_manager=manager()).run()
    for field in legacy:
        if not field.startswith("device"):
            assert enabled[field] == legacy[field]
    assert legacy["device_metrics"] is None
    assert legacy["devices"] == legacy["device_requests"] == {}


def test_cancel_during_endpoint_drains_committed_movement_after_cpu_finishes() -> None:
    """A valid endpoint callback outlives cancellation and process termination."""
    device = manager(2, disk=True, policy=SchedulingPolicy.SCAN)
    process = Process("P", 0, 1, device_requests={0: spec(cylinder=20)})
    engine = SimulationEngine([process], device_manager=device)
    engine.step()
    engine.step()
    engine.advance_time(2)
    device.cancel_request(next(iter(device.requests)), engine.current_time)
    engine.check_arrivals()
    result = engine.run()
    assert process.completion_time == 3
    assert process.blocked_time == 2
    assert result["current_time"] == 49
    assert cpu_time(result) == 1
    assert result["devices"]["d0"]["head_position"] == 99
    assert result["device_metrics"]["devices"]["d0"]["busy_time"] == 49
    assert result["device_metrics"]["devices"]["d0"]["head_movement"] == 49
    assert result["devices"]["d0"]["active_operation"] is None


def test_execution_overflow_requires_reset_and_preserves_cause() -> None:
    """The audited generator failure cannot produce silent successful steps or Run."""
    process = Process("P", 1e308, 1, device_requests={0: spec()})
    device = manager(service=1e308)
    engine = SimulationEngine([process], device_manager=device)
    engine.step()
    with pytest.raises(SimulationExecutionError, match="Reset required") as failure:
        engine.step()
    assert isinstance(failure.value.__cause__, ValueError)
    original = engine.execution_error
    assert original["type"] == "ValueError"
    assert "finite" in original["message"]
    assert engine._runner is None
    assert device.requests == {}
    assert engine._device_waiting == engine._device_submitted == {}
    for action in (engine.step, engine.run, engine.step):
        with pytest.raises(SimulationExecutionError, match="Reset required"):
            action()
        assert engine.execution_error == original
    assert engine.results()["execution_error"] == original
    # Correct the offending input and reset; no callback or failed state survives.
    process.arrival_time = 0
    process.device_requests[0] = replace(spec(), service_time=1)
    engine.reset()
    assert engine.execution_error is None
    result = engine.run()
    assert result["current_time"] == 2
    assert result["execution_error"] is None


def test_scheduling_error_cancels_partial_submission() -> None:
    """A representable service alone may still overflow after seeking."""
    cfg = DeviceConfig(
        device_id="d0", device_type=DeviceType.DISK, policy=SchedulingPolicy.FCFS,
        default_service_time=5e307, disk=DiskGeometry(
            min_cylinder=0, max_cylinder=2, initial_head=0,
            initial_direction=ScanDirection.UP, seek_time_per_cylinder=5e307,
        ),
    )
    device = DeviceManager([cfg])
    process = Process("P", 5e307, 1, device_requests={0: spec(cylinder=2)})
    engine = SimulationEngine([process], device_manager=device)
    engine.step()
    with pytest.raises(SimulationExecutionError, match="finite"):
        engine.step()
    assert engine._device_waiting == engine._device_submitted == {}
    assert device.devices["d0"].queue == []
    assert device.devices["d0"].active_operation is None
    assert next(iter(device.requests.values())).state.value == "CANCELLED"
    assert process.state == ProcessState.RUNNING
    assert process.blocked_time == 0
    assert engine.results()["execution_error"] is not None
    device.validate()


def test_exhausted_runner_with_unfinished_work_fails_promptly() -> None:
    """StopIteration is success only when all required work is finished."""
    engine = SimulationEngine([Process("P", 0, 1)])
    engine._runner = iter(())
    with pytest.raises(SimulationExecutionError, match="unfinished work"):
        engine.step()
    with pytest.raises(SimulationExecutionError, match="Reset required"):
        engine.run()
    engine.reset()
    assert engine.run()["current_time"] == 1


@pytest.mark.parametrize("entry", ["step", "run"])
def test_no_progress_is_detected(entry: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both public execution paths guard an unchanged unfinished state."""
    engine = SimulationEngine([Process("P", 0, 1)])
    if entry == "step":
        engine._runner = iter([engine.results()])
    else:
        monkeypatch.setattr(engine, "step", engine.results)
    with pytest.raises(SimulationExecutionError, match="no progress"):
        getattr(engine, entry)()
    assert engine.execution_error["type"] == "RuntimeError"


@pytest.mark.parametrize("independent", [False, True])
@pytest.mark.parametrize("boundary", [
    "history", "blocked_sync", "block_log", "queue_before", "queue_after",
    "scheduled_sync",
])
def test_submission_transition_failure_cleanup(
    monkeypatch: pytest.MonkeyPatch, independent: bool, boundary: str,
) -> None:
    """All submission boundaries clean up only the affected process and device."""
    device = manager(service=10, count=2 if independent else 1)
    target = "d1" if independent else "d0"
    process = Process("P", 0, 1, device_requests={0: spec(target)})
    processes = ([Process("A", 0, 1, device_requests={0: spec()})]
                 if independent else []) + [process]
    engine = SimulationEngine(processes, device_manager=device)
    for _ in range(10):
        engine.step()
        if process.state == ProcessState.RUNNING:
            break
    assert process.state == ProcessState.RUNNING
    before = device.snapshot(engine.current_time)
    other_events = [e for e in engine.event_queue
                    if e[3] == "device_complete" and e[4][0] != target]
    other_waits = {key: value for key, value in engine._device_waiting.items()}
    failure = RuntimeError("submission boundary failed")
    original_event, original_sync = device._event, engine._sync_device_state
    original_log, original_schedule = engine.log, engine.schedule_event
    calls = 0

    def event(*args, **kwargs):
        if args[2] == "device_request" and args[0].config.device_id == target:
            raise failure
        return original_event(*args, **kwargs)

    def sync():
        nonlocal calls
        original_sync()
        if process.state == ProcessState.WAITING:
            calls += 1
            if calls == (2 if boundary == "scheduled_sync" else 1):
                raise failure

    def log(*args, **kwargs):
        original_log(*args, **kwargs)
        if args[0] is process and args[1] == "block":
            raise failure

    def schedule(timestamp, event_type, payload):
        if event_type == "device_complete" and payload[0] == target:
            if boundary == "queue_after":
                original_schedule(timestamp, event_type, payload)
            raise failure
        return original_schedule(timestamp, event_type, payload)

    with monkeypatch.context() as patch:
        if boundary == "history":
            patch.setattr(device, "_event", event)
        elif boundary == "block_log":
            patch.setattr(engine, "log", log)
        elif boundary.startswith("queue_"):
            patch.setattr(engine, "schedule_event", schedule)
        else:
            patch.setattr(engine, "_sync_device_state", sync)
        with pytest.raises(SimulationExecutionError) as raised:
            engine.step()
    assert raised.value.__cause__ is failure
    assert process.state == ProcessState.RUNNING
    assert process.blocked_time == 0
    assert engine._device_waiting == other_waits
    assert all(key[0] != "P" for key in engine._device_submitted)
    runtime = device.devices[target]
    assert runtime.queue == [] and runtime.active_operation is None
    requests = [r for r in device.requests.values() if r.pid == "P"]
    assert all(r.state.value == "CANCELLED" for r in requests)
    notifications = [n for n in device.snapshot(0)["notifications"]
                     if n["pid"] == "P"]
    assert len(notifications) == len(requests)
    if independent:
        assert device.snapshot(0)["devices"]["d0"] == before["devices"]["d0"]
        assert all(event in engine.event_queue for event in other_events)
    for event in list(engine.event_queue):
        if event[3] == "device_complete" and event[4][0] == target:
            snapshot = device.snapshot(0)
            engine._complete_device_operation(*event[4])
            assert device.snapshot(0) == snapshot
    device.validate()
    with pytest.raises(SimulationExecutionError, match="Reset required"):
        engine.run()
    engine.reset()
    first = engine.run()
    engine.reset()
    assert engine.run() == first


@pytest.mark.parametrize("secondary", ["cancel_after", "cancel_event", "diagnostic"])
def test_cleanup_errors_do_not_replace_submission_cause(
    monkeypatch: pytest.MonkeyPatch, secondary: str,
) -> None:
    """Secondary cleanup diagnostics cannot hide the original execution failure."""
    process = Process("P", 0, 1, device_requests={0: spec()})
    device = manager()
    engine = SimulationEngine([process], device_manager=device)
    engine.step()
    original = RuntimeError("original submission error")
    cleanup = ValueError("secondary cleanup error")
    original_log, original_cancel = engine.log, device.cancel_request
    original_sync, original_event = engine._sync_device_state, device._event

    def log(*args, **kwargs):
        if args[1] == "block" and secondary != "cancel_event":
            raise original
        if secondary == "diagnostic" and args[1] == "device_submission_failed":
            raise cleanup
        return original_log(*args, **kwargs)

    def cancel(*args, **kwargs):
        original_cancel(*args, **kwargs)
        raise cleanup

    def sync():
        original_sync()
        if device.devices["d0"].active_operation is not None:
            raise original

    def event(*args, **kwargs):
        if args[2] == "operation_abort":
            raise cleanup
        return original_event(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(engine, "log", log)
        if secondary == "cancel_after":
            patch.setattr(device, "cancel_request", cancel)
        elif secondary == "cancel_event":
            patch.setattr(engine, "_sync_device_state", sync)
            patch.setattr(device, "_event", event)
        with pytest.raises(SimulationExecutionError) as raised:
            engine.step()
    assert raised.value.__cause__ is original
    assert any("secondary cleanup error" in note for note in original.__notes__)
    assert process.state == ProcessState.RUNNING
    assert engine._device_waiting == engine._device_submitted == {}
    assert device.devices["d0"].queue == []
    assert device.devices["d0"].active_operation is None
    assert next(iter(device.requests.values())).state == RequestState.CANCELLED
    assert len(device.snapshot(0)["notifications"]) == 1
    device.validate()
    for event in engine.event_queue:
        if event[3] == "device_complete":
            snapshot = device.snapshot(0)
            engine._complete_device_operation(*event[4])
            assert device.snapshot(0) == snapshot
    engine.reset()
    first = engine.run()
    engine.reset()
    assert engine.run() == first


@pytest.mark.parametrize("action", ["failure", "cancel_active", "cancel_queued"])
def test_batch_terminal_wakeups_recovery_and_device_isolation(action: str) -> None:
    """Active/queued outcomes wake once while an independent device keeps running."""
    device = manager(service=10, count=2)
    processes = [Process(pid, 0, 1, device_requests={0: spec(dev)})
                 for pid, dev in [("A", "d0"), ("B", "d0"), ("C", "d1")]]
    engine = SimulationEngine(processes, device_manager=device)
    for _ in range(10):
        engine.step()
        if all(p.state == ProcessState.WAITING for p in processes):
            break
    assert all(p.state == ProcessState.WAITING for p in processes)
    calls = []
    on_ready = engine.scheduler.on_ready

    def ready(process, timestamp):
        calls.append((process.pid, timestamp))
        return on_ready(process, timestamp)

    engine.scheduler.on_ready = ready
    engine.advance_time(2)
    independent = device.snapshot(2)["devices"]["d1"]
    requests = {r.pid: r.request_id for r in device.requests.values()}
    if action == "failure":
        assert len(device.fail_device("d0", 2, "broken")) == 2
    else:
        pid = "A" if action == "cancel_active" else "B"
        device.cancel_request(requests[pid], 2)
        assert device.cancel_request(requests[pid], 2) == ()
    assert device.snapshot(2)["devices"]["d1"] == independent
    engine.check_arrivals()
    engine.check_arrivals()
    if action == "failure":
        assert device.schedule_io("d0", 2) is None
        device.recover_device("d0", 2)
        assert device.schedule_io("d0", 2) is None
    result = engine.run()
    assert sorted(pid for pid, _ in calls) == ["A", "B", "C"]
    assert len(result["device_outcomes"]) == 3
    expected = {"failure": [2, 2, 10], "cancel_active": [2, 12, 10],
                "cancel_queued": [10, 2, 10]}[action]
    assert [p.blocked_time for p in processes] == expected
    outcomes = [device.requests[requests[p]].state for p in ("A", "B")]
    assert outcomes == {
        "failure": [RequestState.FAILED, RequestState.FAILED],
        "cancel_active": [RequestState.CANCELLED, RequestState.COMPLETED],
        "cancel_queued": [RequestState.COMPLETED, RequestState.CANCELLED],
    }[action]
    assert len(device.snapshot(engine.current_time)["notifications"]) == 3
    device.validate()
    engine.reset()
    first = engine.run()
    engine.reset()
    assert engine.run() == first


def test_duplicate_notification_history_wakes_once() -> None:
    """Duplicate notification records cannot duplicate on_ready or blocked time."""
    process = Process("P", 0, 1, device_requests={0: spec()})
    device = manager(service=10)
    engine = SimulationEngine([process], device_manager=device)
    engine.step()
    engine.step()
    engine.advance_time(2)
    request_id = next(iter(device.requests))
    device.cancel_request(request_id, 2)
    device._notifications.append(device._notifications[-1])
    calls = []
    on_ready = engine.scheduler.on_ready

    def ready(process, timestamp):
        calls.append((process.pid, timestamp))
        return on_ready(process, timestamp)

    engine.scheduler.on_ready = ready
    engine.check_arrivals()
    engine.check_arrivals()
    assert calls == [("P", 2)]
    assert process.blocked_time == 2
    result = engine.run()
    assert len([e for e in result["events"] if e["action"] == "device_interrupt"]) == 1
    assert calls == [("P", 2)]


@pytest.mark.parametrize("invalid", ["pid", "device", "terminated"])
def test_invalid_notification_cannot_wake_process(invalid: str) -> None:
    """Owner validation precedes state, scheduler, association, and time mutation."""
    process = Process("P", 0, 1, device_requests={0: spec()})
    device = manager(service=10)
    engine = SimulationEngine([process], device_manager=device)
    engine.step()
    engine.step()
    engine.advance_time(2)
    device.cancel_request(next(iter(device.requests)), 2)
    notification = device._notifications[-1]
    if invalid == "terminated":
        process.state = ProcessState.TERMINATED
    else:
        device._notifications[-1] = replace(
            notification, **({"pid": "wrong"} if invalid == "pid" else
                             {"device_id": "wrong"}),
        )
    before = (process.state, process.blocked_time,
              dict(engine._device_waiting), engine._device_notification_cursor)
    calls = []
    on_ready = engine.scheduler.on_ready

    def ready(process, timestamp):
        calls.append((process.pid, timestamp))
        return on_ready(process, timestamp)

    engine.scheduler.on_ready = ready
    with pytest.raises(ValueError, match="does not match"):
        engine.check_arrivals()
    assert calls == []
    assert (process.state, process.blocked_time, engine._device_waiting,
            engine._device_notification_cursor) == before


@pytest.mark.parametrize("submitting_device", ["d0", "d1"])
@pytest.mark.parametrize("recorded", [False, True])
@pytest.mark.parametrize("cleanup", ["none", "abort_history", "cancel_after"])
def test_older_queued_request_callback_failure_is_finalized(
    monkeypatch: pytest.MonkeyPatch, submitting_device: str,
    recorded: bool, cleanup: str,
) -> None:
    """Synchronization must clean up B's failed registration, not just P's input."""
    device = manager(service=10, count=3)
    processes = [Process(pid, 0, 1, device_requests={0: spec(dev)})
                 for pid, dev in [("A", "d0"), ("B", "d0"), ("C", "d2"),
                                  ("P", submitting_device)]]
    engine = SimulationEngine(processes, device_manager=device)
    for _ in range(12):
        engine.step()
        if processes[-1].state == ProcessState.RUNNING:
            break
    assert [p.state for p in processes] == [ProcessState.WAITING] * 3 + [
        ProcessState.RUNNING,
    ]
    ids = {request.pid: request.request_id for request in device.requests.values()}
    independent = device.devices["d2"]
    independent_callbacks = [e for e in engine.event_queue
                             if e[3] == "device_complete" and e[4][0] == "d2"]
    device.cancel_request(ids["A"], 0)
    assert device.requests[ids["B"]].state == RequestState.QUEUED
    failure = RuntimeError("B callback registration failed")
    original_schedule, on_ready = engine.schedule_event, engine.scheduler.on_ready
    original_cancel, original_event = device.cancel_request, device._event
    attempted, wakeups = [], []
    failed_operations = []

    def schedule(timestamp, event_type, payload):
        if event_type == "device_complete":
            attempted.append(payload[1].request_id)
            if payload[1].request_id == ids["B"]:
                failed_operations.append(payload)
                if recorded:
                    original_schedule(timestamp, event_type, payload)
                raise failure
        return original_schedule(timestamp, event_type, payload)

    def ready(process, timestamp):
        wakeups.append((process.pid, timestamp))
        return on_ready(process, timestamp)

    def cancel(*args, **kwargs):
        result = original_cancel(*args, **kwargs)
        if args[0] == ids["B"]:
            raise ValueError("secondary B cleanup failure")
        return result

    def event(*args, **kwargs):
        request = kwargs.get("request")
        if (args[2] == "operation_abort" and request is not None
                and request.request_id == ids["B"]):
            raise ValueError("secondary B cleanup failure")
        return original_event(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(engine, "schedule_event", schedule)
        patch.setattr(engine.scheduler, "on_ready", ready)
        if cleanup == "cancel_after":
            patch.setattr(device, "cancel_request", cancel)
        elif cleanup == "abort_history":
            patch.setattr(device, "_event", event)
        with pytest.raises(SimulationExecutionError) as raised:
            engine.step()
        engine._sync_device_state()
        engine._sync_device_state()
    assert raised.value.__cause__ is failure
    assert attempted == [ids["B"]]
    if cleanup != "none":
        assert any("secondary B cleanup failure" in n for n in failure.__notes__)
    assert device.requests[ids["B"]].state == RequestState.CANCELLED
    assert processes[1].state == ProcessState.READY
    assert processes[1].blocked_time == 0
    assert ids["B"] not in engine._device_waiting
    assert sorted(wakeups) == [("A", 0), ("B", 0)]
    assert processes[-1].state == ProcessState.RUNNING
    assert device.devices["d0"].active_operation is None
    assert device.devices["d2"] == independent
    assert all(event in engine.event_queue for event in independent_callbacks)
    assert sum(n["request_id"] == ids["B"]
               for n in device.snapshot(0)["notifications"]) == 1
    before = device.snapshot(0)
    for payload in failed_operations:
        engine._complete_device_operation(*payload)
    assert device.snapshot(0) == before
    device.validate()
    with pytest.raises(SimulationExecutionError, match="Reset required"):
        engine.run()
    engine.reset()
    first = engine.run()
    engine.reset()
    assert engine.run() == first


def test_synchronization_failure_preserves_earlier_scheduled_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later device failure cannot roll back another device's valid callback."""
    device = manager(service=10, count=4)
    processes = [Process(pid, 0, 1, device_requests={0: spec(dev)})
                 for pid, dev in [("A", "d0"), ("B", "d1"), ("C", "d2"),
                                  ("D", "d0"), ("E", "d1"), ("P", "d3")]]
    engine = SimulationEngine(processes, device_manager=device)
    for _ in range(20):
        engine.step()
        if processes[-1].state == ProcessState.RUNNING:
            break
    assert processes[-1].state == ProcessState.RUNNING
    ids = {r.pid: r.request_id for r in device.requests.values()}
    device.cancel_request(ids["A"], 0)
    device.cancel_request(ids["B"], 0)
    independent = device.devices["d2"]
    failure = RuntimeError("E registration failed after D was scheduled")
    original = engine.schedule_event
    scheduled = []

    def schedule(timestamp, kind, payload):
        if kind == "device_complete":
            scheduled.append(payload[1].request_id)
            if payload[1].request_id == ids["E"]:
                raise failure
        return original(timestamp, kind, payload)

    with monkeypatch.context() as patch:
        patch.setattr(engine, "schedule_event", schedule)
        with pytest.raises(SimulationExecutionError) as raised:
            engine.step()
    assert raised.value.__cause__ is failure
    assert scheduled == [ids["D"], ids["E"]]
    operation = device.devices["d0"].active_operation
    assert operation.request_id == ids["D"]
    assert any(e[3] == "device_complete" and e[4][1] == operation
               for e in engine.event_queue)
    assert processes[3].state == ProcessState.WAITING
    assert processes[4].state == ProcessState.READY
    assert device.requests[ids["E"]].state == RequestState.CANCELLED
    assert device.devices["d1"].active_operation is None
    assert device.devices["d2"] == independent
    device.validate()
    engine.reset()
    first = engine.run()
    engine.reset()
    assert engine.run() == first


@pytest.mark.parametrize("boundary", [
    "activation", "activation_operation", "seek_service", "seek_activation",
    "endpoint",
])
@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_scheduling_boundary_cleanup_and_continuations(
    monkeypatch: pytest.MonkeyPatch, boundary: str, cleanup_failure: bool,
) -> None:
    """Partial activation, seek continuations, and requestless movement fail closed."""
    disk = boundary in ("seek_service", "seek_activation", "endpoint")
    policy = SchedulingPolicy.SCAN if boundary == "endpoint" else SchedulingPolicy.FCFS
    device = manager(service=2, disk=disk, policy=policy)
    cylinder = 20 if boundary == "endpoint" else 60 if disk else None
    process = Process("P", 0, 1, device_requests={0: spec(cylinder=cylinder)})
    engine = SimulationEngine([process], device_manager=device)
    engine.step()
    if boundary in ("seek_service", "seek_activation"):
        engine.step()
        assert process.state == ProcessState.WAITING
    failure = RuntimeError("selected operation could not be registered")
    original_event, original_schedule = device._event, engine.schedule_event
    wakeups = []
    on_ready = engine.scheduler.on_ready

    def ready(process, timestamp):
        wakeups.append((process.pid, timestamp))
        return on_ready(process, timestamp)

    def event(*args, **kwargs):
        if ((boundary == "activation" and args[2] == "device_start")
                or (boundary in ("activation_operation", "seek_activation")
                    and args[2] == "service")):
            raise failure
        cleanup_kind = "operation_abort" if boundary != "activation" else "device_cancelled"
        if cleanup_failure and args[2] == cleanup_kind:
            raise ValueError("secondary scheduling cleanup history error")
        return original_event(*args, **kwargs)

    def schedule(timestamp, kind, payload):
        if kind == "device_complete" and payload[1].kind == (
            OperationKind.SERVICE if boundary == "seek_service" else
            OperationKind.SCAN_ENDPOINT
        ):
            raise failure
        return original_schedule(timestamp, kind, payload)

    with monkeypatch.context() as patch:
        patch.setattr(device, "_event", event)
        patch.setattr(engine, "schedule_event", schedule)
        patch.setattr(engine.scheduler, "on_ready", ready)
        with pytest.raises(SimulationExecutionError) as raised:
            engine.run()
    assert raised.value.__cause__ is failure
    if cleanup_failure:
        assert any("secondary scheduling cleanup" in n for n in failure.__notes__)
    request = next(iter(device.requests.values()))
    assert request.state == (RequestState.FAILED if boundary == "endpoint"
                             else RequestState.CANCELLED)
    assert device.devices["d0"].active_operation is None
    assert device.devices["d0"].queue == []
    assert engine._device_waiting == {}
    assert len(device.snapshot(engine.current_time)["notifications"]) == 1
    if boundary in ("seek_service", "seek_activation"):
        assert process.state == ProcessState.READY
        assert process.blocked_time == 10
        assert wakeups == [("P", 10)]
        assert device.devices["d0"].head_position == 60
    else:
        assert process.state == ProcessState.RUNNING
        assert process.blocked_time == 0
        assert wakeups == []
    device.validate()
    engine.reset()
    first = engine.run()
    engine.reset()
    assert engine.run() == first
