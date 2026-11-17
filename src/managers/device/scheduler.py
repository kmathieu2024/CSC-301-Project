"""Pure device scheduling; DeviceManager executes with caller-owned time.

Call select_next at an idle operation boundary. BUSY devices return IDLE (meaning
no new work), never replacement work. Requests arriving during committed movement
wait until its boundary. A request at a passed cylinder is not intercepted; SCAN
may reach it on its return sweep. SSTF can starve distant requests under a steady
stream of nearer arrivals; no aging is applied.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from .models import (
    DeviceRequest, DeviceRuntime, DeviceState, ScanDirection, SchedulingPolicy,
)
from .validation import validate_runtime_state


class DeviceDecisionKind(Enum):
    """Request selection, independent endpoint movement, or no new work."""

    SERVICE = "SERVICE"
    MOVE_TO_ENDPOINT = "MOVE_TO_ENDPOINT"
    IDLE = "IDLE"


@dataclass(frozen=True, kw_only=True)
class DeviceSchedulingDecision:
    """Value-only plan, without changing request records or reserving work.

    SERVICE selects a whole request: seek_duration precedes service_duration.
    Phase 3 creates REQUEST_SEEK then SERVICE operations, skipping zero-distance
    seek if appropriate. MOVE_TO_ENDPOINT creates only SCAN_ENDPOINT; it has no
    request or service charge. IDLE means do not start anything, including while
    an existing operation continues. No decision advances time.

    direction applies when starting the plan; direction_after applies at its
    completed boundary. Endpoint travel reverses only after reaching the bound.
    At a physical bound an outward direction can reverse immediately as part of
    SERVICE selection, without a movement event or time charge. Non-disk plans
    have no cylinder/direction. Idle plans carry no movement/direction updates.

    Decisions are ephemeral: apply against the same validated snapshot, or
    reselect after queue changes. Phase 3 owns generation/token checks, timing,
    metrics, and atomic commitment; this object is not an execution callback.
    """

    kind: DeviceDecisionKind
    request_id: str | None = None
    target_cylinder: int | None = None
    direction: ScanDirection | None = None
    direction_after: ScanDirection | None = None
    seek_distance: int = 0
    seek_duration: float = 0
    service_duration: float = 0
    reason: str = ""


def _reverse(direction: ScanDirection) -> ScanDirection:
    return ScanDirection.DOWN if direction == ScanDirection.UP else ScanDirection.UP


def _arrival_key(request: DeviceRequest) -> tuple[float, int]:
    return request.submitted_at, request.sequence


def select_next(
    runtime: DeviceRuntime, requests: Mapping[str, DeviceRequest],
) -> DeviceSchedulingDecision:
    """Validate a snapshot and select deterministic, non-preemptive device work.

    Raises ValueError for malformed runtime/configuration or request registries,
    including missing records and duplicate queue entries or device sequences.
    Registry records for other devices are allowed per Phase 1 validation.
    FCFS orders by submission time then sequence; SSTF adds distance first;
    SCAN adds distance within its current direction, serving the head first.
    No input, queue order, clock, or runtime state is modified.
    """
    validate_runtime_state(runtime, requests)
    if runtime.state != DeviceState.IDLE:
        return DeviceSchedulingDecision(
            kind=DeviceDecisionKind.IDLE, reason=runtime.state.value.lower(),
        )
    if not runtime.queue:
        return DeviceSchedulingDecision(kind=DeviceDecisionKind.IDLE, reason="empty")

    queued = [requests[request_id] for request_id in runtime.queue]
    policy = runtime.config.policy
    head = runtime.head_position
    direction = runtime.direction
    if policy == SchedulingPolicy.FCFS:
        selected = min(queued, key=_arrival_key)
    elif policy == SchedulingPolicy.SSTF:
        selected = min(queued, key=lambda r: (
            abs(r.spec.cylinder - head), *_arrival_key(r),
        ))
    else:  # SCAN; Phase 1 validation rejects other policies/non-disk geometry.
        at_head = [r for r in queued if r.spec.cylinder == head]
        if at_head:
            selected = min(at_head, key=_arrival_key)
        else:
            disk = runtime.config.disk
            endpoint = (
                disk.max_cylinder if direction == ScanDirection.UP
                else disk.min_cylinder
            )
            if head == endpoint:
                direction = _reverse(direction)
            eligible = [r for r in queued if (
                r.spec.cylinder > head if direction == ScanDirection.UP
                else r.spec.cylinder < head
            )]
            if not eligible:
                distance = abs(endpoint - head)
                return DeviceSchedulingDecision(
                    kind=DeviceDecisionKind.MOVE_TO_ENDPOINT,
                    target_cylinder=endpoint, direction=direction,
                    direction_after=_reverse(direction), seek_distance=distance,
                    seek_duration=distance * disk.seek_time_per_cylinder,
                )
            selected = min(eligible, key=lambda r: (
                abs(r.spec.cylinder - head), *_arrival_key(r),
            ))

    distance = abs(selected.spec.cylinder - head) if runtime.config.disk else 0
    return DeviceSchedulingDecision(
        kind=DeviceDecisionKind.SERVICE, request_id=selected.request_id,
        target_cylinder=selected.spec.cylinder, direction=direction,
        direction_after=direction, seek_distance=distance,
        seek_duration=(distance * runtime.config.disk.seek_time_per_cylinder
                       if runtime.config.disk else 0),
        service_duration=selected.resolved_service_time,
    )
