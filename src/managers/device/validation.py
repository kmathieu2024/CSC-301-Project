"""Pure validation of device inputs and stable runtime snapshots.

Invalid contracts raise ValueError before caller mutation. Constructors are data
containers: contextual validation is explicit, while the runtime factory always
validates its configuration. No coercion or wall-clock access occurs here.
"""

import math
from collections.abc import Mapping, Sequence

from .models import (
    DeviceConfig, DeviceOperation, DeviceRequest, DeviceRequestSpec, DeviceRuntime,
    DeviceState, DeviceType, DiskGeometry, OperationKind, RequestState,
    ScanDirection, SchedulingPolicy,
)


def _text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{field} must be nonempty text without outer whitespace")


def _integer(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")


def _time(value: object, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite nonnegative number")
    try:
        valid = value >= 0 and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{field} must be a finite nonnegative number")


def _cylinder(value: object, geometry: DiskGeometry, field: str) -> None:
    _integer(value, field)
    if not geometry.min_cylinder <= value <= geometry.max_cylinder:
        raise ValueError(f"{field} must be within disk bounds")


def validate_device_config(config: DeviceConfig) -> None:
    """Validate identity, policy, timing, and device-specific disk geometry."""
    if not isinstance(config, DeviceConfig):
        raise ValueError("config must be DeviceConfig")
    _text(config.device_id, "device_id")
    if not isinstance(config.device_type, DeviceType):
        raise ValueError("device_type must be DeviceType")
    if not isinstance(config.policy, SchedulingPolicy):
        raise ValueError("policy must be SchedulingPolicy")
    _time(config.default_service_time, "default_service_time")
    if config.device_type != DeviceType.DISK:
        if config.disk is not None or config.policy != SchedulingPolicy.FCFS:
            raise ValueError("Printer/keyboard require FCFS and prohibit disk geometry")
        return
    if not isinstance(config.disk, DiskGeometry):
        raise ValueError("Disk requires DiskGeometry")
    disk = config.disk
    _integer(disk.min_cylinder, "min_cylinder")
    _integer(disk.max_cylinder, "max_cylinder")
    if disk.min_cylinder >= disk.max_cylinder:
        raise ValueError("min_cylinder must be less than max_cylinder")
    _cylinder(disk.initial_head, disk, "initial_head")
    if not isinstance(disk.initial_direction, ScanDirection):
        raise ValueError("initial_direction must be ScanDirection")
    _time(disk.seek_time_per_cylinder, "seek_time_per_cylinder")
    try:
        maximum_seek = (
            disk.max_cylinder - disk.min_cylinder
        ) * disk.seek_time_per_cylinder
    except OverflowError as error:
        raise ValueError("Derived seek duration must be finite") from error
    _time(maximum_seek, "maximum seek duration")


def validate_device_configs(
    configs: Sequence[DeviceConfig],
) -> dict[str, DeviceConfig]:
    """Validate an entire configuration sequence and return its unique registry."""
    if not isinstance(configs, Sequence) or isinstance(configs, (str, bytes)):
        raise ValueError("configs must be a sequence")
    registry = {}
    for config in configs:
        validate_device_config(config)
        if config.device_id in registry:
            raise ValueError(f"Duplicate device_id: {config.device_id}")
        registry[config.device_id] = config
    return registry


def validate_request_spec(spec: DeviceRequestSpec, config: DeviceConfig) -> None:
    """Validate Disk READ/WRITE, Printer PRINT, or Keyboard READ requests."""
    validate_device_config(config)
    if not isinstance(spec, DeviceRequestSpec):
        raise ValueError("spec must be DeviceRequestSpec")
    _text(spec.device_id, "device_id")
    if spec.device_id != config.device_id:
        raise ValueError("Request device_id must match configuration")
    allowed = {
        DeviceType.DISK: ("READ", "WRITE"),
        DeviceType.PRINTER: ("PRINT",),
        DeviceType.KEYBOARD: ("READ",),
    }
    if not isinstance(spec.operation, str) or spec.operation not in allowed[
        config.device_type
    ]:
        raise ValueError("operation is unsupported for device_type")
    if config.disk is not None:
        _cylinder(spec.cylinder, config.disk, "cylinder")
    elif spec.cylinder is not None:
        raise ValueError("Printer/keyboard requests prohibit cylinder")
    if spec.service_time is not None:
        _time(spec.service_time, "service_time")


def validate_request_offsets(
    requests: Mapping[int, DeviceRequestSpec],
    burst_time: int | float,
    configs: Mapping[str, DeviceConfig],
) -> None:
    """Validate CPU offsets and all device references without modifying inputs."""
    _time(burst_time, "burst_time")
    if burst_time == 0:
        raise ValueError("burst_time must be positive")
    if not isinstance(requests, Mapping) or not isinstance(configs, Mapping):
        raise ValueError("requests and configs must be mappings")
    for device_id, config in configs.items():
        validate_device_config(config)
        if device_id != config.device_id:
            raise ValueError("Registry key must match device_id")
    for offset, spec in requests.items():
        _integer(offset, "offset")
        if offset >= burst_time:
            raise ValueError("offset must be less than burst_time")
        if not isinstance(spec, DeviceRequestSpec):
            raise ValueError("spec must be DeviceRequestSpec")
        _text(spec.device_id, "device_id")
        if spec.device_id not in configs:
            raise ValueError(f"Unknown device_id: {spec.device_id}")
        validate_request_spec(spec, configs[spec.device_id])


def _validate_request(request: DeviceRequest, config: DeviceConfig) -> None:
    _text(request.request_id, "request_id")
    _text(request.pid, "pid")
    _integer(request.generation, "generation")
    _integer(request.sequence, "sequence")
    validate_request_spec(request.spec, config)
    _time(request.submitted_at, "submitted_at")
    _time(request.resolved_service_time, "resolved_service_time")
    expected = (
        config.default_service_time if request.spec.service_time is None
        else request.spec.service_time
    )
    if request.resolved_service_time != expected:
        raise ValueError("resolved_service_time must match request/default")
    if not isinstance(request.state, RequestState):
        raise ValueError("state must be RequestState")
    if request.start_time is not None:
        _time(request.start_time, "start_time")
        if request.start_time < request.submitted_at:
            raise ValueError("start_time precedes submission")
    terminal = request.state in (
        RequestState.COMPLETED, RequestState.FAILED, RequestState.CANCELLED,
    )
    if terminal:
        _time(request.finish_time, "finish_time")
        earliest = (
            request.submitted_at if request.start_time is None else request.start_time
        )
        if request.finish_time < earliest:
            raise ValueError("finish_time precedes submission/start")
    elif request.finish_time is not None:
        raise ValueError("Nonterminal requests prohibit finish_time")
    if request.state == RequestState.QUEUED and request.start_time is not None:
        raise ValueError("Queued requests prohibit start_time")
    if request.state in (RequestState.ACTIVE, RequestState.COMPLETED):
        if request.start_time is None:
            raise ValueError("Active/completed requests require start_time")
    if request.state == RequestState.FAILED:
        _text(request.error, "error")
    elif request.error is not None:
        raise ValueError("Only failed requests carry error details")


def _validate_operation(
    runtime: DeviceRuntime, request: DeviceRequest | None,
) -> None:
    operation = runtime.active_operation
    if not isinstance(operation, DeviceOperation):
        raise ValueError("BUSY device requires DeviceOperation")
    _text(operation.token, "token")
    _integer(operation.generation, "operation generation")
    if operation.generation != runtime.generation:
        raise ValueError("Operation generation is stale")
    if not isinstance(operation.kind, OperationKind):
        raise ValueError("kind must be OperationKind")
    _time(operation.started_at, "started_at")
    _time(operation.due_at, "due_at")
    if operation.due_at < operation.started_at:
        raise ValueError("due_at precedes started_at")
    if operation.request_id != runtime.active_request_id:
        raise ValueError("Operation request_id must match active_request_id")
    if request is not None and operation.started_at < request.start_time:
        raise ValueError("Operation precedes request start")
    if operation.kind == OperationKind.SERVICE:
        if request is None:
            raise ValueError("Service requires an active request")
        if (operation.source_cylinder is not None
                or operation.target_cylinder is not None):
            raise ValueError("Service prohibits cylinder endpoints")
        if runtime.config.disk and runtime.head_position != request.spec.cylinder:
            raise ValueError("Service head must be at request cylinder")
        duration = request.resolved_service_time
    else:
        disk = runtime.config.disk
        if disk is None:
            raise ValueError("Seek requires a disk")
        _cylinder(operation.source_cylinder, disk, "source_cylinder")
        _cylinder(operation.target_cylinder, disk, "target_cylinder")
        if operation.source_cylinder != runtime.head_position:
            raise ValueError("Seek source must match committed head")
        if operation.kind == OperationKind.REQUEST_SEEK:
            if request is None or operation.target_cylinder != request.spec.cylinder:
                raise ValueError("Request seek must target its active request")
        else:
            if runtime.config.policy != SchedulingPolicy.SCAN or request is not None:
                raise ValueError("Endpoint seek requires SCAN and no active request")
            endpoint = (
                disk.max_cylinder if runtime.direction == ScanDirection.UP
                else disk.min_cylinder
            )
            if operation.target_cylinder != endpoint:
                raise ValueError("Endpoint must match SCAN direction")
            if operation.source_cylinder == endpoint:
                raise ValueError("Reverse at endpoint without another endpoint seek")
        duration = abs(
            operation.target_cylinder - operation.source_cylinder
        ) * disk.seek_time_per_cylinder
    _time(duration, "derived duration")
    try:
        expected_due = operation.started_at + duration
    except OverflowError as error:
        raise ValueError("Derived due_at must be finite") from error
    _time(expected_due, "derived due_at")
    if operation.due_at != expected_due:
        raise ValueError("due_at must equal started_at plus modeled duration")


def validate_runtime_state(
    runtime: DeviceRuntime, requests: Mapping[str, DeviceRequest],
) -> None:
    """Validate one device against a global request registry at a stable boundary.

    Foreign-device records are ignored after identity/type checks. Validate each
    registered device to check the whole system. Transition history, callback
    delivery, and scheduling policy execution belong to DeviceManager.
    """
    if not isinstance(runtime, DeviceRuntime) or not isinstance(requests, Mapping):
        raise ValueError("Expected DeviceRuntime and request mapping")
    validate_device_config(runtime.config)
    if not isinstance(runtime.state, DeviceState):
        raise ValueError("state must be DeviceState")
    _integer(runtime.generation, "generation")
    if runtime.config.disk is not None:
        _cylinder(runtime.head_position, runtime.config.disk, "head_position")
        if not isinstance(runtime.direction, ScanDirection):
            raise ValueError("direction must be ScanDirection")
    elif runtime.head_position is not None or runtime.direction is not None:
        raise ValueError("Printer/keyboard prohibit head position and direction")
    if runtime.state == DeviceState.FAILED:
        _text(runtime.failure_reason, "failure_reason")
    elif runtime.failure_reason is not None:
        raise ValueError("Only FAILED device carries failure_reason")
    if not isinstance(runtime.queue, list):
        raise ValueError("queue must be a list of request IDs")
    for request_id in runtime.queue:
        _text(request_id, "queued request_id")
    if len(set(runtime.queue)) != len(runtime.queue):
        raise ValueError("Queue contains duplicate request IDs")
    if runtime.active_request_id is not None:
        _text(runtime.active_request_id, "active_request_id")
        if runtime.active_request_id in runtime.queue:
            raise ValueError("Active request must not be queued")
    owned = {}
    sequences = set()
    for request_id, request in requests.items():
        if not isinstance(request, DeviceRequest):
            raise ValueError("Registry values must be DeviceRequest")
        if request_id != request.request_id:
            raise ValueError("Registry key must match request_id")
        if not isinstance(request.spec, DeviceRequestSpec):
            raise ValueError("spec must be DeviceRequestSpec")
        if request.spec.device_id != runtime.config.device_id:
            continue
        _validate_request(request, runtime.config)
        if request.generation != runtime.generation:
            raise ValueError("Request generation is stale")
        if request.sequence in sequences:
            raise ValueError("Duplicate request sequence on device")
        sequences.add(request.sequence)
        owned[request_id] = request
        if (request.state == RequestState.QUEUED) != (request_id in runtime.queue):
            raise ValueError("QUEUED state must match queue membership")
        if (request.state == RequestState.ACTIVE) != (
            request_id == runtime.active_request_id
        ):
            raise ValueError("ACTIVE state must match active_request_id")
    if any(request_id not in owned for request_id in runtime.queue):
        raise ValueError("Queue references unknown/foreign request")
    if runtime.active_request_id is not None and runtime.active_request_id not in owned:
        raise ValueError("Active request is unknown/foreign")
    if runtime.state in (DeviceState.OFFLINE, DeviceState.FAILED) and runtime.queue:
        raise ValueError("Unavailable device must have an empty queue")
    if runtime.state == DeviceState.BUSY:
        _validate_operation(runtime, owned.get(runtime.active_request_id))
    elif runtime.active_operation is not None or runtime.active_request_id is not None:
        raise ValueError("Nonbusy device prohibits active operation/request")
