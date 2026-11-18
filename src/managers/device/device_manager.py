"""Standalone, caller-timed device execution; no global clock or Process access.

The caller submits arrivals, schedules idle devices, and delivers operation tokens
at their exact due_at times. Methods never drive an event loop. Equal-time actions
are processed in caller order. A seek completion continues the reserved request's
service; other pending requests require an explicit schedule_io call after service
or endpoint completion. Zero-time work therefore uses finite, explicit callbacks.
"""

import math
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from fractions import Fraction
from operator import add, mul, truediv
from typing import Any, Callable

from .models import (
    DeviceConfig, DeviceOperation, DeviceRequest, DeviceRequestSpec, DeviceRuntime,
    DeviceState, OperationKind, RequestState, ScanDirection, create_device_runtime,
)
from .scheduler import DeviceDecisionKind, select_next
from .validation import (
    validate_device_configs, validate_request_spec, validate_runtime_state,
)


@dataclass(frozen=True, kw_only=True)
class DeviceNotification:
    """One terminal interrupt report; delivery to a process belongs to the caller."""

    request_id: str
    pid: str
    device_id: str
    timestamp: float
    outcome: RequestState
    reason: str | None = None


@dataclass(frozen=True, kw_only=True)
class OperationResult:
    """A seek may start service; a service completion reports one notification."""

    next_operation: DeviceOperation | None = None
    notifications: tuple[DeviceNotification, ...] = ()


def _number(value: object, name: str) -> None:
    try:
        valid = (not isinstance(value, bool) and isinstance(value, (int, float))
                 and value >= 0 and math.isfinite(value))
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{name} must be finite and nonnegative")


def _text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be nonempty text without outer whitespace")


def _derived(operation: Callable[[Any, Any], Any], left: Any, right: Any) -> Any:
    """Preserve native arithmetic normally; retain exact exceptional intermediates."""
    if not isinstance(left, Fraction) and not isinstance(right, Fraction):
        try:
            value = operation(left, right)
            if math.isfinite(value) and (value != 0 or left == 0 or right == 0):
                return value
        except OverflowError:
            pass
    return operation(Fraction(left), Fraction(right))


def _total(values: list[Any]) -> Any:
    """Keep ordinary sum behavior; avoid losing a representable average to overflow."""
    if not any(isinstance(value, Fraction) for value in values):
        try:
            value = sum(values)
            if math.isfinite(value):
                return value
        except OverflowError:
            pass
    return sum((Fraction(value) for value in values), Fraction(0))


def _due_at(timestamp: float, duration: float) -> float:
    """Reject an unrepresentable operation boundary rather than imposing a time cap."""
    try:
        due = timestamp + duration
        _number(due, "operation due_at")
    except OverflowError as error:
        raise ValueError("Operation due_at must be finite") from error
    if duration > 0 and due <= timestamp:
        raise ValueError("Positive duration cannot advance timestamp at this precision")
    return due


def _json(value: Any) -> Any:
    if isinstance(value, Fraction):
        try:
            number = float(value)
        except OverflowError:
            return None
        return None if number == 0 and value != 0 else number
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _json(asdict(value))
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json(item) for item in value]
    return value


class DeviceManager:
    """Own device execution and history with explicit simulated timestamps.

    Mutation timestamps are globally nondecreasing within a run, as required of
    a caller-owned chronological event stream. Reads project state through their
    timestamp without mutation. Neither may pass an outstanding operation's due
    time. Reset starts a new run at any finite nonnegative timestamp.

    Returned runtime/request properties are defensive copies. Configuration is
    frozen. All invalid public inputs are rejected before mutation. Configuration
    replacement is allowed only at the pristine run epoch (or after reset).
    """

    def __init__(
        self, configs: Sequence[DeviceConfig] = (), *, timestamp: float = 0,
        scenario_id: str = "default",
    ) -> None:
        _number(timestamp, "timestamp")
        _text(scenario_id, "scenario_id")
        registry = validate_device_configs(configs)
        self.scenario_id = scenario_id
        self._configs = registry
        self._generation = 0
        self._initialize(timestamp)

    @property
    def devices(self) -> dict[str, DeviceRuntime]:
        """Return independent runtime copies for inspection/validation."""
        return deepcopy(self._devices)

    @property
    def requests(self) -> dict[str, DeviceRequest]:
        """Return independent request history copies."""
        return deepcopy(self._requests)

    def _initialize(self, timestamp: float) -> None:
        self._epoch = self._last_time = timestamp
        self._devices = {
            key: create_device_runtime(config) for key, config in self._configs.items()
        }
        for runtime in self._devices.values():
            runtime.generation = self._generation
        self._requests: dict[str, DeviceRequest] = {}
        self._stats = {
            key: {"busy_time": 0, "idle_time": 0, "unavailable_time": 0,
                  "queue_area": 0, "max_queue_length": 0, "head_movement": 0,
                  "request_seek_time": 0, "service_time": 0, "endpoint_time": 0}
            for key in self._devices
        }
        self._costs: dict[str, dict[str, Any]] = {}
        self._events: list[dict[str, Any]] = []
        self._timeline: list[dict[str, Any]] = []
        self._notifications: list[DeviceNotification] = []
        self._request_sequence = self._operation_sequence = 0

    def configure(
        self, configs: Sequence[DeviceConfig], timestamp: float,
    ) -> None:
        """Replace the full registry only in a pristine, idle run at its epoch."""
        registry = validate_device_configs(configs)
        self._check_time(timestamp)
        if (timestamp != self._epoch or self._requests or self._events
                or any(r.state != DeviceState.IDLE for r in self._devices.values())):
            raise ValueError("configure requires a pristine run; reset first")
        self._configs = registry
        self._generation += 1
        self._initialize(timestamp)

    def _device(self, device_id: str) -> DeviceRuntime:
        _text(device_id, "device_id")
        if device_id not in self._devices:
            raise ValueError(f"Unknown device_id: {device_id}")
        return self._devices[device_id]

    def _request(self, request_id: str) -> DeviceRequest:
        _text(request_id, "request_id")
        if request_id not in self._requests:
            raise ValueError(f"Unknown request_id: {request_id}")
        return self._requests[request_id]

    def _check_time(self, timestamp: float) -> None:
        _number(timestamp, "timestamp")
        if timestamp < self._last_time:
            raise ValueError("timestamp precedes the last mutation")
        if any(r.active_operation and timestamp > r.active_operation.due_at
               for r in self._devices.values()):
            raise ValueError(
                "Deliver outstanding operation completion before timestamp"
            )

    def _project(
        self, timestamp: float,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        stats, costs = deepcopy(self._stats), deepcopy(self._costs)
        elapsed = timestamp - self._last_time
        for key, runtime in self._devices.items():
            stats[key]["queue_area"] = _derived(
                add, stats[key]["queue_area"], _derived(mul, elapsed, len(runtime.queue)),
            )
            category = ("busy_time" if runtime.state == DeviceState.BUSY else
                        "idle_time" if runtime.state == DeviceState.IDLE else
                        "unavailable_time")
            stats[key][category] = _derived(add, stats[key][category], elapsed)
            operation = runtime.active_operation
            if operation:
                operation_field = {
                    OperationKind.REQUEST_SEEK: "request_seek_time",
                    OperationKind.SERVICE: "service_time",
                    OperationKind.SCAN_ENDPOINT: "endpoint_time",
                }[operation.kind]
                stats[key][operation_field] = _derived(
                    add, stats[key][operation_field], elapsed,
                )
            if operation and operation.request_id is not None:
                field = ("service_time" if operation.kind == OperationKind.SERVICE
                         else "seek_time")
                costs[operation.request_id][field] = _derived(
                    add, costs[operation.request_id][field], elapsed,
                )
        for values in (*stats.values(), *costs.values()):
            for name, value in values.items():
                if name not in ("reason", "service_start_time") and not isinstance(
                    value, Fraction,
                ):
                    _number(value, name)
        return stats, costs

    def _touch(self, timestamp: float) -> None:
        self._stats, self._costs = self._project(timestamp)
        self._last_time = timestamp

    def _event(
        self, runtime: DeviceRuntime, timestamp: float, event_type: str,
        old: str, new: str, *, request: DeviceRequest | None = None,
        outcome: str = "success", duration: float = 0, message: str = "",
    ) -> None:
        self._events.append({
            "timestamp": timestamp, "manager": "device", "event_type": event_type,
            "entity_id": request.pid if request else runtime.config.device_id,
            "device_id": runtime.config.device_id,
            "request_id": request.request_id if request else None,
            "action": event_type, "previous_state": old, "new_state": new,
            "outcome": outcome, "duration": duration, "message": message,
            "scenario_id": self.scenario_id,
        })

    def submit_request(
        self, pid: str, spec: DeviceRequestSpec, timestamp: float,
        *, request_id: str | None = None,
    ) -> str:
        """Validate then enqueue; optional caller IDs must be unique in this run."""
        _text(pid, "pid")
        if not isinstance(spec, DeviceRequestSpec):
            raise ValueError("spec must be DeviceRequestSpec")
        runtime = self._device(spec.device_id)
        validate_request_spec(spec, runtime.config)
        self._check_time(timestamp)
        if runtime.state in (DeviceState.OFFLINE, DeviceState.FAILED):
            raise ValueError("Cannot submit to an offline/failed device")
        sequence = self._request_sequence
        if request_id is None:
            request_id = f"request-{sequence}"
            while request_id in self._requests:
                sequence += 1
                request_id = f"request-{sequence}"
        _text(request_id, "request_id")
        if request_id in self._requests:
            raise ValueError(f"Duplicate request_id: {request_id}")
        service_time = (runtime.config.default_service_time
                        if spec.service_time is None else spec.service_time)
        # Even the earliest possible finish must be representable before enqueue.
        _due_at(timestamp, service_time)
        request = DeviceRequest(
            request_id=request_id, generation=self._generation, sequence=sequence,
            pid=pid, spec=spec, submitted_at=timestamp,
            resolved_service_time=service_time,
        )
        previous_sequence = self._request_sequence
        previous_accounting = self._stats, self._costs, self._last_time
        event_count = len(self._events)
        try:
            self._touch(timestamp)
            self._request_sequence = sequence + 1
            self._requests[request_id] = request
            self._costs[request_id] = {
                "seek_time": 0, "service_time": 0, "service_start_time": None,
                "reason": None,
            }
            runtime.queue.append(request_id)
            stats = self._stats[spec.device_id]
            stats["max_queue_length"] = max(
                stats["max_queue_length"], len(runtime.queue),
            )
            self._event(runtime, timestamp, "device_request", "", "QUEUED",
                        request=request)
        except Exception as error:
            # _touch projects into new dictionaries. Restore the old accounting
            # references and remove only this synchronous registration's entries.
            try:
                self._requests.pop(request_id, None)
                if request_id in runtime.queue:
                    runtime.queue.remove(request_id)
                self._request_sequence = previous_sequence
                self._stats, self._costs, self._last_time = previous_accounting
                del self._events[event_count:]
            except Exception as cleanup_error:
                error.add_note("Submission rollback failed: " + str(cleanup_error))
            raise
        return request_id

    def _operation(
        self, runtime: DeviceRuntime, kind: OperationKind, timestamp: float,
        duration: float, *, target: int | None = None,
    ) -> DeviceOperation:
        operation = DeviceOperation(
            token=f"g{self._generation}:operation-{self._operation_sequence}",
            generation=self._generation, kind=kind, started_at=timestamp,
            due_at=_due_at(timestamp, duration), request_id=runtime.active_request_id,
            source_cylinder=(runtime.head_position
                             if kind != OperationKind.SERVICE else None),
            target_cylinder=target,
        )
        self._operation_sequence += 1
        runtime.active_operation = operation
        runtime.state = DeviceState.BUSY
        request = self._requests.get(operation.request_id)
        if kind == OperationKind.SERVICE:
            self._costs[operation.request_id]["service_start_time"] = timestamp
        self._event(runtime, timestamp, kind.value.lower(), "IDLE", "BUSY",
                    request=request, duration=duration)
        return operation

    def schedule_io(self, device_id: str, timestamp: float) -> DeviceOperation | None:
        """Apply select_next to one device; return its first completion instruction.

        Repeated calls while busy return None, not a duplicate callback. The caller
        schedules devices in its chosen deterministic order for equal-time work.
        """
        runtime = self._device(device_id)
        self._check_time(timestamp)
        decision = select_next(runtime, self._requests)
        if decision.kind != DeviceDecisionKind.IDLE:
            due = _due_at(timestamp, decision.seek_duration)
            _due_at(due, decision.service_duration)
        self._touch(timestamp)
        if decision.kind == DeviceDecisionKind.IDLE:
            return None
        runtime.direction = decision.direction
        if decision.kind == DeviceDecisionKind.MOVE_TO_ENDPOINT:
            return self._operation(
                runtime, OperationKind.SCAN_ENDPOINT, timestamp,
                decision.seek_duration, target=decision.target_cylinder,
            )
        request = self._requests[decision.request_id]
        runtime.queue.remove(request.request_id)
        runtime.active_request_id = request.request_id
        request.state = RequestState.ACTIVE
        request.start_time = timestamp
        self._event(runtime, timestamp, "device_start", "QUEUED", "ACTIVE",
                    request=request)
        if decision.seek_distance:
            return self._operation(
                runtime, OperationKind.REQUEST_SEEK, timestamp,
                decision.seek_duration, target=decision.target_cylinder,
            )
        return self._operation(runtime, OperationKind.SERVICE, timestamp,
                               decision.service_duration)

    def _end_operation(
        self, runtime: DeviceRuntime, timestamp: float, *, completed: bool,
    ) -> None:
        operation = runtime.active_operation
        movement = 0
        if completed and operation.kind != OperationKind.SERVICE:
            movement = abs(operation.target_cylinder - operation.source_cylinder)
            runtime.head_position = operation.target_cylinder
            self._stats[runtime.config.device_id]["head_movement"] += movement
        self._timeline.append({
            **_json(operation), "device_id": runtime.config.device_id,
            "end": timestamp, "status": "completed" if completed else "aborted",
            "head_movement": movement,
        })
        request = self._requests.get(operation.request_id)
        self._event(runtime, timestamp, "operation_complete" if completed
                    else "operation_abort", "BUSY", "IDLE", request=request,
                    duration=timestamp - operation.started_at)
        runtime.active_operation = None
        runtime.state = DeviceState.IDLE

    def _terminal(
        self, request: DeviceRequest, state: RequestState, timestamp: float,
        reason: str | None = None,
    ) -> DeviceNotification:
        previous = request.state.value
        request.state = state
        request.finish_time = timestamp
        request.error = reason if state == RequestState.FAILED else None
        self._costs[request.request_id]["reason"] = reason
        notification = DeviceNotification(
            request_id=request.request_id, pid=request.pid,
            device_id=request.spec.device_id, timestamp=timestamp,
            outcome=state, reason=reason,
        )
        self._notifications.append(notification)
        runtime = self._devices[request.spec.device_id]
        outcome = ("success" if state == RequestState.COMPLETED else
                   "error" if state == RequestState.FAILED else "cancelled")
        self._event(runtime, timestamp, "device_" + state.value.lower(),
                    previous, state.value, request=request, outcome=outcome,
                    message=reason or "")
        self._event(runtime, timestamp, "interrupt", state.value, state.value,
                    request=request, outcome=outcome, message=reason or "")
        return notification

    def complete_operation(
        self, device_id: str, token: str, generation: int, timestamp: float,
    ) -> OperationResult:
        """Consume exactly one valid callback at due_at; reject duplicates/stale work.

        Request seek completion returns the reserved service operation. Service
        completion releases the device and returns one terminal notification.
        Endpoint completion releases the device and reverses direction. Call
        schedule_io explicitly to select pending work after release.
        """
        runtime = self._device(device_id)
        _text(token, "token")
        if isinstance(generation, bool) or not isinstance(generation, int):
            raise ValueError("generation must be an integer")
        operation = runtime.active_operation
        if (operation is None or token != operation.token
                or generation != self._generation):
            raise ValueError("Stale, duplicate, or mismatched operation callback")
        self._check_time(timestamp)
        if timestamp != operation.due_at:
            raise ValueError("Completion timestamp must equal operation due_at")
        self._touch(timestamp)
        self._end_operation(runtime, timestamp, completed=True)
        if operation.kind == OperationKind.REQUEST_SEEK:
            request = self._requests[operation.request_id]
            return OperationResult(next_operation=self._operation(
                runtime, OperationKind.SERVICE, timestamp,
                request.resolved_service_time,
            ))
        if operation.kind == OperationKind.SCAN_ENDPOINT:
            runtime.direction = (ScanDirection.DOWN
                                 if runtime.direction == ScanDirection.UP
                                 else ScanDirection.UP)
            return OperationResult()
        request = self._requests[operation.request_id]
        runtime.active_request_id = None
        return OperationResult(notifications=(self._terminal(
            request, RequestState.COMPLETED, timestamp,
        ),))

    def cancel_request(
        self, request_id: str, timestamp: float, reason: str = "cancelled",
    ) -> tuple[DeviceNotification, ...]:
        """Cancel queued/active work; terminal requests keep their original outcome."""
        request = self._request(request_id)
        _text(reason, "reason")
        self._check_time(timestamp)
        if request.finish_time is not None:
            return ()
        self._touch(timestamp)
        runtime = self._devices[request.spec.device_id]
        abort_error = None
        if request.state == RequestState.QUEUED:
            runtime.queue.remove(request_id)
        else:
            try:
                if runtime.active_operation is not None:
                    self._end_operation(runtime, timestamp, completed=False)
            except Exception as error:
                abort_error = error
            finally:
                # Invalidate the callback even if abort-history recording fails.
                runtime.active_operation = None
                runtime.active_request_id = None
                runtime.state = DeviceState.IDLE
        try:
            notification = self._terminal(
                request, RequestState.CANCELLED, timestamp, reason,
            )
        except Exception as error:
            if abort_error is None:
                raise
            abort_error.add_note("Cancellation history failed: " + str(error))
        if abort_error is not None:
            raise abort_error
        return (notification,)

    def fail_device(
        self, device_id: str, timestamp: float, reason: str,
    ) -> tuple[DeviceNotification, ...]:
        """Abort active work and fail active then queued requests in queue order."""
        runtime = self._device(device_id)
        _text(reason, "reason")
        self._check_time(timestamp)
        if runtime.state == DeviceState.FAILED:
            raise ValueError("Device is already failed")
        self._touch(timestamp)
        previous = runtime.state.value
        affected = ([runtime.active_request_id] if runtime.active_request_id else [])
        affected += runtime.queue
        errors = []
        try:
            if runtime.active_operation is not None:
                self._end_operation(runtime, timestamp, completed=False)
        except Exception as error:
            errors.append(error)
        finally:
            runtime.active_operation = None
            runtime.active_request_id = None
            runtime.queue = []
            runtime.state = DeviceState.FAILED
            runtime.failure_reason = reason
        notifications = []
        for key in affected:
            try:
                notifications.append(self._terminal(
                    self._requests[key], RequestState.FAILED, timestamp, reason,
                ))
            except Exception as error:
                # History failures must not leave later queue entries live.
                errors.append(error)
        try:
            self._event(runtime, timestamp, "device_failure", previous, "FAILED",
                        outcome="error", message=reason)
        except Exception as error:
            errors.append(error)
        if errors:
            for error in errors[1:]:
                errors[0].add_note("Device failure history failed: " + str(error))
            raise errors[0]
        return tuple(notifications)

    def _availability(
        self, device_id: str, timestamp: float, expected: DeviceState,
        target: DeviceState,
    ) -> None:
        runtime = self._device(device_id)
        self._check_time(timestamp)
        if runtime.state != expected or runtime.queue or runtime.active_operation:
            raise ValueError(
                f"Availability change requires empty {expected.value} device"
            )
        self._touch(timestamp)
        runtime.state = target
        runtime.failure_reason = None
        self._event(runtime, timestamp, "device_" + target.value.lower(),
                    expected.value, target.value)

    def recover_device(self, device_id: str, timestamp: float) -> None:
        """Explicitly recover FAILED to IDLE without retries or head movement."""
        self._availability(device_id, timestamp, DeviceState.FAILED, DeviceState.IDLE)

    def set_offline(self, device_id: str, timestamp: float) -> None:
        """Take only an empty IDLE device offline."""
        self._availability(device_id, timestamp, DeviceState.IDLE, DeviceState.OFFLINE)

    def set_online(self, device_id: str, timestamp: float) -> None:
        """Restore an administratively OFFLINE device to IDLE."""
        self._availability(device_id, timestamp, DeviceState.OFFLINE, DeviceState.IDLE)

    def reset(self, timestamp: float = 0) -> None:
        """Discard history silently; restore initial geometry with a new generation."""
        _number(timestamp, "timestamp")
        self._generation += 1
        self._initialize(timestamp)

    def metrics(self, timestamp: float) -> dict[str, Any]:
        """Project counters without mutation; rates use time since run epoch.

        Busy/idle/unavailable partition elapsed time. Utilization is a percentage.
        Throughput counts successful completions only. Average waiting uses all
        terminal requests, including queued failures/cancellations. Queue area is
        the integral of pending count; endpoint travel is busy but not request cost.
        Request seek/service costs are elapsed work, including aborted work.
        Exceptional derived intermediates use exact rational arithmetic. A final
        nonzero quantity outside the finite float range is JSON null (overflow or
        underflow), never a fabricated zero. Ordinary arithmetic is unchanged.
        """
        self._check_time(timestamp)
        stats, costs = self._project(timestamp)
        elapsed = timestamp - self._epoch
        request_metrics = {}
        for key, request in self._requests.items():
            end = timestamp if request.finish_time is None else request.finish_time
            wait_end = end if request.start_time is None else request.start_time
            request_metrics[key] = {
                **costs[key], "submitted_at": request.submitted_at,
                "start_time": request.start_time, "finish_time": request.finish_time,
                "queue_waiting_time": wait_end - request.submitted_at,
                "turnaround_time": end - request.submitted_at,
                "outcome": request.state.value,
            }
        devices = {}
        for key, runtime in self._devices.items():
            owned = [r for r in self._requests.values() if r.spec.device_id == key]
            terminal = [r for r in owned if r.finish_time is not None]
            completed = sum(r.state == RequestState.COMPLETED for r in owned)
            devices[key] = {
                **stats[key], "submitted": len(owned), "completed": completed,
                "failed": sum(r.state == RequestState.FAILED for r in owned),
                "cancelled": sum(r.state == RequestState.CANCELLED for r in owned),
                "pending": len(runtime.queue),
                "active": int(runtime.active_request_id is not None),
                "current_queue_length": len(runtime.queue),
                "average_queue_length": _derived(
                    truediv, stats[key]["queue_area"], elapsed,
                )
                if elapsed else 0,
                "utilization": _derived(mul, _derived(
                    truediv, stats[key]["busy_time"], elapsed,
                ), 100)
                if elapsed else 0,
                "throughput": _derived(truediv, completed, elapsed) if elapsed else 0,
                "average_waiting_time": _derived(truediv, _total([
                    request_metrics[r.request_id]["queue_waiting_time"]
                    for r in terminal
                ]), len(terminal)) if terminal else 0,
                "interrupt_count": len(terminal),
            }
        return _json({
            "elapsed_time": elapsed, "devices": devices, "requests": request_metrics,
        })

    def snapshot(self, timestamp: float) -> dict[str, Any]:
        """Return detached JSON-safe configuration, state, metrics, and histories."""
        metrics = self.metrics(timestamp)
        active = [{
            **_json(runtime.active_operation), "device_id": key,
            "end": timestamp, "status": "active", "head_movement": 0,
        } for key, runtime in self._devices.items() if runtime.active_operation]
        return {
            "timestamp": timestamp, "configs": _json(self._configs),
            "devices": _json(self._devices), "requests": _json(self._requests),
            "metrics": metrics, "events": deepcopy(self._events),
            "timeline": deepcopy(self._timeline) + active,
            "notifications": _json(self._notifications),
        }

    def validate(self) -> None:
        """Check all stable runtime invariants using Phase 1 validation."""
        for runtime in self._devices.values():
            validate_runtime_state(runtime, self._requests)
