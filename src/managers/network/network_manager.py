"""Standalone deterministic packet execution; callers own time and callbacks.

Submit inputs, then call schedule_network at the same timestamp to obtain reserved
operations. Process each instruction at due_at using complete_operation. Results
are value-only NetworkUpdates; no engine, Process, authorization, or real I/O is
used. A transmitter is freed before propagation, allowing pipelined delivery.

Register update.operations in their returned order. If registration i fails, call
abort_operations(update.operations[i:], now); keep the registered prefix intact.
Already-registered callbacks for aborted packets become harmless stale callbacks.
Deliver the original update's notifications as well as cleanup notifications.
Updates return terminal notifications once; drain_notifications retrieves notices
retained after an exception without starting work. History always retains them.

A public transition failing after acceptance finalizes only packets touched by
that transition as CALLBACK_FAILURE; unrelated operations remain intact. Original
errors propagate, with cleanup errors attached as diagnostic notes. Validation
and predictable admission timing checks precede acceptance. No global rollback
or callback registration infrastructure is implemented here.
"""

from collections.abc import Callable, Sequence
from copy import deepcopy
from fractions import Fraction
from typing import Any, TypeVar

from .models import (
    Availability, NetworkConfig, NetworkNotification, NetworkOperation,
    NetworkRequestSpec, NetworkRuntime, NetworkUpdate, OperationKind,
    PacketRuntime, PacketState, TerminalReason, callback_token,
    create_network_runtime, create_packet_runtime, snapshot_network,
)
from .routing import shortest_path
from .scheduler import select_next
from .validation import (
    _number, _text, operation_due_at, transmission_duration,
    validate_network_config, validate_operation, validate_request_spec,
    validate_runtime_state,
)

_T = TypeVar("_T")
_TERMINAL = {
    PacketState.DELIVERED, PacketState.DROPPED,
    PacketState.FAILED, PacketState.CANCELLED,
}


def _finite(value: Fraction | int | float) -> float | None:
    """Represent a derived metric, using null for overflow/nonzero underflow."""
    try:
        result = float(value)
    except OverflowError:
        return None
    if result in (float("inf"), -float("inf")) or result != result:
        return None
    return None if value != 0 and result == 0 else result


class NetworkManager:
    """Own topology, packet lifecycles, operation identity, history, and metrics.

    runtime returns a defensive copy. Mutation timestamps are monotonic boundary
    observations, not an independently advancing simulation clock. Reads may
    project ongoing work up to the next due callback and never deliver notices.
    """

    def __init__(
        self, config: NetworkConfig | None = None, *, now: float = 0,
        scenario_id: str = "default",
    ) -> None:
        """Configure an empty or supplied topology at an explicit epoch."""
        _text(scenario_id, "scenario_id")
        self.scenario_id = scenario_id
        self._runtime = create_network_runtime(
            config if config is not None else NetworkConfig(),
        )
        self._initialize(now)

    @property
    def runtime(self) -> NetworkRuntime:
        """Return detached runtime records; callers cannot mutate manager state."""
        return deepcopy(self._runtime)

    def _initialize(self, now: float) -> None:
        _number(now, "now")
        self._epoch = self._last_time = now
        self._outbox: list[str] = []
        self._affected: set[str] = set()
        self._events: list[dict[str, Any]] = []
        self._hops: dict[str, list[dict[str, Any]]] = {}
        self._paths: dict[str, list[str]] = {}
        self._costs: dict[str, dict[str, Fraction]] = {}
        self._link_stats = {
            key: {"busy": Fraction(0), "idle": Fraction(0),
                  "unavailable": Fraction(0), "queue_area": Fraction(0),
                  "max_queue": 0, "serialized_bytes": 0,
                  "wire_bytes": Fraction(0)}
            for key in self._runtime.links
        }

    def configure(self, config: NetworkConfig, now: float = 0) -> None:
        """Atomically replace configuration only before any packet is accepted."""
        validate_network_config(config)
        self._check_time(now)
        if self._runtime.packets:
            raise ValueError("Configuration requires a pristine run; reset first")
        generation = self._runtime.generation + 1
        self._runtime = create_network_runtime(config, generation=generation)
        self._initialize(now)

    def reset(self, now: float = 0) -> None:
        """Restore UP topology, clear all history, and invalidate old callbacks."""
        _number(now, "now")
        self._runtime = create_network_runtime(
            self._runtime.config, generation=self._runtime.generation + 1,
        )
        self._initialize(now)

    def validate(self) -> None:
        """Check stable ownership and lifecycle invariants without mutation."""
        validate_runtime_state(self._runtime)

    def _check_time(self, now: float) -> None:
        _number(now, "now")
        if now < self._last_time:
            raise ValueError("Timestamp precedes the latest network transition")
        if self._outbox and now > self._last_time:
            raise ValueError("Publish reserved operations before advancing time")
        if any(op.due_at < now for op in self._runtime.operations.values()):
            raise ValueError("Process due network callbacks before advancing time")

    def _available(self, link_id: str) -> bool:
        link = self._runtime.links[link_id]
        return link.state == Availability.UP and all(
            self._runtime.nodes[node].state == Availability.UP
            for node in (link.config.source, link.config.destination)
        )

    def _project(self, now: float) -> dict[str, dict[str, Any]]:
        result = deepcopy(self._link_stats)
        delta = Fraction(now) - Fraction(self._last_time)
        for key, link in self._runtime.links.items():
            category = ("unavailable" if not self._available(key) else
                        "busy" if link.transmitting_packet_id is not None else "idle")
            result[key][category] += delta
            result[key]["queue_area"] += delta * len(link.queue)
            if link.transmitting_packet_id is not None:
                packet = self._runtime.packets[link.transmitting_packet_id]
                operation = self._runtime.operations[packet.operation_token]
                duration = Fraction(operation.due_at) - Fraction(operation.started_at)
                result[key]["wire_bytes"] += delta * packet.spec.size_bytes / duration
        return result

    def _touch(self, now: float) -> None:
        self._link_stats = self._project(now)
        self._last_time = now

    def _event(
        self, action: str, now: float, packet: PacketRuntime | None = None,
        *, link_id: str | None = None, node_id: str | None = None,
        previous: str | None = None,
        message: str = "",
    ) -> None:
        self._events.append({
            "timestamp": now, "manager": "network", "event_type": action,
            "entity_id": packet.packet_id if packet else (link_id or node_id),
            "action": action, "previous_state": previous,
            "new_state": packet.state.value if packet else None,
            "outcome": packet.reason.value if packet and packet.reason else None,
            "duration": None, "message": message,
            "scenario_id": self.scenario_id,
            "packet_id": packet.packet_id if packet else None,
            "pid": packet.pid if packet else None, "link_id": link_id,
            "node_id": node_id,
        })

    def _run(self, now: float, work: Callable[[], _T]) -> _T:
        self._check_time(now)
        self.validate()
        self._touch(now)
        self._affected = set()
        delivered_before = self._runtime.delivered_notification_ids.copy()
        try:
            result = work()
            self.validate()
            return result
        except Exception as error:
            # A failed public call did not hand its result to the caller.
            self._runtime.delivered_notification_ids = delivered_before
            for key in sorted(self._affected,
                              key=lambda key: self._runtime.packets[key].sequence):
                packet = self._runtime.packets[key]
                if packet.state in _TERMINAL:
                    continue
                try:
                    self._finish(packet, PacketState.FAILED,
                                 TerminalReason.CALLBACK_FAILURE, now)
                except Exception as cleanup_error:
                    error.add_note(f"Network cleanup for {key}: {cleanup_error!r}")
                    # Core finalization has no logging hook. A broken event sink
                    # must not leave a live operation or mask the original error.
                    try:
                        self._finalize(packet, PacketState.FAILED,
                                       TerminalReason.CALLBACK_FAILURE, now)
                    except Exception as final_error:
                        error.add_note(
                            f"Network finalization for {key}: {final_error!r}"
                        )
            raise
        finally:
            self._affected = set()

    def _cost(self, packet: PacketRuntime, name: str, duration: Fraction) -> None:
        self._costs[packet.packet_id][name] += duration
        setattr(packet, name, float(self._costs[packet.packet_id][name]))

    def _detach(self, packet: PacketRuntime, now: float) -> None:
        if packet.link_id is None:
            return
        link = self._runtime.links[packet.link_id]
        operation = self._runtime.operations.get(packet.operation_token)
        if packet.state == PacketState.QUEUED:
            self._cost(packet, "queue_wait_time",
                       Fraction(now) - Fraction(packet.queued_at))
            if packet.packet_id in link.queue:
                link.queue.remove(packet.packet_id)
        elif operation is not None:
            name = ("transmission_time" if operation.kind == OperationKind.TRANSMISSION
                    else "propagation_time")
            self._cost(packet, name, Fraction(now) - Fraction(operation.started_at))
            self._runtime.operations.pop(operation.token)
        if packet.operation_token in self._outbox:
            self._outbox.remove(packet.operation_token)
        if link.transmitting_packet_id == packet.packet_id:
            link.transmitting_packet_id = None
        if packet.packet_id in link.propagating_packet_ids:
            link.propagating_packet_ids.remove(packet.packet_id)
        # Clear core ownership before optional history updates. Cleanup must also
        # handle an attachment that failed before its operation was registered.
        packet.link_id = packet.operation_token = None
        packet.queued_at = packet.enqueue_sequence = None
        if operation is not None and self._hops[packet.packet_id]:
            phase = ("transmission" if operation.kind == OperationKind.TRANSMISSION
                     else "propagation")
            self._hops[packet.packet_id][-1][phase + "_end"] = now

    def _finalize(
        self, packet: PacketRuntime, state: PacketState, reason: TerminalReason,
        now: float,
    ) -> None:
        if packet.state in _TERMINAL:
            return
        notice = NetworkNotification(
            packet_id=packet.packet_id, generation=packet.generation, pid=packet.pid,
            source=packet.spec.source, destination=packet.spec.destination,
            timestamp=now, outcome=state, reason=reason,
        )
        self._detach(packet, now)
        packet.operation_generation += 1
        packet.state, packet.reason, packet.finished_at = state, reason, now
        self._runtime.notifications[packet.packet_id] = notice
        if (self._hops[packet.packet_id]
                and self._hops[packet.packet_id][-1]["propagation_end"] is None):
            self._hops[packet.packet_id][-1]["outcome"] = state.value

    def _finish(
        self, packet: PacketRuntime, state: PacketState, reason: TerminalReason,
        now: float,
    ) -> None:
        previous = packet.state.value
        self._finalize(packet, state, reason, now)
        self._event("PACKET_" + state.value, now, packet, previous=previous)

    def _operation(
        self, packet: PacketRuntime, link_id: str, kind: OperationKind, now: float,
    ) -> NetworkOperation:
        link = self._runtime.links[link_id].config
        duration = (transmission_duration(packet.spec.size_bytes, link)
                    if kind == OperationKind.TRANSMISSION else link.propagation_delay)
        due = operation_due_at(now, duration)
        if kind == OperationKind.TRANSMISSION:
            operation_due_at(due, link.propagation_delay)
        sequence = self._runtime.next_operation_sequence
        return NetworkOperation(
            token=callback_token(self._runtime.generation, sequence),
            generation=self._runtime.generation, sequence=sequence,
            packet_generation=packet.operation_generation, packet_id=packet.packet_id,
            link_id=link_id, kind=kind, started_at=now, due_at=due,
        )

    def _attach(self, packet: PacketRuntime, operation: NetworkOperation) -> None:
        self._runtime.next_operation_sequence += 1
        packet.link_id, packet.operation_token = operation.link_id, operation.token
        self._runtime.operations[operation.token] = operation
        self._outbox.append(operation.token)

    def _start(self, packet: PacketRuntime, link_id: str, now: float) -> None:
        self._affected.add(packet.packet_id)
        operation = self._operation(packet, link_id, OperationKind.TRANSMISSION, now)
        self._detach(packet, now)
        packet.state = PacketState.TRANSMITTING
        self._hops[packet.packet_id].append({
            "link_id": link_id, "source": packet.current_node,
            "destination": self._runtime.links[link_id].config.destination,
            "transmission_start": now, "transmission_end": None,
            "propagation_start": None, "propagation_end": None, "outcome": None,
        })
        self._attach(packet, operation)
        self._runtime.links[link_id].transmitting_packet_id = packet.packet_id
        self._event("PACKET_SEND", now, packet, link_id=link_id)

    def _start_waiting(self, link_id: str, now: float) -> None:
        packet_id = select_next(self._runtime, link_id)
        if packet_id is not None:
            self._start(self._runtime.packets[packet_id], link_id, now)

    def _admit(self, packet: PacketRuntime, now: float) -> None:
        self._affected.add(packet.packet_id)
        if any(self._runtime.nodes[node].state == Availability.DOWN
               for node in (packet.current_node, packet.spec.destination)):
            self._finish(packet, PacketState.FAILED, TerminalReason.NODE_FAILURE, now)
            return
        if packet.current_node == packet.spec.destination:
            self._finish(packet, PacketState.DELIVERED, TerminalReason.DELIVERED, now)
            return
        if packet.completed_hops >= self._runtime.config.hop_limit:
            self._finish(packet, PacketState.DROPPED, TerminalReason.HOP_LIMIT, now)
            return
        path = shortest_path(
            self._runtime, packet.current_node, packet.spec.destination,
        )
        if path is None:
            self._finish(packet, PacketState.DROPPED, TerminalReason.NO_ROUTE, now)
            return
        link_id = path[0]
        self._start_waiting(link_id, now)
        link = self._runtime.links[link_id]
        if link.transmitting_packet_id is None:
            self._start(packet, link_id, now)
        elif len(link.queue) >= link.config.queue_capacity_packets:
            self._finish(packet, PacketState.DROPPED, TerminalReason.QUEUE_FULL, now)
        else:
            packet.state, packet.link_id = PacketState.QUEUED, link_id
            packet.queued_at = now
            packet.enqueue_sequence = self._runtime.next_enqueue_sequence
            self._runtime.next_enqueue_sequence += 1
            link.queue.append(packet.packet_id)
            stats = self._link_stats[link_id]
            stats["max_queue"] = max(stats["max_queue"], len(link.queue))
            self._event("PACKET_QUEUE", now, packet, link_id=link_id)

    def submit_packet(
        self, pid: str, spec: NetworkRequestSpec, now: float,
    ) -> str:
        """Validate then accept one packet; publish its instructions at this time.

        Admission may start a free transmitter immediately, queue, drop, or finish
        zero-hop delivery. schedule_network returns any resulting instructions
        and notifications. Invalid inputs/known timing overflow create no record.
        """
        self._check_time(now)
        validate_request_spec(spec, self._runtime.config)
        _text(pid, "pid")
        path = shortest_path(self._runtime, spec.source, spec.destination)
        if path:
            link = self._runtime.links[path[0]].config
            due = operation_due_at(now, transmission_duration(spec.size_bytes, link))
            operation_due_at(due, link.propagation_delay)
        packet = create_packet_runtime(
            spec, self._runtime.config, pid=pid,
            sequence=self._runtime.next_packet_sequence,
            generation=self._runtime.generation, now=now,
        )

        def accept() -> str:
            self._runtime.packets[packet.packet_id] = packet
            self._runtime.next_packet_sequence += 1
            self._affected.add(packet.packet_id)
            self._costs[packet.packet_id] = {
                name: Fraction(0) for name in
                ("queue_wait_time", "transmission_time", "propagation_time")
            }
            self._hops[packet.packet_id], self._paths[packet.packet_id] = [], []
            self._event("PACKET_CREATE", now, packet)
            self._admit(packet, now)
            return packet.packet_id

        return self._run(now, accept)

    def _take_notifications(self) -> tuple[NetworkNotification, ...]:
        result = tuple(notice for key, notice in self._runtime.notifications.items()
                       if key not in self._runtime.delivered_notification_ids)
        self._runtime.delivered_notification_ids.update(n.packet_id for n in result)
        return result

    def drain_notifications(self, now: float) -> tuple[NetworkNotification, ...]:
        """Return previously undelivered notices once, without scheduling work."""
        self._check_time(now)
        return self._take_notifications()

    def _publish(self) -> NetworkUpdate:
        operations = tuple(self._runtime.operations[token] for token in self._outbox)
        self._affected.update(op.packet_id for op in operations)
        notices = tuple(
            notice for key, notice in self._runtime.notifications.items()
            if key not in self._runtime.delivered_notification_ids
        )
        result = NetworkUpdate(operations=operations, notifications=notices)
        self._runtime.delivered_notification_ids.update(n.packet_id for n in notices)
        self._outbox.clear()
        return result

    def schedule_network(self, now: float) -> NetworkUpdate:
        """Start eligible queues and publish newly created callbacks exactly once."""
        def schedule() -> NetworkUpdate:
            for link_id in sorted(self._runtime.links):
                self._start_waiting(link_id, now)
            return self._publish()

        return self._run(now, schedule)

    def complete_operation(
        self, operation: NetworkOperation, now: float,
    ) -> NetworkUpdate:
        """Complete at due_at; stale callbacks are no-ops at valid timestamps."""
        if not isinstance(operation, NetworkOperation):
            raise ValueError("operation must be NetworkOperation")
        _text(operation.token, "operation token")
        self._check_time(now)
        active = self._runtime.operations.get(operation.token)
        if active != operation:
            return NetworkUpdate()
        validate_operation(
            operation, self._runtime.packets[active.packet_id],
            self._runtime.links[active.link_id].config,
        )
        if operation.token in self._outbox:
            raise ValueError("Operation must be published before completion")
        if operation.due_at != now:
            raise ValueError("Callback timestamp must equal due_at")

        def complete() -> NetworkUpdate:
            packet = self._runtime.packets[operation.packet_id]
            self._affected.add(packet.packet_id)
            link_id = operation.link_id
            self._detach(packet, now)
            packet.state = PacketState.AT_NODE
            if operation.kind == OperationKind.TRANSMISSION:
                self._link_stats[link_id]["serialized_bytes"] += packet.spec.size_bytes
                propagation = self._operation(packet, link_id,
                                              OperationKind.PROPAGATION, now)
                packet.state = PacketState.PROPAGATING
                self._attach(packet, propagation)
                self._runtime.links[link_id].propagating_packet_ids.append(
                    packet.packet_id,
                )
                self._hops[packet.packet_id][-1]["propagation_start"] = now
                self._event("PACKET_PROPAGATE", now, packet, link_id=link_id)
                self._start_waiting(link_id, now)
            else:
                packet.current_node = self._runtime.links[link_id].config.destination
                packet.completed_hops += 1
                self._paths[packet.packet_id].append(link_id)
                self._hops[packet.packet_id][-1]["outcome"] = "FORWARDED"
                self._event("PACKET_RECEIVE", now, packet, link_id=link_id)
                self._admit(packet, now)
            return self._publish()

        return self._run(now, complete)

    def _packet(self, packet_id: str) -> PacketRuntime:
        _text(packet_id, "packet_id")
        if packet_id not in self._runtime.packets:
            raise ValueError(f"Unknown packet_id: {packet_id}")
        return self._runtime.packets[packet_id]

    def cancel_packet(self, packet_id: str, now: float) -> NetworkUpdate:
        """Cancel queued/in-flight work; terminal cancellation is a no-op."""
        packet = self._packet(packet_id)

        def cancel() -> NetworkUpdate:
            if packet.state not in _TERMINAL:
                self._affected.add(packet_id)
                self._finish(packet, PacketState.CANCELLED,
                             TerminalReason.CANCELLED, now)
            return self._publish()

        return self._run(now, cancel)

    def abort_operations(
        self, operations: Sequence[NetworkOperation], now: float,
    ) -> NetworkUpdate:
        """Finalize only exact live instructions whose registration failed.

        Pass the unregistered suffix of an update, never its registered prefix.
        Stale instructions are harmless. Cleanup does not dispatch waiting work.
        """
        if (not isinstance(operations, Sequence) or isinstance(operations, (str, bytes))
                or any(not isinstance(op, NetworkOperation) for op in operations)):
            raise ValueError("operations must be a sequence of NetworkOperation")

        selected = []
        for operation in operations:
            _text(operation.token, "operation token")
            if self._runtime.operations.get(operation.token) == operation:
                validate_operation(
                    operation, self._runtime.packets[operation.packet_id],
                    self._runtime.links[operation.link_id].config,
                )
                selected.append(operation)

        def abort() -> NetworkUpdate:
            self._affected.update(op.packet_id for op in selected)
            for operation in selected:
                if self._runtime.operations.get(operation.token) == operation:
                    packet = self._packet(operation.packet_id)
                    self._affected.add(packet.packet_id)
                    self._finish(packet, PacketState.FAILED,
                                 TerminalReason.CALLBACK_FAILURE, now)
            return self._publish()

        return self._run(now, abort)

    def _failure(
        self, now: float, *, link_id: str | None = None, node_id: str | None = None,
    ) -> NetworkUpdate:
        affected_links = {
            key for key, link in self._runtime.links.items()
            if key == link_id or node_id in (
                link.config.source, link.config.destination,
            )
        }
        queued = []
        affected = []
        for key in sorted(affected_links):
            link = self._runtime.links[key]
            queued.extend(sorted((self._packet(pid) for pid in link.queue),
                                 key=lambda p: (p.queued_at, p.enqueue_sequence,
                                                p.sequence)))
            affected.extend(self._packet(pid) for pid in link.propagating_packet_ids)
            if link.transmitting_packet_id:
                affected.append(self._packet(link.transmitting_packet_id))
        if node_id is not None:
            affected.extend(p for p in self._runtime.packets.values()
                            if p.state == PacketState.AT_NODE
                            and p.current_node == node_id)
        self._affected.update(p.packet_id for p in queued + affected)
        # Remove all affected ownership before any route query; DOWN topology
        # cannot retain queue/operation ownership under Phase 1 validation.
        for packet in queued:
            self._detach(packet, now)
            packet.state = PacketState.AT_NODE
        reason = (TerminalReason.NODE_FAILURE if node_id is not None
                  else TerminalReason.LINK_FAILURE)
        for packet in sorted(affected, key=lambda p: p.sequence):
            self._finish(packet, PacketState.FAILED, reason, now)
        if node_id is not None:
            self._runtime.nodes[node_id].state = Availability.DOWN
            for packet in queued:
                if packet.current_node == node_id:
                    self._finish(packet, PacketState.FAILED, reason, now)
        else:
            self._runtime.links[link_id].state = Availability.DOWN
        for packet in queued:
            if packet.state not in _TERMINAL:
                self._event("PACKET_REROUTE", now, packet)
                self._admit(packet, now)
        self._event("NODE_DOWN" if node_id else "LINK_DOWN", now,
                    link_id=link_id, node_id=node_id)
        return self._publish()

    def fail_link(self, link_id: str, now: float) -> NetworkUpdate:
        """Fail in-flight work; reroute waiters with deterministic admission."""
        self._link(link_id)
        return self._run(now, lambda: self._failure(now, link_id=link_id))

    def _link(self, link_id: str) -> None:
        _text(link_id, "link_id")
        if link_id not in self._runtime.links:
            raise ValueError(f"Unknown link_id: {link_id}")

    def _node(self, node_id: str) -> None:
        _text(node_id, "node_id")
        if node_id not in self._runtime.nodes:
            raise ValueError(f"Unknown node_id: {node_id}")

    def fail_node(self, node_id: str, now: float) -> NetworkUpdate:
        """Fail resident/in-flight packets; reroute eligible upstream waiters."""
        self._node(node_id)
        return self._run(now, lambda: self._failure(now, node_id=node_id))

    def recover_link(self, link_id: str, now: float) -> NetworkUpdate:
        """Restore link availability, without retrying any terminal packet."""
        self._link(link_id)

        def recover() -> NetworkUpdate:
            self._runtime.links[link_id].state = Availability.UP
            self._event("LINK_UP", now, link_id=link_id)
            return self._publish()

        return self._run(now, recover)

    def recover_node(self, node_id: str, now: float) -> NetworkUpdate:
        """Restore node availability; explicitly DOWN links remain DOWN."""
        self._node(node_id)

        def recover() -> NetworkUpdate:
            self._runtime.nodes[node_id].state = Availability.UP
            self._event("NODE_UP", now, node_id=node_id)
            return self._publish()

        return self._run(now, recover)

    def metrics(self, now: float) -> dict[str, Any]:
        """Project detached packet/link metrics without advancing or delivering.

        Rates use elapsed time since reset, including unavailable intervals.
        Zero-denominator rates are null. Busy means serialization, not propagation.
        Goodput counts unique delivered payload; serialized_bytes counts fully
        serialized hops. wire_bytes includes partial/aborted serialization, using
        each operation's byte fraction so completion counts exactly its payload.
        Averages of latency include delivered packets only; queue wait averages
        include all accepted packets, including live/dropped/cancelled packets.
        """
        self._check_time(now)
        elapsed = Fraction(now) - Fraction(self._epoch)
        stats = self._project(now)
        packets = {}
        counts = {state.value.lower(): 0
                  for state in sorted(_TERMINAL, key=lambda state: state.value)}
        reasons: dict[str, int] = {}
        delivered_bytes = 0
        latencies = []
        waits = []
        for key, packet in self._runtime.packets.items():
            costs = self._costs[key].copy()
            if packet.state == PacketState.QUEUED:
                costs["queue_wait_time"] += Fraction(now) - Fraction(packet.queued_at)
            elif packet.operation_token:
                op = self._runtime.operations[packet.operation_token]
                name = ("transmission_time" if op.kind == OperationKind.TRANSMISSION
                        else "propagation_time")
                costs[name] += Fraction(now) - Fraction(op.started_at)
            waits.append(costs["queue_wait_time"])
            terminal = packet.state in _TERMINAL
            if terminal:
                counts[packet.state.value.lower()] += 1
                reasons[packet.reason.value] = reasons.get(packet.reason.value, 0) + 1
            latency = (Fraction(packet.finished_at if terminal else now)
                       - Fraction(packet.submitted_at))
            if packet.state == PacketState.DELIVERED:
                delivered_bytes += packet.spec.size_bytes
                latencies.append(latency)
            packets[key] = {
                **{name: _finite(value) for name, value in costs.items()},
                "submitted_at": packet.submitted_at, "finished_at": packet.finished_at,
                "elapsed_time": _finite(latency),
                "delivery_latency": (
                    _finite(latency) if packet.state == PacketState.DELIVERED else None
                ),
                "outcome": packet.state.value,
                "reason": packet.reason.value if terminal else None,
            }
        links = {}
        for key, item in stats.items():
            link = self._runtime.links[key]
            links[key] = {
                "busy_time": _finite(item["busy"]), "idle_time": _finite(item["idle"]),
                "unavailable_time": _finite(item["unavailable"]),
                "utilization": _finite(item["busy"] / elapsed) if elapsed else None,
                "queue_length_integral": _finite(item["queue_area"]),
                "average_queue_length": (
                    _finite(item["queue_area"] / elapsed) if elapsed else None
                ),
                "maximum_queue_length": item["max_queue"],
                "current_queue_length": len(link.queue),
                "serialized_bytes": item["serialized_bytes"],
                "wire_bytes": _finite(item["wire_bytes"]),
            }
        total = len(self._runtime.packets)
        return {
            "submitted": total, **counts, "elapsed_time": _finite(elapsed),
            "in_flight": len(self._runtime.operations),
            "queued": sum(len(link.queue) for link in self._runtime.links.values()),
            "terminal_notifications": len(self._runtime.notifications),
            "reason_counts": dict(sorted(reasons.items())),
            "delivered_payload_bytes": delivered_bytes,
            "throughput": (
                _finite(Fraction(counts["delivered"]) / elapsed) if elapsed else None
            ),
            "goodput": (
                _finite(Fraction(delivered_bytes) / elapsed) if elapsed else None
            ),
            "average_delivery_latency": (
                _finite(sum(latencies) / len(latencies)) if latencies else None
            ),
            "average_queue_wait": _finite(sum(waits) / total) if total else None,
            "links": links, "packets": packets,
        }

    def snapshot(self, now: float) -> dict[str, Any]:
        """Return JSON-safe configuration, state, history, events, and metrics."""
        self._check_time(now)
        result = snapshot_network(self._runtime)
        result.update({
            "metrics": self.metrics(now), "events": deepcopy(self._events),
            "packet_paths": deepcopy(self._paths), "hop_history": deepcopy(self._hops),
        })
        return result
