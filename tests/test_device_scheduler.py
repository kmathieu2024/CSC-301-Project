"""Independent scheduling tests; the harness is not a production DeviceManager."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace

import pytest

from src.managers.device import (
    DeviceConfig, DeviceDecisionKind, DeviceOperation, DeviceRequest,
    DeviceRequestSpec, DeviceSchedulingDecision, DeviceState, DeviceType,
    DiskGeometry, OperationKind, RequestState, ScanDirection, SchedulingPolicy,
    create_device_runtime, select_next, validate_runtime_state,
)


class BoundaryHarness:
    """Apply plans at seek/service boundaries using the Phase 1 operation model."""

    def __init__(
        self, policy: SchedulingPolicy = SchedulingPolicy.SCAN,
        *, direction: ScanDirection = ScanDirection.UP, head: int = 50,
        minimum: int = 0, maximum: int = 99, seek_time: float = 1,
        service_time: float = 2, device_type: DeviceType = DeviceType.DISK,
    ) -> None:
        self.runtime = create_device_runtime(DeviceConfig(
            device_id="d0", device_type=device_type, policy=policy,
            default_service_time=service_time,
            disk=DiskGeometry(
                min_cylinder=minimum, max_cylinder=maximum, initial_head=head,
                initial_direction=direction, seek_time_per_cylinder=seek_time,
            ) if device_type == DeviceType.DISK else None,
        ))
        self.requests: dict[str, DeviceRequest] = {}
        self.now = 0
        self.movement = 0
        self.busy_time = 0
        self.path: list[int] = []
        self.order: list[str] = []
        self.decisions: list[DeviceSchedulingDecision] = []
        self.pending_direction: ScanDirection | None = None

    def enqueue(
        self, cylinder: int | None = None, *, submitted: float | None = None,
        sequence: int | None = None,
    ) -> str:
        """Add an arrival, including during a committed operation."""
        request_id = f"r{len(self.requests)}"
        operation = (
            "PRINT" if self.runtime.config.device_type == DeviceType.PRINTER
            else "READ"
        )
        request = DeviceRequest(
            request_id=request_id, generation=self.runtime.generation,
            sequence=len(self.requests) if sequence is None else sequence,
            pid=f"P{len(self.requests)}", spec=DeviceRequestSpec(
                device_id="d0", operation=operation, cylinder=cylinder,
            ), submitted_at=self.now if submitted is None else submitted,
            resolved_service_time=self.runtime.config.default_service_time,
        )
        self.requests[request_id] = request
        self.runtime.queue.append(request_id)
        return request_id

    def choose(self) -> DeviceSchedulingDecision:
        """Select without applying work."""
        return select_next(self.runtime, self.requests)

    def begin(self) -> DeviceSchedulingDecision:
        """Commit a decision, leaving its first timed operation active."""
        decision = self.choose()
        self.decisions.append(decision)
        if decision.kind == DeviceDecisionKind.IDLE:
            return decision
        self.runtime.state = DeviceState.BUSY
        self.runtime.direction = decision.direction
        self.pending_direction = decision.direction_after
        if decision.kind == DeviceDecisionKind.MOVE_TO_ENDPOINT:
            kind = OperationKind.SCAN_ENDPOINT
        else:
            request = self.requests[decision.request_id]
            self.runtime.queue.remove(request.request_id)
            self.runtime.active_request_id = request.request_id
            request.state = RequestState.ACTIVE
            request.start_time = self.now
            kind = (
                OperationKind.REQUEST_SEEK if decision.seek_distance
                else OperationKind.SERVICE
            )
        is_service = kind == OperationKind.SERVICE
        self.runtime.active_operation = DeviceOperation(
            token=f"o{len(self.decisions)}", generation=self.runtime.generation,
            kind=kind, request_id=self.runtime.active_request_id,
            started_at=self.now, due_at=self.now + (
                decision.service_duration if is_service else decision.seek_duration
            ), source_cylinder=None if is_service else self.runtime.head_position,
            target_cylinder=None if is_service else decision.target_cylinder,
        )
        validate_runtime_state(self.runtime, self.requests)
        return decision

    def finish_boundary(self) -> None:
        """Finish exactly one operation; a request seek proceeds to its service."""
        operation = self.runtime.active_operation
        assert operation is not None
        self.now = operation.due_at
        self.busy_time += operation.due_at - operation.started_at
        if operation.kind != OperationKind.SERVICE:
            self.movement += abs(operation.target_cylinder - operation.source_cylinder)
            self.runtime.head_position = operation.target_cylinder
            self.path.append(operation.target_cylinder)
        if operation.kind == OperationKind.REQUEST_SEEK:
            request = self.requests[operation.request_id]
            self.runtime.active_operation = DeviceOperation(
                token=operation.token + "s", generation=self.runtime.generation,
                kind=OperationKind.SERVICE, request_id=request.request_id,
                started_at=self.now, due_at=self.now + request.resolved_service_time,
            )
        else:
            if operation.kind == OperationKind.SERVICE:
                request = self.requests[operation.request_id]
                request.state = RequestState.COMPLETED
                request.finish_time = self.now
                self.order.append(request.request_id)
            self.runtime.state = DeviceState.IDLE
            self.runtime.active_request_id = None
            self.runtime.active_operation = None
            self.runtime.direction = self.pending_direction
        validate_runtime_state(self.runtime, self.requests)

    def drain(self) -> None:
        """Run finite fixture work to idle, bounded to catch zero-time loops."""
        for _ in range(100):
            if self.runtime.state == DeviceState.BUSY:
                self.finish_boundary()
            elif self.begin().kind == DeviceDecisionKind.IDLE:
                return
        raise AssertionError("Harness did not reach idle")


@pytest.mark.parametrize("policy,order,path,movement", [
    (SchedulingPolicy.FCFS, [60, 20, 70], [60, 20, 70], 100),
    (SchedulingPolicy.SSTF, [60, 70, 20], [60, 70, 20], 70),
    (SchedulingPolicy.SCAN, [60, 70, 20], [60, 70, 99, 20], 128),
])
def test_reference_fixture(
    policy: SchedulingPolicy, order: list[int], path: list[int], movement: int,
) -> None:
    """Assert complete physical paths, service order, and nonduplicated costs."""
    harness = BoundaryHarness(policy)
    for cylinder in (60, 20, 70):
        harness.enqueue(cylinder)
    harness.drain()
    assert [harness.requests[r].spec.cylinder for r in harness.order] == order
    assert harness.path == path
    assert harness.movement == movement
    assert harness.busy_time == movement + 6
    assert sum(d.seek_distance for d in harness.decisions) == movement
    assert sum(d.service_duration for d in harness.decisions) == 6


@pytest.mark.parametrize("policy", list(SchedulingPolicy))
def test_empty_and_single_request(policy: SchedulingPolicy) -> None:
    """Empty queues stop movement; a request can require a SCAN endpoint first."""
    harness = BoundaryHarness(policy)
    decision = harness.choose()
    assert decision.kind == DeviceDecisionKind.IDLE
    assert decision.seek_distance == decision.seek_duration == 0
    assert decision.direction is None
    request_id = harness.enqueue(20)
    harness.drain()
    assert harness.order == [request_id]
    assert harness.movement == (128 if policy == SchedulingPolicy.SCAN else 30)


@pytest.mark.parametrize("policy", list(SchedulingPolicy))
def test_same_cylinder_submission_and_sequence_ties(policy: SchedulingPolicy) -> None:
    """Queue list order and request ID spelling are not scheduling tie-breakers."""
    harness = BoundaryHarness(policy)
    later = harness.enqueue(60, submitted=5, sequence=0)
    equal_later_sequence = harness.enqueue(60, submitted=1, sequence=9)
    first = harness.enqueue(60, submitted=1, sequence=3)
    harness.now = 5
    harness.runtime.queue.reverse()
    harness.drain()
    assert harness.order == [first, equal_later_sequence, later]
    assert harness.movement == 10


def test_fcfs_uses_arrival_before_distance() -> None:
    """FCFS chooses older distant work and still charges disk seeking."""
    harness = BoundaryHarness(SchedulingPolicy.FCFS, seek_time=0.5)
    harness.enqueue(51, submitted=2)
    older = harness.enqueue(20, submitted=1)
    decision = harness.choose()
    assert decision.request_id == older
    assert decision.seek_distance == 30
    assert decision.seek_duration == 15
    assert decision.service_duration == 2


def test_sstf_distance_then_submission_then_sequence() -> None:
    """Equidistant targets use arrival time and then sequence."""
    harness = BoundaryHarness(SchedulingPolicy.SSTF)
    harness.enqueue(49, submitted=9)
    harness.enqueue(51, submitted=3, sequence=8)
    first = harness.enqueue(49, submitted=3, sequence=2)
    harness.enqueue(40, submitted=0)
    assert harness.choose().request_id == first


@pytest.mark.parametrize("policy", [SchedulingPolicy.SSTF, SchedulingPolicy.SCAN])
@pytest.mark.parametrize("direction", list(ScanDirection))
def test_current_head_has_zero_movement(
    policy: SchedulingPolicy, direction: ScanDirection,
) -> None:
    """The head cylinder wins over either side, even against older work."""
    harness = BoundaryHarness(policy, direction=direction)
    harness.enqueue(49)
    harness.enqueue(51)
    current = harness.enqueue(50)
    decision = harness.choose()
    assert decision.request_id == current
    assert decision.seek_distance == decision.seek_duration == 0


def test_scan_down_path_and_inclusive_nonzero_bounds() -> None:
    """DOWN reaches the physical minimum rather than reversing at last work."""
    harness = BoundaryHarness(direction=ScanDirection.DOWN, minimum=10, maximum=90)
    for cylinder in (40, 80, 30):
        harness.enqueue(cylinder)
    harness.drain()
    assert harness.path == [40, 30, 10, 80]
    assert harness.movement == 110


@pytest.mark.parametrize("head,direction,target,new_direction", [
    (99, ScanDirection.UP, 20, ScanDirection.DOWN),
    (0, ScanDirection.DOWN, 80, ScanDirection.UP),
])
def test_reversal_at_endpoint_is_free(
    head: int, direction: ScanDirection, target: int, new_direction: ScanDirection,
) -> None:
    """No zero-distance endpoint event is needed when already at the bound."""
    harness = BoundaryHarness(head=head, direction=direction)
    harness.enqueue(target)
    decision = harness.choose()
    assert decision.kind == DeviceDecisionKind.SERVICE
    assert decision.direction == decision.direction_after == new_direction
    assert harness.runtime.direction == direction
    assert decision.seek_duration == abs(target - head)


def test_current_endpoint_request_precedes_free_reversal() -> None:
    """Serve the endpoint itself before choosing work in the opposite direction."""
    harness = BoundaryHarness(head=99)
    harness.enqueue(20)
    endpoint = harness.enqueue(99)
    assert harness.begin().request_id == endpoint
    harness.finish_boundary()
    decision = harness.choose()
    assert decision.direction == ScanDirection.DOWN
    assert decision.seek_distance == 79


@pytest.mark.parametrize("policy", list(SchedulingPolicy))
def test_arrival_during_committed_seek_and_service(policy: SchedulingPolicy) -> None:
    """New nearer work cannot replace a selected request in either phase."""
    harness = BoundaryHarness(policy)
    active = harness.enqueue(70)
    harness.begin()
    harness.now = 5
    passed = harness.enqueue(55)
    assert harness.choose().kind == DeviceDecisionKind.IDLE
    assert harness.runtime.active_request_id == active
    harness.finish_boundary()
    assert harness.runtime.head_position == 70
    assert harness.runtime.active_operation.kind == OperationKind.SERVICE
    harness.enqueue(70)
    assert harness.choose().kind == DeviceDecisionKind.IDLE
    harness.finish_boundary()
    assert harness.order == [active]
    harness.drain()
    assert passed in harness.order
    if policy == SchedulingPolicy.SCAN:
        assert harness.path == [70, 99, 55]


def test_arrival_during_endpoint_movement_waits_for_boundary() -> None:
    """Passed-cylinder requests wait for the return sweep after endpoint travel."""
    harness = BoundaryHarness()
    behind = harness.enqueue(20)
    decision = harness.begin()
    assert decision.kind == DeviceDecisionKind.MOVE_TO_ENDPOINT
    assert decision.request_id is None
    assert decision.service_duration == 0
    harness.now = 10
    arrived = harness.enqueue(60)
    assert harness.choose().kind == DeviceDecisionKind.IDLE
    harness.finish_boundary()
    assert harness.runtime.head_position == 99
    assert harness.runtime.direction == ScanDirection.DOWN
    assert harness.choose().request_id == arrived
    harness.drain()
    assert harness.order == [arrived, behind]
    assert harness.path == [99, 60, 20]
    assert harness.movement == 128


def test_cancel_queue_between_decisions_and_during_endpoint() -> None:
    """Decisions reserve nothing; committed endpoint travel survives cancellation."""
    harness = BoundaryHarness()
    cancelled = harness.enqueue(60)
    assert harness.choose().request_id == cancelled
    request = harness.requests[cancelled]
    request.state = RequestState.CANCELLED
    request.finish_time = 0
    harness.runtime.queue.remove(cancelled)
    assert harness.choose().kind == DeviceDecisionKind.IDLE
    behind = harness.enqueue(20)
    harness.begin()
    harness.requests[behind].state = RequestState.CANCELLED
    harness.requests[behind].finish_time = 0
    harness.runtime.queue.remove(behind)
    assert harness.choose().kind == DeviceDecisionKind.IDLE
    harness.finish_boundary()
    assert harness.path == [99]
    assert harness.choose().kind == DeviceDecisionKind.IDLE


@pytest.mark.parametrize("policy", list(SchedulingPolicy))
def test_zero_seek_time_and_zero_service_terminate(policy: SchedulingPolicy) -> None:
    """Physical distance is retained even when movement has zero time cost."""
    harness = BoundaryHarness(policy, seek_time=0, service_time=0)
    for cylinder in (50, 60, 20, 70):
        harness.enqueue(cylinder)
    harness.drain()
    assert len(harness.order) == 4
    assert harness.busy_time == 0
    assert harness.movement > 0


@pytest.mark.parametrize("kind", [DeviceType.PRINTER, DeviceType.KEYBOARD])
def test_non_disks_fcfs(kind: DeviceType) -> None:
    """Peripheral FCFS has service costs without fake disk movement."""
    harness = BoundaryHarness(SchedulingPolicy.FCFS, device_type=kind)
    later = harness.enqueue(submitted=2)
    first = harness.enqueue(submitted=1)
    harness.now = 2
    decision = harness.choose()
    assert decision.request_id == first
    assert decision.target_cylinder is None
    assert decision.direction is None
    assert decision.seek_distance == decision.seek_duration == 0
    harness.drain()
    assert harness.order == [first, later]
    assert harness.path == []
    assert harness.busy_time == 4


@pytest.mark.parametrize("kind", [DeviceType.PRINTER, DeviceType.KEYBOARD])
@pytest.mark.parametrize("policy", [SchedulingPolicy.SSTF, SchedulingPolicy.SCAN])
def test_invalid_device_policy_combinations(
    kind: DeviceType, policy: SchedulingPolicy,
) -> None:
    """Validation applies before selection, even for empty queues."""
    harness = BoundaryHarness(SchedulingPolicy.FCFS, device_type=kind)
    harness.runtime.config = replace(harness.runtime.config, policy=policy)
    with pytest.raises(ValueError, match="FCFS"):
        harness.choose()


@pytest.mark.parametrize("problem,match", [
    ("missing", "unknown/foreign"), ("duplicate_queue", "duplicate"),
    ("wrong_key", "Registry key"), ("foreign", "unknown/foreign"),
    ("duplicate_sequence", "Duplicate request sequence"),
    ("terminal_queued", "QUEUED"), ("stale", "stale"),
    ("policy", "policy"), ("state", "state"), ("busy", "DeviceOperation"),
    ("head", "bounds"), ("direction", "direction"), ("record", "DeviceRequest"),
])
def test_malformed_snapshots_raise_clear_errors(problem: str, match: str) -> None:
    """Reuse Phase 1 validation for queue, configuration, and lifecycle errors."""
    harness = BoundaryHarness()
    request_id = harness.enqueue(60)
    if problem == "missing":
        harness.requests.clear()
    elif problem == "duplicate_queue":
        harness.runtime.queue.append(request_id)
    elif problem == "wrong_key":
        harness.requests["other"] = harness.requests.pop(request_id)
    elif problem == "foreign":
        request = harness.requests[request_id]
        request.spec = replace(request.spec, device_id="other")
    elif problem == "duplicate_sequence":
        harness.enqueue(70, sequence=0)
    elif problem == "terminal_queued":
        harness.requests[request_id].state = RequestState.CANCELLED
        harness.requests[request_id].finish_time = 0
    elif problem == "stale":
        harness.requests[request_id].generation = 1
    elif problem == "policy":
        harness.runtime.config = replace(harness.runtime.config, policy="LOOK")
    elif problem == "state":
        harness.runtime.state = "IDLE"
    elif problem == "busy":
        harness.runtime.state = DeviceState.BUSY
    elif problem == "head":
        harness.runtime.head_position = 100
    elif problem == "direction":
        harness.runtime.direction = "UP"
    else:
        harness.requests[request_id] = None
    with pytest.raises(ValueError, match=match):
        harness.choose()


@pytest.mark.parametrize("state", [DeviceState.OFFLINE, DeviceState.FAILED])
def test_unavailable_devices_do_not_schedule(state: DeviceState) -> None:
    """A valid unavailable snapshot returns no work without changing its state."""
    harness = BoundaryHarness()
    harness.runtime.state = state
    if state == DeviceState.FAILED:
        harness.runtime.failure_reason = "hardware failure"
    assert harness.choose().kind == DeviceDecisionKind.IDLE
    assert harness.runtime.state == state


@pytest.mark.parametrize("policy", list(SchedulingPolicy))
def test_selection_is_pure_immutable_and_repeatable(policy: SchedulingPolicy) -> None:
    """Repeated calls and independent replays return equal value decisions."""
    harness = BoundaryHarness(policy)
    for cylinder in (60, 20, 70):
        harness.enqueue(cylinder)
    before = deepcopy((harness.runtime, harness.requests, harness.now))
    decision = harness.choose()
    for _ in range(3):
        assert harness.choose() == decision
    assert (harness.runtime, harness.requests, harness.now) == before
    with pytest.raises(FrozenInstanceError):
        decision.request_id = "different"
    replay = deepcopy(harness)
    harness.drain()
    replay.drain()
    assert harness.decisions == replay.decisions
    assert harness.path == replay.path


def test_busy_selection_is_also_pure() -> None:
    """No-work decisions leave active operations and queued arrivals intact."""
    harness = BoundaryHarness()
    harness.enqueue(70)
    harness.begin()
    harness.enqueue(55)
    before = deepcopy((harness.runtime, harness.requests))
    decision = harness.choose()
    assert decision.kind == DeviceDecisionKind.IDLE
    assert decision.reason == "busy"
    assert (harness.runtime, harness.requests) == before
