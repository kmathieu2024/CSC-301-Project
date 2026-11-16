"""Device data contracts; these models never advance simulation time.

Movement commits only at completed seek boundaries. SCAN travels to the physical
endpoint before reversing when pending work remains behind the head, and stops
when its queue is empty. Committed movement is not interrupted by new arrivals.
Failure/cancellation aborts movement without committing partial distance; elapsed
busy time still counts. Service uses the resolved duration, excluding seek.
Requests are ordered by submission time then sequence for FCFS; SSTF adds distance
first. SCAN serves the current cylinder first, then the nearest cylinder in
its direction; equal-cylinder requests use submission time then sequence.
Endpoint travel counts as busy time and head movement, not request service time.
DeviceManager owns lifecycle execution; SimulationEngine owns clock advancement
and process blocking/resumption.
"""

from dataclasses import dataclass, field
from enum import Enum


class DeviceType(Enum):
    """Supported simulated peripherals."""

    DISK = "DISK"
    PRINTER = "PRINTER"
    KEYBOARD = "KEYBOARD"


class SchedulingPolicy(Enum):
    """Device queue policies, independent of CPU scheduling."""

    FCFS = "FCFS"
    SSTF = "SSTF"
    SCAN = "SCAN"


class ScanDirection(Enum):
    """Direction of increasing or decreasing disk cylinders."""

    UP = "UP"
    DOWN = "DOWN"


class DeviceState(Enum):
    """Availability and activity of a device."""

    IDLE = "IDLE"
    BUSY = "BUSY"
    OFFLINE = "OFFLINE"
    FAILED = "FAILED"


class RequestState(Enum):
    """Request lifecycle, with three irreversible terminal outcomes."""

    QUEUED = "QUEUED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class OperationKind(Enum):
    """Timed device activity, including request-independent endpoint travel."""

    REQUEST_SEEK = "REQUEST_SEEK"
    SERVICE = "SERVICE"
    SCAN_ENDPOINT = "SCAN_ENDPOINT"


@dataclass(frozen=True, kw_only=True)
class DiskGeometry:
    """Immutable disk bounds and initial state; units are simulation units."""

    min_cylinder: int
    max_cylinder: int
    initial_head: int
    initial_direction: ScanDirection
    seek_time_per_cylinder: float


@dataclass(frozen=True, kw_only=True)
class DeviceConfig:
    """Immutable input; validate before registration or runtime creation."""

    device_id: str
    device_type: DeviceType
    policy: SchedulingPolicy
    default_service_time: float
    disk: DiskGeometry | None = None


@dataclass(frozen=True, kw_only=True)
class DeviceRequestSpec:
    """Workload input: exact uppercase operation; None selects default service."""

    device_id: str
    operation: str
    cylinder: int | None = None
    service_time: float | None = None


@dataclass(kw_only=True)
class DeviceRequest:
    """Manager-owned request history; finish time applies to every terminal state.

    start_time begins the request seek (or service when no seek is needed).
    resolved_service_time excludes seek. generation distinguishes reset runs.
    """

    request_id: str
    generation: int
    sequence: int
    pid: str
    spec: DeviceRequestSpec
    submitted_at: float
    resolved_service_time: float
    state: RequestState = RequestState.QUEUED
    start_time: float | None = None
    finish_time: float | None = None
    error: str | None = None


@dataclass(frozen=True, kw_only=True)
class DeviceOperation:
    """Committed timed activity; tokens identify callbacks within a generation.

    due_at must equal started_at plus modeled seek/service duration. A service
    has no cylinder endpoints; seek operations carry both endpoints.
    """

    token: str
    generation: int
    kind: OperationKind
    started_at: float
    due_at: float
    request_id: str | None = None
    source_cylinder: int | None = None
    target_cylinder: int | None = None


@dataclass(kw_only=True)
class DeviceRuntime:
    """Mutable state, separate from config; queues contain request IDs only.

    Submission to OFFLINE/FAILED is rejected without creating a request record.
    Failure drains requests to FAILED and invalidates active callbacks. Recovery
    retains committed head/direction; going offline requires an empty idle device.
    Cancellation is terminal and aborts an active operation without partial seek.
    Cancelling queued work does not interrupt committed SCAN endpoint movement.
    Reset creates fresh state, clears history/counters, restores initial geometry,
    restarts request sequences, and increments generation without notifications.
    Cancelling unknown IDs raises ValueError; cancelling terminal requests is an
    idempotent no-op. Each accepted request gets one terminal notification. Stale
    callbacks are rejected without mutation using generation and operation token.
    """

    config: DeviceConfig
    state: DeviceState = DeviceState.IDLE
    queue: list[str] = field(default_factory=list)
    active_request_id: str | None = None
    head_position: int | None = None
    direction: ScanDirection | None = None
    active_operation: DeviceOperation | None = None
    generation: int = 0
    failure_reason: str | None = None


def create_device_runtime(config: DeviceConfig) -> DeviceRuntime:
    """Validate configuration and create independent, initially idle state.

    DeviceManager owns reset generations; this factory starts generation zero.
    """
    from .validation import validate_device_config

    validate_device_config(config)
    return DeviceRuntime(
        config=config,
        head_position=config.disk.initial_head if config.disk else None,
        direction=config.disk.initial_direction if config.disk else None,
    )
