
import random
import math
from collections.abc import Sequence
from typing import Any
from urllib.parse import quote

from src.core.process import Process, ProcessState
from src.managers.device import DeviceManager, DeviceOperation, validate_request_offsets
from src.managers.memory.memory_manager import NoFrameAvailable
from src.managers.file import FileManager
from src.managers.process.scheduler import Scheduler, validate_processes
from src.managers.network import (
    NetworkManager, NetworkOperation, NetworkUpdate, NetworkTopologyEvent,
    TopologyAction, packet_identifier, validate_topology_events,
    validate_request_offsets as validate_network_offsets,
    validate_request_spec as validate_network_spec,
)


class SimulationExecutionError(RuntimeError):
    """Execution failed; reset is required before attempting Step or Run again."""


class SimulationEngine:
    # Controls simulation time, process arrivals, and event history.
    # Scheduler decides which process uses the CPU.

    def __init__(self, processes, algorithm="fcfs", quantum=4,
             aging_interval=4, context_switch=0,
             scenario_id="default", seed=0, memory_manager=None,
             file_manager=None, device_manager: DeviceManager | None = None,
             security_manager=None, network_manager: NetworkManager | None = None,
             network_events: Sequence[NetworkTopologyEvent] = ()):

        self.processes = list(processes)
        validate_processes(self.processes)

        if isinstance(context_switch, bool) or not isinstance(context_switch, (int, float)):
            raise ValueError("Context-switch time must be a number")

        if context_switch < 0:
            raise ValueError("Context-switch time cannot be negative")

        for process in self.processes:
            for offset, duration in process.blocking_requests.items():
                if (isinstance(offset, bool) or not isinstance(offset, (int, float))
                        or not math.isfinite(offset) or not 0 <= offset < process.burst_time
                        or isinstance(duration, bool) or not isinstance(duration, (int, float))
                        or not math.isfinite(duration) or duration <= 0):
                    raise ValueError("Invalid blocking request for process " + process.pid)

        self.algorithm = algorithm.lower()
        self.quantum = quantum
        self.aging_interval = aging_interval
        self.context_switch = context_switch
        self.scenario_id = scenario_id
        self.seed = seed
        self.memory_manager = memory_manager
        self.file_manager = file_manager if file_manager is not None else FileManager()
        self.security_manager = security_manager
        if self.security_manager is not None:
            # FileManager already delegates protected operations through this
            # interface, so the policy stays centralized in SecurityManager.
            self.file_manager.authorizer = self.security_manager
        self.device_manager = device_manager
        if device_manager is None:
            if any(p.device_requests for p in self.processes):
                raise ValueError("Device requests require a device manager")
        else:
            configs = {key: r.config for key, r in device_manager.devices.items()}
            for process in self.processes:
                validate_request_offsets(
                    process.device_requests, process.burst_time, configs,
                )
        self.network_manager = network_manager
        if network_manager is None:
            if network_events:
                raise ValueError("Network events require a network manager")
            if not isinstance(network_events, (list, tuple)):
                raise ValueError("network events must be a list or tuple")
            self.network_events = ()
            if any(p.network_requests for p in self.processes):
                raise ValueError("Network requests require a network manager")
        else:
            config = network_manager.runtime.config
            self.network_events = validate_topology_events(network_events, config)
            for process in self.processes:
                validate_network_offsets(
                    process.network_requests, process.burst_time, config,
                )

        for process in self.processes:
            for offset, command in process.file_requests.items():
                if (
                  isinstance(offset, bool)
                 or not isinstance(offset, int)
                 or not 0 <= offset < process.burst_time
                 or not isinstance(command, str)
                 or not command.strip()
                ):
                    raise ValueError(
                      "Invalid file request for process " + process.pid
                    )

        for process in self.processes:
            for offset, address in process.memory_references.items():
                if (isinstance(offset, bool) or not isinstance(offset, int) or
                        offset < 0 or offset >= process.burst_time or
                        isinstance(address, bool) or not isinstance(address, int) or address < 0):
                    raise ValueError("Invalid memory reference for process " + process.pid)
        if memory_manager is None and any(p.memory_references for p in self.processes):
            raise ValueError("Memory references require a memory manager")

        self.reset()

    def reset(self):
        # Reset simulation progress while preserving the file system.

        for process in self.processes:
            process.reset()

        self.scheduler = Scheduler(
            self.algorithm,
            quantum=self.quantum,
            aging_interval=self.aging_interval
        )

        if self.memory_manager is not None:
            self.memory_manager.reset()
        self._memory_done = set()
        self._memory_pending = set()
        self._frame_waiters = []
        self.random = random.Random(self.seed)

        self.current_time = 0
        self.events = []
        self.timeline = []
        self.event_queue = []
        self.completed = 0
        self.last_pid = None
        self.next_event_id = 0
        self._runner = None
        self._execution_error: dict[str, Any] | None = None
        self._execution_exception: Exception | None = None
        self._blocks_done = set()
        self._files_done = set()
        if self.security_manager is not None:
            # Audit history survives reset. Only new events enter this run's
            # unified event stream.
            self._security_event_cursor = len(self.security_manager.security_events)
            self.security_manager.set_context(0, self.scenario_id)
        else:
            self._security_event_cursor = 0
        self._device_submitted: dict[tuple[str, float], str] = {}
        self._device_waiting: dict[str, tuple[Process, float]] = {}
        self._device_outcomes: dict[str, dict[str, Any]] = {}
        self._device_submission_id: str | None = None
        self._device_event_cursor = self._device_notification_cursor = 0
        if self.device_manager is not None:
            self.device_manager.reset(timestamp=0)
        self._network_submitted: dict[tuple[str, float], str | None] = {}
        self._network_waiting: dict[str, tuple[Process, float]] = {}
        self._network_outcomes: dict[str, dict[str, Any]] = {}
        self._network_submission_id: str | None = None
        self._network_event_cursor = 0
        if self.network_manager is not None:
            self.network_manager.reset(now=0)

        # Add aarrivals to the event queue.

        for process in self.processes:
            self.schedule_event(
                process.arrival_time,
                "arrival",
                process
            )
        for event in self.network_events:
            self.schedule_event(event.timestamp, "network_topology", event)

    def schedule_event(self, time, event_type, process):
        # Store upcoming events in chronological order.
        # Process events and device callbacks share the same queue.

        if time < self.current_time:
            raise ValueError("Cannot schedule an event in the past")

        if event_type not in (
            "arrival", "unblock", "device_complete", "network_complete",
            "network_topology",
        ):
            raise ValueError("Unsupported event type: " + event_type)

        # Sort by time, owner PID (device ID for endpoint travel), insertion ID.
        # The unique insertion ID prevents comparisons between payload types.

        if event_type == "device_complete":
            device_id, operation = process
            request = self.device_manager.requests.get(operation.request_id)
            order_key = request.pid if request is not None else device_id
        elif event_type == "network_complete":
            order_key = self.network_manager.runtime.packets[process.packet_id].pid
        elif event_type == "network_topology":
            if (self.network_manager is None
                    or not isinstance(process, NetworkTopologyEvent)):
                raise ValueError("Topology controls require a configured network event")
            order_key = (process.link_id if process.link_id is not None
                         else process.node_id)
        else:
            order_key = process.pid
        entry = (
            time,
            order_key,
            self.next_event_id,
            event_type,
            process
        )

        self.event_queue.append(entry)
        self.event_queue.sort()

        self.next_event_id += 1

    def log(self, process, event_type, action, old_state, duration=0):
        # Record an event.

        event = {
            "timestamp": self.current_time,
            "manager": "process",
            "event_type": event_type,
            "entity_id": process.pid,
            "action": action,
            "previous_state": old_state.value,
            "new_state": process.state.value,
            "outcome": "success",
            "duration": duration,
            "message": "",
            "scenario_id": self.scenario_id
        }

        self.events.append(event)

    def check_arrivals(self):
        # Dispatch due events. Legacy process events retain their original order.

        self._sync_device_state()
        self._sync_network_state()

        while self.event_queue and self.event_queue[0][0] <= self.current_time:

            time, pid, number, event_type, process = self.event_queue.pop(0)

            if event_type == "device_complete":
                self._complete_device_operation(*process)
                continue
            if event_type == "network_complete":
                self._complete_network_operation(process)
                continue
            if event_type == "network_topology":
                self._apply_network_topology_event(process)
                continue

            expected = ProcessState.NEW if event_type == "arrival" else ProcessState.WAITING
            if process.state != expected:
                raise ValueError("Unexpected state for " + event_type + ": " + process.pid)

            old_state = process.state

            process.state = ProcessState.READY

            self.scheduler.on_ready(process, time)

            self.log(
                process,
                event_type,
                "admit" if event_type == "arrival" else "unblock",
                old_state
            )

    def _queue_device_operation(
        self, device_id: str, operation: DeviceOperation | None,
    ) -> None:
        """Keep callback identity private; the event ID prevents payload comparison."""
        if operation is not None:
            try:
                self.schedule_event(
                    operation.due_at, "device_complete", (device_id, operation),
                )
            except Exception as error:
                active = self.device_manager.devices[device_id].active_operation
                if active == operation:
                    self._invalidate_unscheduled_device_work(
                        device_id, operation.request_id, error,
                    )
                raise

    def _schedule_device(self, device_id: str) -> None:
        """Ask the device manager for work without charging CPU time."""
        previous = self.device_manager.devices[device_id]
        try:
            operation = self.device_manager.schedule_io(device_id, self.current_time)
        except Exception as error:
            current = self.device_manager.devices[device_id]
            if (previous.active_operation is None
                    and previous.active_request_id is None
                    and (current.active_operation is not None
                         or current.active_request_id is not None)):
                self._invalidate_unscheduled_device_work(
                    device_id, current.active_request_id, error,
                )
            raise
        self._queue_device_operation(device_id, operation)

    def _invalidate_unscheduled_device_work(
        self, device_id: str, request_id: str | None, error: Exception,
    ) -> None:
        """Finalize the selected work, then deliver outcomes without scheduling.

        Request operations are canceled. Endpoint travel has no owning request;
        failing that device aborts the movement and fails its waiting queue.
        The outer execution boundary still seals the run until reset.
        """
        try:
            reason = "Device operation scheduling failed: " + str(error)
            if request_id is None:
                self.device_manager.fail_device(device_id, self.current_time, reason)
            else:
                self.device_manager.cancel_request(
                    request_id, self.current_time, reason,
                )
        except Exception as cleanup_error:
            error.add_note("Operation cleanup failed: " + str(cleanup_error))
        try:
            snapshot = self.device_manager.snapshot(self.current_time)
        except Exception as cleanup_error:
            error.add_note("Operation cleanup snapshot failed: " + str(cleanup_error))
            return
        try:
            self._import_device_events(snapshot)
        except Exception as cleanup_error:
            error.add_note("Operation cleanup history failed: " + str(cleanup_error))
        try:
            self._deliver_device_notifications(
                snapshot, defer_request_id=self._device_submission_id,
            )
        except Exception as cleanup_error:
            error.add_note("Operation cleanup delivery failed: " + str(cleanup_error))

    def _import_device_events(self, snapshot: dict[str, Any]) -> None:
        """Append each manager event exactly once under the engine scenario ID."""
        for event in snapshot["events"][self._device_event_cursor:]:
            self.events.append({**event, "scenario_id": self.scenario_id})
        self._device_event_cursor = len(snapshot["events"])

    def _sync_device_state(self) -> None:
        """Deliver authoritative terminal notifications, including external aborts.

        Standalone callers may fail/cancel through device_manager at the engine's
        current_time, then call check_arrivals() or step(). No estimated unblock
        is scheduled. Notification history and request ownership prevent repeats.
        """
        if self.device_manager is None:
            return
        snapshot = self.device_manager.snapshot(self.current_time)
        self._import_device_events(snapshot)
        affected = self._deliver_device_notifications(snapshot)
        for device_id in sorted(affected):
            self._schedule_device(device_id)
        if affected:
            self._import_device_events(self.device_manager.snapshot(self.current_time))

    def _deliver_device_notifications(
        self, snapshot: dict[str, Any], *, defer_request_id: str | None = None,
    ) -> set[str]:
        """Deliver once; a failed in-progress submission has its own rollback."""
        affected = set()
        notifications = snapshot["notifications"]
        for notification in notifications[self._device_notification_cursor:]:
            request_id = notification["request_id"]
            waiting = self._device_waiting.get(request_id)
            if waiting is None or request_id == defer_request_id:
                continue
            process, started = waiting
            request = snapshot["requests"].get(request_id)
            if (request is None
                    or notification["device_id"] != request["spec"]["device_id"]
                    or notification["pid"] != request["pid"]
                    or process.pid != notification["pid"]
                    or process.state != ProcessState.WAITING
                    or notification["timestamp"] != self.current_time):
                raise ValueError("Device notification does not match waiting process")
            del self._device_waiting[request_id]
            process.blocked_time += self.current_time - started
            old_state = process.state
            process.state = ProcessState.READY
            self.scheduler.on_ready(process, self.current_time)
            self._device_outcomes[request_id] = dict(notification)
            self.log(process, "unblock", "device_interrupt", old_state,
                     self.current_time - started)
            self.events[-1].update({
                "request_id": request_id, "device_id": notification["device_id"],
                "outcome": ("success" if notification["outcome"] == "COMPLETED"
                            else "error" if notification["outcome"] == "FAILED"
                            else "cancelled"),
                "device_outcome": notification["outcome"],
                "message": notification["reason"] or "",
            })
            affected.add(notification["device_id"])
        self._device_notification_cursor = len(notifications)
        return affected

    def _complete_device_operation(
        self, device_id: str, operation: DeviceOperation,
    ) -> None:
        """Ignore invalidated callbacks; a seek continues service without waking CPU."""
        active = self.device_manager.devices[device_id].active_operation
        if active != operation:
            return
        try:
            result = self.device_manager.complete_operation(
                device_id, operation.token, operation.generation, self.current_time,
            )
        except Exception as error:
            # Seek completion may activate service before its history write fails.
            self._invalidate_unscheduled_device_work(
                device_id, operation.request_id, error,
            )
            raise
        self._queue_device_operation(device_id, result.next_operation)
        self._sync_device_state()
        self._schedule_device(device_id)
        self._sync_device_state()

    def _submit_device_request(
        self, process: Process, key: tuple[str, float],
    ) -> None:
        """Submit once, block until a matching terminal interrupt, and release CPU."""
        spec = process.device_requests[key[1]]
        old_state = process.state
        blocked_time = process.blocked_time
        request_id = None
        previous_submission = self._device_submission_id
        try:
            request_id = self.device_manager.submit_request(
                process.pid, spec, self.current_time,
            )
            self._device_submission_id = request_id
            self._device_submitted[key] = request_id
            self._device_waiting[request_id] = (process, self.current_time)
            process.state = ProcessState.WAITING
            self._sync_device_state()
            self.log(process, "block", "wait_for_device", old_state)
            self.events[-1].update({
                "request_id": request_id, "device_id": spec.device_id,
            })
            self._schedule_device(spec.device_id)
            self._sync_device_state()
        except Exception as error:
            # Registration rolls back locally if it fails before returning an ID.
            # Otherwise invalidate only this request; queued callbacks become stale.
            failed_state = process.state
            if request_id is not None:
                self._device_waiting.pop(request_id, None)
                self._device_submitted.pop(key, None)
            process.state = old_state
            process.blocked_time = blocked_time
            try:
                if request_id is not None:
                    self.device_manager.cancel_request(
                        request_id, self.current_time,
                        "Device submission failed: " + str(error),
                    )
            except Exception as cleanup_error:
                error.add_note("Request cleanup failed: " + str(cleanup_error))
            try:
                self._import_device_events(
                    self.device_manager.snapshot(self.current_time),
                )
                self.log(process, "device_submission_failed", "abort_device_wait",
                         failed_state)
                self.events[-1].update({"outcome": "error", "message": str(error)})
            except Exception as cleanup_error:
                error.add_note("Submission diagnostic failed: " + str(cleanup_error))
            raise
        finally:
            self._device_submission_id = previous_submission

    @staticmethod
    def _network_target(source: str, destination: str) -> str:
        """Canonical RBAC path; node IDs cannot inject POSIX path segments."""
        def encode(node: str) -> str:
            return quote(node, safe="").replace(".", "%2E")

        return f"/nodes/{encode(source)}/to/{encode(destination)}"

    def _sync_network_state(self) -> None:
        """Import history and deliver outcomes only at explicit execution boundaries.

        External callers must pass manager failure/cancellation updates through
        apply_network_update, including their operation instructions. Results
        never calls this method. NetworkManager remains callback identity owner.
        """
        if self.network_manager is None:
            return
        snapshot = self.network_manager.snapshot(self.current_time)
        for event in snapshot["events"][self._network_event_cursor:]:
            self.events.append({**event, "scenario_id": self.scenario_id})
        self._network_event_cursor = len(snapshot["events"])
        self._deliver_network_notifications(snapshot)

    def _deliver_network_notifications(self, snapshot: dict[str, Any]) -> None:
        """Validate packet/PID ownership before exactly-once wakeup and accounting."""
        generation = self.network_manager.runtime.generation
        for packet_id, notice in snapshot["notifications"].items():
            waiting = self._network_waiting.get(packet_id)
            if waiting is None or packet_id == self._network_submission_id:
                continue
            process, started = waiting
            packet = snapshot["packets"].get(packet_id)
            if (packet is None or notice["packet_id"] != packet_id
                    or notice["pid"] != packet["pid"] or process.pid != notice["pid"]
                    or notice["source"] != packet["spec"]["source"]
                    or notice["destination"] != packet["spec"]["destination"]
                    or notice["generation"] != packet["generation"]
                    or notice["generation"] != generation
                    or notice["outcome"] != packet["state"]
                    or notice["reason"] != packet["reason"]
                    or notice["timestamp"] != packet["finished_at"]
                    or notice["timestamp"] != self.current_time
                    or process.state != ProcessState.WAITING):
                raise ValueError("Network notification does not match waiting process")
            del self._network_waiting[packet_id]
            process.blocked_time += self.current_time - started
            old_state = process.state
            process.state = ProcessState.READY
            self.scheduler.on_ready(process, self.current_time)
            outcome = {key: value for key, value in notice.items()
                       if key != "generation"}
            outcome["request_offset"] = next(
                key[1] for key, value in self._network_submitted.items()
                if value == packet_id
            )
            self._network_outcomes[packet_id] = outcome
            self.log(process, "unblock", "network_interrupt", old_state,
                     self.current_time - started)
            self.events[-1].update({
                "packet_id": packet_id, "network_outcome": notice["outcome"],
                "outcome": "success" if notice["outcome"] == "DELIVERED" else "error",
                "message": notice["reason"],
            })

    @staticmethod
    def _network_cleanup_note(error: Exception, cleanup: Exception) -> None:
        """Retain nested cleanup diagnostics without replacing the primary error."""
        error.add_note("Network cleanup failed: " + repr(cleanup))
        for note in getattr(cleanup, "__notes__", ()):
            error.add_note(note)

    def _register_network_update(self, update: NetworkUpdate) -> None:
        """Register ordered instructions; invalidate only an unregistered suffix."""
        if not isinstance(update, NetworkUpdate):
            raise ValueError("Expected NetworkUpdate")
        for index, operation in enumerate(update.operations):
            active = self.network_manager.runtime.operations.get(operation.token)
            if active != operation:
                continue
            # The existing queue is the registration record, not a second token
            # registry. Reapplying an update must not create duplicate callbacks.
            if any(entry[3] == "network_complete" and entry[4] == operation
                   for entry in self.event_queue):
                continue
            try:
                self.schedule_event(operation.due_at, "network_complete", operation)
            except Exception as error:
                suffix = update.operations[index:]
                # Registration may append and then raise; remove only that
                # suffix's callbacks, leaving the registered prefix untouched.
                self.event_queue[:] = [
                    entry for entry in self.event_queue
                    if not (entry[3] == "network_complete" and entry[4] in suffix)
                ]
                try:
                    cleanup = self.network_manager.abort_operations(
                        suffix, self.current_time,
                    )
                    if cleanup.operations:
                        self._register_network_update(cleanup)
                except Exception as cleanup_error:
                    self._network_cleanup_note(error, cleanup_error)
                    # If the abort entry point failed before doing any cleanup,
                    # cancel only the suffix's still-owned packets. A registered
                    # prefix must retain both its operation and process owner.
                    for failed in suffix:
                        active = self.network_manager.runtime.operations.get(
                            failed.token,
                        )
                        if active != failed:
                            continue
                        try:
                            fallback = self.network_manager.cancel_packet(
                                failed.packet_id, self.current_time,
                            )
                            if fallback.operations:
                                self._register_network_update(fallback)
                        except Exception as fallback_error:
                            self._network_cleanup_note(error, fallback_error)
                try:
                    self._sync_network_state()
                except Exception as cleanup_error:
                    self._network_cleanup_note(error, cleanup_error)
                raise
        self._sync_network_state()

    def apply_network_update(self, update: NetworkUpdate) -> None:
        """Apply external manager actions at current_time, never through results().

        Example: engine.apply_network_update(manager.fail_link(id, current_time)).
        Handles returned callbacks and authoritative notification history, then
        dispatches eligible queues. Unexpected failures require engine reset.
        """
        self._raise_if_failed()
        if self.network_manager is None:
            raise ValueError("Network updates require a network manager")
        try:
            self._register_network_update(update)
            self._register_network_update(
                self.network_manager.schedule_network(self.current_time),
            )
        except Exception as error:
            self._fail_execution(error)

    def _apply_network_topology_event(self, event: NetworkTopologyEvent) -> None:
        """Execute configured controls with the normal queue order and safety contract."""
        actions = {
            TopologyAction.LINK_DOWN: self.network_manager.fail_link,
            TopologyAction.LINK_UP: self.network_manager.recover_link,
            TopologyAction.NODE_DOWN: self.network_manager.fail_node,
            TopologyAction.NODE_UP: self.network_manager.recover_node,
        }
        target = event.link_id if event.link_id is not None else event.node_id
        try:
            update = actions[event.action](target, self.current_time)
        except Exception as error:
            try:
                self._sync_network_state()
            except Exception as cleanup_error:
                self._network_cleanup_note(error, cleanup_error)
            raise
        self.apply_network_update(update)

    def _complete_network_operation(self, operation: NetworkOperation) -> None:
        """Dispatch serialization/propagation boundaries without charging CPU time."""
        try:
            update = self.network_manager.complete_operation(
                operation, self.current_time,
            )
        except Exception as error:
            try:
                self._sync_network_state()
            except Exception as cleanup_error:
                self._network_cleanup_note(error, cleanup_error)
            raise
        self._register_network_update(update)

    def _submit_network_request(
        self, process: Process, key: tuple[str, float],
    ) -> bool:
        """Authorize once, associate before publication, and synchronously block."""
        spec = process.network_requests[key[1]]
        validate_network_spec(spec, self.network_manager.runtime.config)
        if self.security_manager is not None:
            self.security_manager.set_context(self.current_time, self.scenario_id)
            try:
                allowed = self.security_manager.authorize(
                    process.pid, "send", self._network_target(
                        spec.source, spec.destination,
                    ), resource="network",
                )
            finally:
                self._sync_security_events()
            if not allowed:
                self._network_submitted[key] = None
                self._network_outcomes[f"denied:{process.pid}:{key[1]}"] = {
                    "packet_id": None, "pid": process.pid, "request_offset": key[1],
                    "source": spec.source, "destination": spec.destination,
                    "timestamp": self.current_time, "outcome": "DENIED",
                    "reason": "ACCESS_DENIED",
                }
                self.log(process, "network_denied", "network_send", process.state)
                self.events[-1].update({
                    "outcome": "denied", "message": "ACCESS_DENIED",
                })
                return False
        old_state, blocked_time = process.state, process.blocked_time
        previous_submission = self._network_submission_id
        expected_id = packet_identifier(
            self.network_manager.runtime.next_packet_sequence,
        )
        self._network_submission_id = expected_id
        packet_id = None
        try:
            packet_id = self.network_manager.submit_packet(
                process.pid, spec, self.current_time,
            )
            self._network_submission_id = packet_id
            self._network_submitted[key] = packet_id
            self._network_waiting[packet_id] = process, self.current_time
            process.state = ProcessState.WAITING
            self.log(process, "block", "wait_for_network", old_state)
            self.events[-1]["packet_id"] = packet_id
            self._register_network_update(
                self.network_manager.schedule_network(self.current_time),
            )
            return True
        except Exception as error:
            packet_id = packet_id or expected_id
            runtime = self.network_manager.runtime
            packet = runtime.packets.get(packet_id)
            registered = any(
                entry[3] == "network_complete" and entry[4].packet_id == packet_id
                and runtime.operations.get(entry[4].token) == entry[4]
                for entry in self.event_queue
            )
            # A different operation can fail after this packet's callback was
            # registered. Preserve that valid prefix and its waiting association.
            if not registered:
                self._network_waiting.pop(packet_id, None)
                self._network_submitted.pop(key, None)
                process.state, process.blocked_time = old_state, blocked_time
                try:
                    if packet is not None:
                        cleanup = self.network_manager.cancel_packet(
                            packet_id, self.current_time,
                        )
                        self._register_network_update(cleanup)
                except Exception as cleanup_error:
                    self._network_cleanup_note(error, cleanup_error)
            try:
                self._sync_network_state()
            except Exception as cleanup_error:
                self._network_cleanup_note(error, cleanup_error)
            raise
        finally:
            self._network_submission_id = previous_submission

    def _has_network_work(self) -> bool:
        """Only live operations/queues prolong execution, never stale callbacks."""
        if self.network_manager is None:
            return False
        runtime = self.network_manager.runtime
        return bool(runtime.operations or any(
            link.queue for link in runtime.links.values()
        ) or any(entry[3] == "network_topology" for entry in self.event_queue))

    @staticmethod
    def _observable_network_data(value: Any) -> Any:
        """Hide callback generations and tokens without changing manager snapshots."""
        if isinstance(value, dict):
            hidden = {"token", "operation_token", "generation", "packet_generation",
                      "operation_generation"}
            record = isinstance(value.get("packet_id"), str) and any(
                key in value for key in ("spec", "kind", "outcome")
            )
            return {key: SimulationEngine._observable_network_data(item)
                    for key, item in value.items() if not record or key not in hidden}
        if isinstance(value, list):
            return [SimulationEngine._observable_network_data(item) for item in value]
        return value

    def _network_results(self) -> dict[str, Any]:
        """Read-only network results; notification delivery has no path through here."""
        snapshot = (self.network_manager.snapshot(self.current_time)
                    if self.network_manager is not None else None)
        timeline = []
        if snapshot:
            for packet_id, hops in snapshot["hop_history"].items():
                for hop in hops:
                    for kind in ("transmission", "propagation"):
                        start = hop[kind + "_start"]
                        end = hop[kind + "_end"]
                        if start is not None:
                            timeline.append({
                                "packet_id": packet_id,
                                "pid": snapshot["packets"][packet_id]["pid"],
                                "link_id": hop["link_id"], "kind": kind.upper(),
                                "start": start, "end": end if end is not None
                                else self.current_time, "active": end is None,
                                "outcome": hop["outcome"],
                            })
        return self._observable_network_data({
            "network_topology": {
                **{key: snapshot[key] for key in ("config", "nodes", "links")},
                "events": [{
                    "timestamp": event.timestamp, "action": event.action.value,
                    **({"link_id": event.link_id} if event.link_id is not None
                       else {"node_id": event.node_id}),
                } for event in self.network_events],
            } if snapshot else {},
            "network_packets": {
                key: {**packet, "path": snapshot["packet_paths"][key],
                      "hops": snapshot["hop_history"][key]}
                for key, packet in snapshot["packets"].items()
            } if snapshot else {},
            "network_metrics": snapshot["metrics"] if snapshot else None,
            "network_events": [{**event, "scenario_id": self.scenario_id}
                               for event in snapshot["events"]] if snapshot else [],
            "network_timeline": timeline,
            "network_outcomes": self._network_outcomes.copy(),
        })

    def _block_process(self, process, duration):
        process.blocked_time += duration
        old_state = process.state
        process.state = ProcessState.WAITING
        self.schedule_event(self.current_time + duration, "unblock", process)
        self.log(process, "block", "wait", old_state, duration)

    def _wait_for_frame(self, process):
        old_state = process.state
        process.state = ProcessState.WAITING
        self._frame_waiters.append((process, self.current_time))
        self.log(process, "block", "wait_for_frame", old_state)

    def _wake_frame_waiter(self):
        if not self._frame_waiters:
            return
        process, started = self._frame_waiters.pop(0)
        process.blocked_time += self.current_time - started
        old_state = process.state
        process.state = ProcessState.READY
        self.scheduler.on_ready(process, self.current_time)
        self.log(process, "unblock", "frame_available", old_state)

    def ready_processes(self):
        # Find all processes that are ready.

        ready = []

        for process in self.processes:

            if process.state == ProcessState.READY:
                ready.append(process)

        return ready

    def advance_time(self, duration):
        # Move the simulation clock forward.

        if duration < 0:
            raise ValueError("Time cannot move backward")

        end_time = self.current_time + duration

        # Process any arrivals that happenned 

        while self.event_queue and self.event_queue[0][0] <= end_time:

            self.current_time = self.event_queue[0][0]

            self.check_arrivals()

        self.current_time = end_time

    def _execute(self):
        # Run the simulation until all processess finish.

        while self._unfinished():

            self.check_arrivals()

            ready = self.ready_processes()

            # Move forward if no processes are ready.

            while not ready:

                if (self.completed == len(self.processes)
                        and not self._has_device_work()
                        and not self._has_network_work()):
                    return

                if not self.event_queue:
                    raise RuntimeError("No ready processes or upcoming arrivals")

                next_time = self.event_queue[0][0]

                if next_time > self.current_time:

                    self.timeline.append({
                        "pid": "IDLE",
                        "start": self.current_time,
                        "end": next_time
                    })

                self.advance_time(
                    next_time - self.current_time
                )

                self.last_pid = None

                ready = self.ready_processes()

            # Ask the scheduler which process should run next.

            decision = self.scheduler.select_next(
                ready,
                self.current_time
            )

            process = decision.process

            if process is None:
                raise RuntimeError("Scheduler did not select a process")

            # Apply context-switch if/when changing processes.

            if self.last_pid is not None and process.pid != self.last_pid:

                if self.context_switch > 0:

                    start = self.current_time

                    self.advance_time(self.context_switch)

                    self.timeline.append({
                        "pid": "CS",
                        "start": start,
                        "end": self.current_time
                    })

                    # Round Robin .
                    # Other algorithms can select again after new arrivals.

                    if self.algorithm != "rr":

                        ready = self.ready_processes()

                        decision = self.scheduler.select_next(
                            ready,
                            self.current_time
                        )

                        process = decision.process

                        if process is None:
                            raise RuntimeError("No process ready after switching")

            # Change the process state to RUNNING.

            old_state = process.state

            process.state = ProcessState.RUNNING

            self.scheduler.on_dispatch(
                process,
                self.current_time
            )

            # Record the first time the process runs.

            if process.start_time is None:

                process.start_time = self.current_time

                process.response_time = (
                    self.current_time - process.arrival_time
                )

            self.log(
                process,
                "dispatch",
                "start",
                old_state
            )

            # Return the RUNNING state before executing the process.
            yield self.results()

            time_left = decision.max_run_time

            # run the selected process.

            while process.remaining_time > 0:

                executed = process.burst_time - process.remaining_time
                key = (process.pid, executed)
                if (
                    executed in process.file_requests
                    and key not in self._files_done
                ):
                    command = process.file_requests[executed]
                    if self.security_manager is not None:
                        self.security_manager.set_context(
                            self.current_time, self.scenario_id
                        )
                    try:
                        result = self.file_manager.execute_command(
                            command,
                            process_id=process.pid,
                            user=process.pid,
                        )
                    except Exception as error:
                        # Authorization is recorded before the file operation
                        # mutates state. Preserve both records if a later,
                        # unexpected exception aborts the simulation step.
                        self._sync_security_events()
                        self._append_file_request_event(
                            process, command, error=error
                        )
                        raise
                    else:
                        self._sync_security_events()
                        self._append_file_request_event(
                            process, command, result=result
                        )
                        self._files_done.add(key)
                if executed in process.blocking_requests and key not in self._blocks_done:
                    duration = process.blocking_requests[executed]
                    self._blocks_done.add(key)
                    self._block_process(process, duration)
                    break

                if (self.memory_manager is not None and
                        executed in process.memory_references and key not in self._memory_done):
                    address = process.memory_references[executed]
                    page = address // self.memory_manager.page_size
                    if key in self._memory_pending and page in self.memory_manager.page_tables.get(process.pid, {}):
                        self._memory_pending.remove(key)
                        self.memory_manager.unprotect(process, page)
                        self._memory_done.add(key)
                        self._wake_frame_waiter()
                    else:
                        try:
                            result = self.memory_manager.reference(process, address)
                        except NoFrameAvailable:
                            self._wait_for_frame(process)
                            break
                        self.events.append({
                            "timestamp": self.current_time, "manager": "memory",
                            "event_type": "page_fault" if result.fault else "page_hit",
                            "entity_id": process.pid, "action": "reference",
                            "previous_state": process.state.value, "new_state": process.state.value,
                            "outcome": "success", "duration": result.latency,
                            "message": "", "scenario_id": self.scenario_id,
                            "address": result.address, "page": result.page, "frame": result.frame,
                            "physical_address": result.physical_address, "evicted": result.evicted,
                        })
                        if result.latency:
                            self.memory_manager.protect(process, page)
                            self._memory_pending.add(key)
                            self._block_process(process, result.latency)
                            break
                        self._memory_pending.discard(key)
                        self._memory_done.add(key)

                if (executed in process.device_requests
                        and key not in self._device_submitted):
                    self._submit_device_request(process, key)
                    break
                if (executed in process.network_requests
                        and key not in self._network_submitted):
                    if self._submit_network_request(process, key):
                        break

                duration = process.remaining_time
                future_offsets = [offset for offset in process.blocking_requests if offset > executed]
                future_offsets += [offset for offset in process.memory_references if offset > executed]
                future_offsets += [
                    offset for offset in process.file_requests
                    if offset > executed
                ]
                future_offsets += [
                    offset for offset in process.device_requests if offset > executed
                ]
                future_offsets += [
                    offset for offset in process.network_requests if offset > executed
                ]
                if future_offsets:
                    duration = min(duration, min(future_offsets) - executed)


                if time_left is not None:
                    duration = min(duration, time_left)

                # Stop at the next scheduled arrival (if necessary).

                if self.event_queue:

                    next_time = self.event_queue[0][0]

                    duration = min(
                        duration,
                        next_time - self.current_time
                    )

                if duration <= 0:
                    self.check_arrivals()
                    continue

                start = self.current_time

                # Update simulated and remaining CPU time.

                self.advance_time(duration)

                process.remaining_time -= duration

                if time_left is not None:
                    time_left -= duration

                self.scheduler.on_cpu_progress(
                    process,
                    duration
                )

                # Record CPU execution.

                self.timeline.append({
                    "pid": process.pid,
                    "start": start,
                    "end": self.current_time
                })

                self.log(
                    process,
                    "execution",
                    "run",
                    process.state,
                    duration
                )

                # Do not requeue completed processes.

                if process.remaining_time == 0:
                    break

                # Check if RR has expired.

                if self.scheduler.quantum_expired():
                    break

                # Check if a different process should run.

                if self.scheduler.should_preempt(
                    process,
                    self.ready_processes(),
                    self.current_time
                ):
                    break

            self.scheduler.on_cpu_stop(process)

            old_state = process.state

            # Check if process has completed.

            if process.state == ProcessState.WAITING:
                pass
            elif process.remaining_time == 0:

                process.state = ProcessState.TERMINATED

                process.completion_time = self.current_time

                process.turnaround_time = (
                    self.current_time - process.arrival_time
                )

                process.waiting_time = (
                    process.turnaround_time - process.burst_time - process.blocked_time
                )

                if self.memory_manager is not None:
                    self.memory_manager.release(process)
                self.completed += 1

                self.log(
                    process,
                    "completion",
                    "terminate",
                    old_state
                )

            else:
                # Return unfinished processes to the ready queue.
                # New arrivals have already been added first.

                process.state = ProcessState.READY

                self.scheduler.on_ready(
                    process,
                    self.current_time
                )

                self.log(
                    process,
                    "preemption",
                    "requeue",
                    old_state
                )

            self.last_pid = process.pid

            # Local delivery/drop can finish during submission. Deliver only
            # after on_cpu_stop so the process is readied exactly once.
            self._sync_network_state()
            yield self.results()

    def _sync_security_events(self) -> None:
        """Copy new authorization events into the simulation's unified stream."""
        if self.security_manager is None:
            return
        security_events = self.security_manager.security_events
        self.events.extend(
            event.copy()
            for event in security_events[self._security_event_cursor:]
        )
        self._security_event_cursor = len(security_events)

    def _append_file_request_event(
        self, process: Process, command: str, *, result=None,
        error: Exception | None = None,
    ) -> None:
        """Record the file operation outcome separately from authorization."""
        failed = error is not None or (
            isinstance(result, str) and result.startswith("Error:")
            and getattr(self.file_manager, "_last_command_failed", True)
        )
        message = str(error) if error is not None else (
            str(result) if result is not None else ""
        )
        self.events.append({
            "timestamp": self.current_time,
            "manager": "file",
            "event_type": "file_request",
            "entity_id": process.pid,
            "action": command,
            "previous_state": process.state.value,
            "new_state": process.state.value,
            "outcome": "error" if failed else "success",
            "duration": 0,
            "message": message,
            "scenario_id": self.scenario_id,
        })

    def _has_device_work(self) -> bool:
        """Committed movement survives cancellation; invalid callbacks do not."""
        return self.device_manager is not None and any(
            runtime.active_operation is not None or runtime.queue
            for runtime in self.device_manager.devices.values()
        )

    @property
    def execution_error(self) -> dict[str, Any] | None:
        """Return detached failure details, without traceback objects."""
        return self._execution_error.copy() if self._execution_error else None

    def _raise_if_failed(self) -> None:
        if self._execution_error is not None:
            error = self._execution_error
            raise SimulationExecutionError(
                f"Simulation failed ({error['type']}): {error['message']}. "
                "Reset required before Step or Run."
            ) from self._execution_exception

    def _fail_execution(self, error: Exception) -> None:
        if self._execution_error is None:
            self._execution_exception = error
            self._execution_error = {
                "type": type(error).__name__, "message": str(error),
                "timestamp": self.current_time,
            }
            self._runner = None
        self._raise_if_failed()

    def _unfinished(self) -> bool:
        return (self.completed < len(self.processes) or self._has_device_work()
                or self._has_network_work())

    def _progress_marker(self) -> tuple:
        """Include zero-time dispatch, blocking, and callback progress."""
        return (
            self.current_time, self.completed, len(self.events), len(self.timeline),
            self.next_event_id, tuple((e[0], e[2]) for e in self.event_queue),
            tuple((p.state, p.remaining_time) for p in self.processes),
        )

    def step(self):
        """Execute one snapshot boundary; execution failures persist until reset."""
        self._raise_if_failed()
        try:
            if not self._unfinished():
                return self.results()
            if self._runner is None:
                self._runner = self._execute()
            before = self._progress_marker()
            try:
                result = next(self._runner)
            except StopIteration:
                if self._unfinished():
                    raise RuntimeError("Execution stopped with unfinished work")
                return self.results()
            if self._unfinished() and before == self._progress_marker():
                raise RuntimeError("Execution made no progress with unfinished work")
            return result
        except Exception as error:
            self._fail_execution(error)

    def run(self):
        """Finish execution or raise promptly; never spin an exhausted runner."""
        self._raise_if_failed()
        try:
            while self._unfinished():
                before = self._progress_marker()
                self.step()
                if self._unfinished() and before == self._progress_marker():
                    raise RuntimeError("Run made no progress with unfinished work")
            return self.results()
        except Exception as error:
            self._fail_execution(error)
    def get_file_tree(self):
        """Return the current in-memory file hierarchy."""

        def build_node(node):
            result = {
                "name": node.name if node.name else "/",
                "type": "directory" if node.is_directory else "file",
                "size": node.metadata.size,
                "owner": node.metadata.owner
            }

            if node.is_directory:
                result["children"] = [
                    build_node(child)
                    for child in sorted(
                        node.children.values(),
                        key=lambda item: item.name
                    )
                ]

            return result

        if self.file_manager is None:
            return None

        return build_node(self.file_manager._root)
    def results(self):
        # Calculate the final simulation results.

        if self._execution_error is None:
            self._sync_device_state()
        device = (self.device_manager.snapshot(self.current_time)
                  if self.device_manager is not None else None)

        busy_time = 0

        # Count time spent executing.

        for item in self.timeline:

            if item["pid"] != "IDLE" and item["pid"] != "CS":

                busy_time += (
                    item["end"] - item["start"]
                )

        context_switch_overhead = sum(
            item["end"] - item["start"]
            for item in self.timeline if item["pid"] == "CS"
        )
        dispatch_counts = {process.pid: 0 for process in self.processes}
        preemption_counts = {process.pid: 0 for process in self.processes}
        for event in self.events:
            if event["manager"] != "process":
                continue
            if event["event_type"] == "dispatch":
                dispatch_counts[event["entity_id"]] += 1
            elif event["event_type"] == "preemption":
                preemption_counts[event["entity_id"]] += 1

        process_results = []

        total_wait = 0
        total_turnaround = 0
        total_response = 0

        # results for every process.

        for process in self.processes:

            process_results.append({
                "pid": process.pid,
                "state": process.state.value,
                "arrival_time": process.arrival_time,
                "burst_time": process.burst_time,
                "priority": process.priority,
                "start_time": process.start_time,
                "completion_time": process.completion_time,
                "waiting_time": process.waiting_time,
                "blocked_time": process.blocked_time,
                "dispatch_count": dispatch_counts[process.pid],
                "preemption_count": preemption_counts[process.pid],
                "turnaround_time": process.turnaround_time,
                "response_time": process.response_time
            })

            total_wait += process.waiting_time

            total_turnaround += process.turnaround_time

            if process.response_time is not None:
                total_response += process.response_time

        count = len(self.processes)

        average_wait = 0
        average_turnaround = 0
        average_response = 0

        # Calculate the average process times.

        if count > 0 and self.completed == count:

            average_wait = total_wait / count

            average_turnaround = total_turnaround / count

            average_response = total_response / count

        # Calculate CPU utilization.

        utilization = 0

        if self.current_time > 0:

            utilization = (
                busy_time / self.current_time * 100
            )

        # Copy event history into a list.

        event_list = []

        for event in self.events:
            event_list.append(event.copy())

        throughput = (
            self.completed / self.current_time
            if self.current_time > 0
            else 0
        )
        if not math.isfinite(throughput):
            throughput = None

        # Return the simulation results.

        return {
            "execution_error": self.execution_error,
            **self._network_results(),
            "devices": {
                key: self._observable_device_data(value)
                for key, value in device["devices"].items()
            } if device else {},
            "device_requests": {
                key: self._observable_device_data(value)
                for key, value in device["requests"].items()
            } if device else {},
            "device_metrics": device["metrics"] if device else None,
            "device_events": [e.copy() for e in self.events
                              if e["manager"] == "device"],
            "device_timeline": self._observable_device_data(
                device["timeline"]
            ) if device else [],
            "device_outcomes": {key: value.copy()
                                for key, value in self._device_outcomes.items()},
            "memory_metrics": self.memory_manager.metrics() if self.memory_manager is not None else None,
            "memory_frames": [
                {
                    "frame": index,
                    "pid": entry[0] if entry is not None else None,
                    "page": entry[1] if entry is not None else None
                }
                for index, entry in enumerate(self.memory_manager.frames)
            ] if self.memory_manager is not None else [],
            "file_tree": self.get_file_tree(),
            "file_storage_used": self.file_manager.storage_used(),
            "security_enabled": self.security_manager is not None,
            "security_users": (
                self.security_manager.users if self.security_manager is not None else []
            ),
            "security_roles": (
                self.security_manager.roles if self.security_manager is not None else []
            ),
            "security_events": (
                list(self.security_manager.security_events)
                if self.security_manager is not None else []
            ),
            "security_alerts": (
                list(self.security_manager.security_alerts)
                if self.security_manager is not None else []
            ),
            "audit_log": (
                list(self.security_manager.audit_log)
                if self.security_manager is not None else []
            ),
            "algorithm": self.algorithm,
            "current_time": self.current_time,
            "seed": self.seed,
            "processes": process_results,
            "average_waiting_time": average_wait,
            "average_turnaround_time": average_turnaround,
            "average_response_time": average_response,
            "cpu_utilization": utilization,
            "context_switch_overhead": context_switch_overhead,
            "timeline": self.timeline,
            "events": event_list,
            "throughput": throughput
        }

    @staticmethod
    def _observable_device_data(value: Any) -> Any:
        """Omit private callback tokens/generations for reproducible run snapshots."""
        if isinstance(value, dict):
            return {
                key: SimulationEngine._observable_device_data(item)
                for key, item in value.items() if key not in ("token", "generation")
            }
        if isinstance(value, list):
            return [SimulationEngine._observable_device_data(item) for item in value]
        return value
