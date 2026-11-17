"""Standalone lifecycle, execution, notifications, and accounting tests."""

import json
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace

import pytest

from src.managers.device import (
    DeviceConfig, DeviceManager, DeviceOperation, DeviceRequestSpec, DeviceState,
    DeviceType, DiskGeometry, OperationKind, OperationResult, RequestState,
    ScanDirection, SchedulingPolicy,
)


def config(
    policy: SchedulingPolicy = SchedulingPolicy.FCFS, *, device_id: str = "disk",
    kind: DeviceType = DeviceType.DISK, seek: float = 1, service: float = 2,
) -> DeviceConfig:
    """Build deterministic disk/printer/keyboard configurations."""
    return DeviceConfig(
        device_id=device_id, device_type=kind, policy=policy,
        default_service_time=service,
        disk=DiskGeometry(
            min_cylinder=0, max_cylinder=99, initial_head=50,
            initial_direction=ScanDirection.UP, seek_time_per_cylinder=seek,
        ) if kind == DeviceType.DISK else None,
    )


def spec(
    cylinder: int | None = 60, *, device_id: str = "disk", operation: str = "READ",
    service: float | None = None,
) -> DeviceRequestSpec:
    """Build request input without runtime state."""
    return DeviceRequestSpec(
        device_id=device_id, operation=operation, cylinder=cylinder,
        service_time=service,
    )


def finish(
    manager: DeviceManager, device_id: str, operation: DeviceOperation,
) -> OperationResult:
    """Deliver the exact completion instruction and check stable invariants."""
    result = manager.complete_operation(
        device_id, operation.token, operation.generation, operation.due_at,
    )
    manager.validate()
    return result


def drain(manager: DeviceManager, timestamp: float = 0) -> float:
    """External event driver: never let the manager advance time itself."""
    for _ in range(200):
        for device_id in manager.devices:
            manager.schedule_io(device_id, timestamp)
        active = [(runtime.active_operation.due_at, key, runtime.active_operation)
                  for key, runtime in manager.devices.items()
                  if runtime.active_operation is not None]
        if not active:
            return timestamp
        timestamp, key, operation = min(active)
        finish(manager, key, operation)
    raise AssertionError("Finite workload did not terminate")


@pytest.mark.parametrize("policy,path,movement,order", [
    (SchedulingPolicy.FCFS, [60, 20, 70], 100, [0, 1, 2]),
    (SchedulingPolicy.SSTF, [60, 70, 20], 70, [0, 2, 1]),
    (SchedulingPolicy.SCAN, [60, 70, 99, 20], 128, [0, 2, 1]),
])
def test_completed_reference_execution(
    policy: SchedulingPolicy, path: list[int], movement: int, order: list[int],
) -> None:
    """Verify actual completed seeks/services and endpoint accounting."""
    manager = DeviceManager([config(policy)])
    ids = [manager.submit_request(f"P{i}", spec(cylinder), 0)
           for i, cylinder in enumerate((60, 20, 70))]
    end = drain(manager)
    snapshot = manager.snapshot(end)
    assert [n["request_id"] for n in snapshot["notifications"]] == [
        ids[i] for i in order
    ]
    assert [entry["target_cylinder"] for entry in snapshot["timeline"]
            if entry["kind"] != "SERVICE"] == path
    metrics = snapshot["metrics"]["devices"]["disk"]
    assert metrics["head_movement"] == movement
    assert metrics["busy_time"] == end == movement + 6
    assert metrics["completed"] == metrics["interrupt_count"] == 3
    assert metrics["utilization"] == 100
    assert sum(m["seek_time"] for m in snapshot["metrics"]["requests"].values()) == (
        movement - (29 if policy == SchedulingPolicy.SCAN else 0)
    )
    assert sum(m["service_time"] for m in snapshot["metrics"]["requests"].values()) == 6
    assert sum(e["event_type"] == "interrupt" for e in snapshot["events"]) == 3


def test_submission_ids_validation_and_defensive_properties() -> None:
    """Invalid requests and duplicate IDs leave all state/counters untouched."""
    manager = DeviceManager([config()])
    key = manager.submit_request("P1", spec(), 0, request_id="request-1")
    generated = manager.submit_request("P2", spec(), 0)
    assert generated == "request-2"
    assert manager.submit_request("P3", spec(), 0) == "request-3"
    original = manager.snapshot(0)
    for pid, request, request_id in (
        ("P1", spec(), key), ("", spec(), None),
        ("P1", spec(device_id="missing"), None),
        ("P1", spec(100), None), ("P1", spec(operation="PRINT"), None),
        ("P1", None, None), ("P1", spec(service=-1), None),
    ):
        with pytest.raises(ValueError):
            manager.submit_request(pid, request, 0, request_id=request_id)
        assert manager.snapshot(0) == original
    manager.devices["disk"].queue.clear()
    manager.requests[key].state = RequestState.COMPLETED
    assert manager.snapshot(0) == original
    manager.validate()


def test_configuration_is_atomic_and_requires_pristine_run() -> None:
    """Configuration cannot erase a workload or silently replace duplicate IDs."""
    manager = DeviceManager()
    manager.configure([config()], 0)
    original = manager.snapshot(0)
    with pytest.raises(ValueError, match="Duplicate"):
        manager.configure([config(), config()], 0)
    assert manager.snapshot(0) == original
    manager.submit_request("P1", spec(), 0)
    with pytest.raises(ValueError, match="pristine"):
        manager.configure([], 0)
    manager.reset()
    manager.configure([], 0)
    assert manager.devices == {}


def test_seek_service_boundaries_and_nonpreemption() -> None:
    """A reserved request retains service across arrivals at the seek boundary."""
    manager = DeviceManager([config(SchedulingPolicy.SSTF)])
    first = manager.submit_request("P1", spec(70), 0)
    seek = manager.schedule_io("disk", 0)
    assert seek.kind == OperationKind.REQUEST_SEEK
    assert seek.due_at == 20
    assert manager.devices["disk"].head_position == 50
    manager.submit_request("P2", spec(55), 5)
    assert manager.schedule_io("disk", 5) is None
    service = finish(manager, "disk", seek).next_operation
    assert service.kind == OperationKind.SERVICE
    assert service.due_at == 22
    assert manager.devices["disk"].head_position == 70
    assert manager.devices["disk"].active_request_id == first
    assert manager.schedule_io("disk", 20) is None
    result = finish(manager, "disk", service)
    assert result.next_operation is None
    assert result.notifications[0].request_id == first
    assert manager.devices["disk"].state == DeviceState.IDLE
    assert len(manager.devices["disk"].queue) == 1


def test_scan_endpoint_arrival_cancellation_and_reversal() -> None:
    """Endpoint travel survives queue cancellation and cannot intercept arrivals."""
    manager = DeviceManager([config(SchedulingPolicy.SCAN)])
    cancelled = manager.submit_request("P1", spec(20), 0)
    endpoint = manager.schedule_io("disk", 0)
    assert endpoint.kind == OperationKind.SCAN_ENDPOINT
    assert endpoint.due_at == 49
    manager.cancel_request(cancelled, 5)
    arrived = manager.submit_request("P2", spec(60), 10)
    assert manager.schedule_io("disk", 10) is None
    finish(manager, "disk", endpoint)
    runtime = manager.devices["disk"]
    assert runtime.head_position == 99
    assert runtime.direction == ScanDirection.DOWN
    operation = manager.schedule_io("disk", 49)
    assert operation.request_id == arrived
    assert operation.target_cylinder == 60
    end = drain(manager, 49)
    assert manager.metrics(end)["devices"]["disk"]["head_movement"] == 88


@pytest.mark.parametrize("kind", [DeviceType.PRINTER, DeviceType.KEYBOARD])
def test_peripheral_serialization_and_wait_metrics(kind: DeviceType) -> None:
    """Waiting is queue time, distinct from service and elapsed turnaround."""
    manager = DeviceManager([config(kind=kind, service=4)])
    request_spec = spec(
        None, operation="PRINT" if kind == DeviceType.PRINTER else "READ",
    )
    first = manager.submit_request("P1", request_spec, 0)
    second = manager.submit_request("P2", request_spec, 0)
    end = drain(manager)
    metrics = manager.metrics(end)
    device = metrics["devices"]["disk"]
    assert end == 8
    assert device["average_waiting_time"] == 2
    assert device["average_queue_length"] == 0.5
    assert device["queue_area"] == 4
    assert device["max_queue_length"] == 2
    assert device["head_movement"] == 0
    assert device["throughput"] == 0.25
    assert metrics["requests"][first]["queue_waiting_time"] == 0
    assert metrics["requests"][second]["queue_waiting_time"] == 4
    assert metrics["requests"][second]["turnaround_time"] == 8


def test_multiple_devices_and_simultaneous_completions() -> None:
    """Each device has one active operation while both execute concurrently."""
    manager = DeviceManager([
        config(device_id="a", kind=DeviceType.PRINTER, service=4),
        config(device_id="b", kind=DeviceType.KEYBOARD, service=4),
    ])
    for key, operation in (("a", "PRINT"), ("b", "READ")):
        manager.submit_request(key, spec(None, device_id=key, operation=operation), 0)
    a = manager.schedule_io("a", 0)
    b = manager.schedule_io("b", 0)
    assert a.token != b.token
    assert a.due_at == b.due_at == 4
    assert len(finish(manager, "a", a).notifications) == 1
    assert len(finish(manager, "b", b).notifications) == 1
    assert all(m["busy_time"] == 4 for m in manager.metrics(4)["devices"].values())


@pytest.mark.parametrize("seek,service", [(0, 0), (0, 2), (1, 0)])
@pytest.mark.parametrize("policy", list(SchedulingPolicy))
def test_zero_duration_operations_terminate(
    seek: float, service: float, policy: SchedulingPolicy,
) -> None:
    """Finite requests need finite explicit callbacks, including zero-distance work."""
    manager = DeviceManager([config(policy, seek=seek, service=service)])
    for cylinder in (50, 60, 20, 70):
        manager.submit_request("P", spec(cylinder), 0)
    end = drain(manager)
    metrics = manager.metrics(end)["devices"]["disk"]
    assert metrics["completed"] == metrics["interrupt_count"] == 4
    if seek == service == 0:
        assert end == 0
        assert metrics["utilization"] == metrics["throughput"] == 0


@pytest.mark.parametrize("phase", ["seek", "service", "endpoint"])
def test_failure_aborts_work_preserves_head_and_recovers(phase: str) -> None:
    """Failed callbacks are invalidated; recovery does not retry requests."""
    manager = DeviceManager([config(SchedulingPolicy.SCAN)])
    first = manager.submit_request("P1", spec(20 if phase == "endpoint" else 60), 0)
    operation = manager.schedule_io("disk", 0)
    if phase == "service":
        operation = finish(manager, "disk", operation).next_operation
    now = operation.started_at + 1
    second = manager.submit_request("P2", spec(70), now)
    expected_head = 60 if phase == "service" else 50
    notifications = manager.fail_device("disk", now, "broken")
    assert {n.request_id for n in notifications} == {first, second}
    assert all(n.outcome == RequestState.FAILED for n in notifications)
    assert manager.devices["disk"].head_position == expected_head
    assert manager.devices["disk"].state == DeviceState.FAILED
    assert manager.metrics(now)["devices"]["disk"]["failed"] == 2
    before = manager.snapshot(now)
    with pytest.raises(ValueError, match="Stale"):
        finish(manager, "disk", operation)
    assert manager.snapshot(now) == before
    with pytest.raises(ValueError):
        manager.submit_request("P3", spec(), now)
    manager.recover_device("disk", now + 1)
    assert manager.devices["disk"].head_position == expected_head
    assert manager.devices["disk"].queue == []
    assert manager.schedule_io("disk", now + 1) is None
    manager.validate()


@pytest.mark.parametrize("phase", ["queued", "seek", "service"])
def test_cancellation_is_terminal_and_idempotent(phase: str) -> None:
    """Queued/active cancellation emits once and preserves committed movement."""
    manager = DeviceManager([config()])
    key = manager.submit_request("P", spec(), 0)
    operation = None
    if phase != "queued":
        operation = manager.schedule_io("disk", 0)
        if phase == "service":
            operation = finish(manager, "disk", operation).next_operation
    now = operation.started_at + 1 if operation else 1
    notification, = manager.cancel_request(key, now, "user stopped")
    assert notification.outcome == RequestState.CANCELLED
    assert notification.reason == "user stopped"
    assert manager.cancel_request(key, now) == ()
    assert manager.metrics(now)["devices"]["disk"]["interrupt_count"] == 1
    assert manager.devices["disk"].head_position == (60 if phase == "service" else 50)
    if operation is not None:
        with pytest.raises(ValueError, match="Stale"):
            finish(manager, "disk", operation)
    with pytest.raises(ValueError, match="Unknown"):
        manager.cancel_request("missing", now)
    manager.validate()


def test_cancelling_completed_request_preserves_success() -> None:
    """A terminal outcome is never overwritten by cancellation."""
    manager = DeviceManager([config()])
    key = manager.submit_request("P", spec(), 0)
    end = drain(manager)
    assert manager.cancel_request(key, end) == ()
    assert manager.requests[key].state == RequestState.COMPLETED


@pytest.mark.parametrize("problem", ["token", "generation", "early", "late", "bool"])
def test_invalid_callbacks_are_atomic(problem: str) -> None:
    """A rejected callback does not consume work or advance accounting."""
    manager = DeviceManager([config()])
    manager.submit_request("P", spec(), 0)
    operation = manager.schedule_io("disk", 0)
    before = manager.snapshot(0)
    token = "wrong" if problem == "token" else operation.token
    generation = 1 if problem == "generation" else operation.generation
    if problem == "bool":
        generation = False
    timestamp = 9 if problem == "early" else 11 if problem == "late" else 10
    with pytest.raises(ValueError):
        manager.complete_operation("disk", token, generation, timestamp)
    assert manager.snapshot(0) == before
    finish(manager, "disk", operation)
    with pytest.raises(ValueError, match="Stale"):
        finish(manager, "disk", operation)


def test_reset_invalidates_callbacks_restores_state_and_reproduces_metrics() -> None:
    """Behavior repeats while callback tokens distinguish successive generations."""
    manager = DeviceManager([config()])
    key = manager.submit_request("P", spec(), 0)
    old = manager.schedule_io("disk", 0)
    manager.reset()
    assert manager.requests == {}
    assert manager.devices["disk"].head_position == 50
    assert manager.snapshot(0)["notifications"] == []
    assert manager.submit_request("P", spec(), 0) == key
    new = manager.schedule_io("disk", 0)
    assert old.token != new.token
    with pytest.raises(ValueError, match="Stale"):
        finish(manager, "disk", old)
    end = drain(manager)
    expected = manager.metrics(end)
    manager.reset()
    manager.submit_request("P", spec(), 0)
    assert manager.metrics(drain(manager)) == expected


def test_availability_and_unavailable_accounting() -> None:
    """Offline/failed time is separate from idle time and cannot accept work."""
    manager = DeviceManager([config()])
    manager.set_offline("disk", 2)
    with pytest.raises(ValueError):
        manager.submit_request("P", spec(), 3)
    manager.set_online("disk", 5)
    metrics = manager.metrics(7)["devices"]["disk"]
    assert metrics["idle_time"] == 4
    assert metrics["unavailable_time"] == 3
    assert metrics["busy_time"] == 0
    manager.submit_request("P", spec(), 7)
    with pytest.raises(ValueError):
        manager.set_offline("disk", 7)
    with pytest.raises(ValueError):
        manager.recover_device("disk", 7)
    manager.schedule_io("disk", 7)
    with pytest.raises(ValueError):
        manager.set_offline("disk", 7)


def test_partial_metrics_read_only_json_and_detached_results() -> None:
    """Reads project elapsed costs without changing counters or leaking objects."""
    manager = DeviceManager([config()])
    first = manager.submit_request("P1", spec(), 0)
    second = manager.submit_request("P2", spec(70), 0)
    manager.schedule_io("disk", 2)
    before = deepcopy(manager.__dict__)
    snapshot = manager.snapshot(5)
    assert manager.__dict__ == before
    json.dumps(snapshot, allow_nan=False)
    device = snapshot["metrics"]["devices"]["disk"]
    assert device["busy_time"] == 3
    assert device["idle_time"] == 2
    assert device["utilization"] == 60
    assert device["queue_area"] == 7
    assert device["average_queue_length"] == 1.4
    assert snapshot["metrics"]["requests"][first]["seek_time"] == 3
    assert snapshot["metrics"]["requests"][first]["queue_waiting_time"] == 2
    assert snapshot["metrics"]["requests"][second]["queue_waiting_time"] == 5
    snapshot["devices"]["disk"]["queue"].clear()
    snapshot["events"].clear()
    assert manager.__dict__ == before


def test_service_start_time_and_abort_costs() -> None:
    """Actual transfer start differs from request activation before seeking."""
    manager = DeviceManager([config(service=4)])
    key = manager.submit_request("P", spec(), 0)
    seek = manager.schedule_io("disk", 2)
    service = finish(manager, "disk", seek).next_operation
    manager.fail_device("disk", 14, "failure")
    metrics = manager.metrics(14)["requests"][key]
    assert service.started_at == metrics["service_start_time"] == 12
    assert metrics["start_time"] == 2
    assert metrics["seek_time"] == 10
    assert metrics["service_time"] == 2
    assert metrics["queue_waiting_time"] == 2
    assert metrics["turnaround_time"] == 14


@pytest.mark.parametrize("timestamp", [-1, True, float("nan"), float("inf"), "1"])
def test_invalid_timestamps_are_rejected(timestamp: object) -> None:
    """Every explicit timestamp must be finite, numeric, and nonnegative."""
    manager = DeviceManager([config()])
    before = manager.snapshot(0)
    with pytest.raises(ValueError):
        manager.submit_request("P", spec(), timestamp)
    with pytest.raises(ValueError):
        manager.metrics(timestamp)
    with pytest.raises(ValueError):
        manager.reset(timestamp)
    assert manager.snapshot(0) == before


def test_time_order_and_skipped_completions_rejected() -> None:
    """The caller must process outstanding events instead of skipping boundaries."""
    manager = DeviceManager([config()])
    manager.submit_request("P", spec(), 2)
    with pytest.raises(ValueError, match="precedes"):
        manager.schedule_io("disk", 1)
    manager.schedule_io("disk", 2)
    with pytest.raises(ValueError, match="outstanding"):
        manager.snapshot(13)
    with pytest.raises(ValueError, match="outstanding"):
        manager.submit_request("P2", spec(), 13)


def test_overflow_does_not_mutate_state() -> None:
    """Reject even the earliest unrepresentable finish before enqueuing a request."""
    manager = DeviceManager([config(service=1e308)], timestamp=1e308)
    before = manager.snapshot(1e308)
    with pytest.raises(ValueError, match="finite"):
        manager.submit_request("P", spec(50), 1e308)
    assert manager.snapshot(1e308) == before


def test_tiny_service_overflow_is_null_and_strict_json_safe() -> None:
    """Positive subnormal service times do not produce Infinity or a false zero."""
    manager = DeviceManager([config(kind=DeviceType.KEYBOARD, service=1e-320)])
    manager.submit_request("P", spec(None), 0)
    operation = manager.schedule_io("disk", 0)
    partial = manager.snapshot(operation.due_at / 2)
    assert partial["metrics"]["devices"]["disk"]["throughput"] == 0
    json.dumps(partial, allow_nan=False)
    finish(manager, "disk", operation)
    snapshot = manager.snapshot(operation.due_at)
    assert snapshot["metrics"]["devices"]["disk"]["throughput"] is None
    assert snapshot["metrics"]["devices"]["disk"]["utilization"] == 100
    json.dumps(snapshot, allow_nan=False)
    # A later, representable denominator recovers a normal rate without mutation.
    assert manager.metrics(1)["devices"]["disk"]["throughput"] == 1


def test_large_finite_service_keeps_representable_metrics() -> None:
    """Large valid durations are supported rather than capped arbitrarily."""
    manager = DeviceManager([config(kind=DeviceType.KEYBOARD, service=1e308)])
    manager.submit_request("P", spec(None), 0)
    operation = manager.schedule_io("disk", 0)
    assert manager.metrics(5e307)["devices"]["disk"]["busy_time"] == 5e307
    finish(manager, "disk", operation)
    snapshot = manager.snapshot(1e308)
    metric = snapshot["metrics"]["devices"]["disk"]
    assert metric["busy_time"] == 1e308
    assert metric["utilization"] == 100
    assert metric["throughput"] == 1e-308
    json.dumps(snapshot, allow_nan=False)


def test_overflowed_queue_area_retains_finite_average_and_read_only_snapshots() -> None:
    """An overflowing integral/sum must not poison a representable average."""
    manager = DeviceManager([config(kind=DeviceType.KEYBOARD, service=0)])
    for index in range(3):
        manager.submit_request(f"P{index}", spec(None), 0)
    before = deepcopy(manager.__dict__)
    partial = manager.snapshot(1e308)
    metric = partial["metrics"]["devices"]["disk"]
    assert metric["queue_area"] is None
    assert metric["average_queue_length"] == 3
    assert manager.__dict__ == before
    json.dumps(partial, allow_nan=False)
    manager.fail_device("disk", 1e308, "stopped")
    metric = manager.metrics(1e308)["devices"]["disk"]
    assert metric["average_waiting_time"] == 1e308
    assert metric["failed"] == 3
    json.dumps(manager.snapshot(1e308), allow_nan=False)
    manager.validate()


def test_positive_duration_cannot_disappear_at_large_timestamp() -> None:
    """Reject precision loss that would turn a positive operation into zero time."""
    manager = DeviceManager([config(kind=DeviceType.KEYBOARD, service=1)],
                            timestamp=1e308)
    before = manager.snapshot(1e308)
    with pytest.raises(ValueError, match="precision"):
        manager.submit_request("P", spec(None), 1e308)
    assert manager.snapshot(1e308) == before


def test_notifications_are_immutable_and_snapshot_events_share_schema() -> None:
    """Interrupt reports are immutable and history uses existing common fields."""
    manager = DeviceManager([config()], scenario_id="phase3")
    key = manager.submit_request("P", spec(), 0)
    notification, = manager.cancel_request(key, 1)
    with pytest.raises(FrozenInstanceError):
        notification.pid = "other"
    fields = {"timestamp", "manager", "event_type", "entity_id", "action",
              "previous_state", "new_state", "outcome", "duration", "message",
              "scenario_id"}
    for event in manager.snapshot(1)["events"]:
        assert fields <= event.keys()
        assert event["scenario_id"] == "phase3"


def test_queued_failure_wait_and_partial_seek_are_not_service() -> None:
    """Failure includes queue waiting; aborted seeks commit no physical movement."""
    manager = DeviceManager([config()])
    active = manager.submit_request("P1", spec(), 0)
    queued = manager.submit_request("P2", spec(70), 0)
    manager.schedule_io("disk", 0)
    manager.fail_device("disk", 3, "failed")
    metrics = manager.metrics(5)
    device = metrics["devices"]["disk"]
    assert device["busy_time"] == device["request_seek_time"] == 3
    assert device["unavailable_time"] == 2
    assert device["head_movement"] == device["service_time"] == 0
    assert device["average_waiting_time"] == 1.5
    assert device["average_queue_length"] == 0.6
    assert metrics["requests"][queued]["queue_waiting_time"] == 3
    assert metrics["requests"][queued]["turnaround_time"] == 3
    assert metrics["requests"][active]["seek_time"] == 3
    assert manager.cancel_request(queued, 5) == ()
    assert manager.requests[queued].state == RequestState.FAILED


def test_endpoint_time_is_separate_and_reset_can_start_at_nonzero_epoch() -> None:
    """Denominators use the explicit run epoch and endpoint time has no owner."""
    manager = DeviceManager([config(SchedulingPolicy.SCAN)], timestamp=10)
    key = manager.submit_request("P", spec(20), 10)
    manager.schedule_io("disk", 10)
    metrics = manager.metrics(15)
    assert metrics["elapsed_time"] == 5
    assert metrics["devices"]["disk"]["endpoint_time"] == 5
    assert metrics["requests"][key]["seek_time"] == 0
    assert metrics["requests"][key]["service_time"] == 0
    manager.reset(100)
    assert manager.metrics(100)["elapsed_time"] == 0
    assert manager.snapshot(100)["timeline"] == []


@pytest.mark.parametrize("action", ["cancel", "fail", "recover", "offline", "online"])
def test_invalid_lifecycle_actions_do_not_mutate(action: str) -> None:
    """Unknown IDs, empty reasons, and invalid transitions are atomic failures."""
    manager = DeviceManager([config()])
    before = manager.snapshot(0)
    with pytest.raises(ValueError):
        if action == "cancel":
            manager.cancel_request("missing", 0)
        elif action == "fail":
            manager.fail_device("disk", 0, " ")
        elif action == "recover":
            manager.recover_device("disk", 0)
        elif action == "offline":
            manager.set_offline("unknown", 0)
        else:
            manager.set_online("disk", 0)
    assert manager.snapshot(0) == before


def test_busy_schedule_does_not_issue_duplicate_instructions() -> None:
    """Scheduling is idempotent while an operation is already committed."""
    manager = DeviceManager([config()])
    manager.submit_request("P", spec(), 0)
    operation = manager.schedule_io("disk", 0)
    for _ in range(5):
        assert manager.schedule_io("disk", 0) is None
    assert manager.devices["disk"].active_operation == operation
    assert len(manager.snapshot(0)["timeline"]) == 1


def test_scan_free_endpoint_reversal_and_current_head_service() -> None:
    """Service at a bound precedes an immediate zero-cost direction change."""
    configuration = config(SchedulingPolicy.SCAN)
    manager = DeviceManager([replace(configuration, disk=replace(
        configuration.disk, initial_head=99,
    ))])
    current = manager.submit_request("P1", spec(99), 0)
    other = manager.submit_request("P2", spec(20), 0)
    service = manager.schedule_io("disk", 0)
    assert service.kind == OperationKind.SERVICE
    assert service.request_id == current
    finish(manager, "disk", service)
    seek = manager.schedule_io("disk", 2)
    assert seek.request_id == other
    assert manager.devices["disk"].direction == ScanDirection.DOWN
    assert seek.due_at == 81
