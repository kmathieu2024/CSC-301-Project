"""Network data contracts, independent of the simulation engine and clock.

Links are directed; full duplex requires two links. Delivery is store-and-forward:
one transmitter per link, but arbitrarily many packets may be propagating.
Capacity counts waiting packets only. FIFO uses (queued_at, enqueue_sequence);
priority uses (priority, queued_at, enqueue_sequence), lower priority first.
Neither policy preempts an operation. Routing uses positive static costs.

The engine retains its existing timestamp/order-key/insertion-ID ordering. A
manager transition must finish before processing the next callback at that time.
Within a network transition, traverse affected link IDs lexicographically and
packets by (queued_at, enqueue_sequence, sequence). Returned instructions retain
that order. Do not add a new global failure/completion priority to the engine.

On link failure, abort transmission/propagation on that link. Waiting packets at
UP nodes detach from the failed egress and reroute in the order above. Admission
to each new egress enforces its waiting-only capacity, including earlier admitted
reroutes; a free transmitter may accept directly even at zero capacity. Overflow
is drop-tail, including priority queues. No route drops immediately. A failed
node terminates resident/affected in-flight packets. Recovery never revives a
terminal packet. NetworkManager implements routing, admission, and failures;
these models contain no execution logic.

NetworkManager owns sequence allocation, transitions, rollback, and notification
delivery. Tokens are unique operation sequences within a reset generation. Packet
operation_generation increments whenever callbacks are invalidated; completion
requires matching token, run generation, packet generation, and live ownership.
Reset increments run generation and creates fresh records/counters, without old
notifications. Cancellation never reuses an operation sequence. Terminal packets
retain one immutable notification; delivered_notification_ids acknowledges it
once. No callback or notification may revive a terminal packet. Cleanup must
invalidate the actual selected packet and preserve unrelated operations.

Constructors are containers, as in Device Manager. Use explicit validation at
boundaries; factories and snapshots always validate. No authorization occurs here.
"""

from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum


class QueuePolicy(Enum):
    """Nonpreemptive outgoing-link queue policies."""

    FIFO = "FIFO"
    PRIORITY = "PRIORITY"


class Availability(Enum):
    """Administrative/failure availability, separate from transmitter activity."""

    UP = "UP"
    DOWN = "DOWN"


class PacketState(Enum):
    """Packet lifecycle; the last four values are irreversible terminal states."""

    AT_NODE = "AT_NODE"
    QUEUED = "QUEUED"
    TRANSMITTING = "TRANSMITTING"
    PROPAGATING = "PROPAGATING"
    DELIVERED = "DELIVERED"
    DROPPED = "DROPPED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TerminalReason(Enum):
    """Machine-readable terminal causes; rejection before acceptance has no record."""

    DELIVERED = "DELIVERED"
    QUEUE_FULL = "QUEUE_FULL"
    NO_ROUTE = "NO_ROUTE"
    HOP_LIMIT = "HOP_LIMIT"
    LINK_FAILURE = "LINK_FAILURE"
    NODE_FAILURE = "NODE_FAILURE"
    CALLBACK_FAILURE = "CALLBACK_FAILURE"
    CANCELLED = "CANCELLED"


class OperationKind(Enum):
    """Independent completion boundaries for serialization and propagation."""

    TRANSMISSION = "TRANSMISSION"
    PROPAGATION = "PROPAGATION"


@dataclass(frozen=True, kw_only=True)
class NodeConfig:
    """Immutable node identity and optional display address."""

    node_id: str
    address: str | None = None


@dataclass(frozen=True, kw_only=True)
class LinkConfig:
    """Directed egress; bandwidth is bytes/time, propagation delay is time."""

    link_id: str
    source: str
    destination: str
    bandwidth_bytes_per_time_unit: float
    propagation_delay: float = 0
    routing_cost: float = 1
    queue_policy: QueuePolicy = QueuePolicy.FIFO
    queue_capacity_packets: int = 0


@dataclass(frozen=True, kw_only=True)
class NetworkConfig:
    """Immutable topology. Copy collections to prevent caller-owned list mutation.

    Empty networks, parallel directed links, and self-links are valid. Positive
    routing costs and the hop limit bound forwarding; source=destination
    is a zero-hop delivery that needs no link.
    """

    nodes: tuple[NodeConfig, ...] = ()
    links: tuple[LinkConfig, ...] = ()
    hop_limit: int = 16

    def __post_init__(self) -> None:
        """Freeze topology collections; contextual validation stays explicit."""
        for name in ("nodes", "links"):
            value = getattr(self, name)
            if not isinstance(value, (tuple, list)):
                raise ValueError(f"{name} must be a tuple or list")
            object.__setattr__(self, name, tuple(value))


@dataclass(frozen=True, kw_only=True)
class NetworkRequestSpec:
    """One packet input; priority is a nonnegative integer, lower is earlier."""

    source: str
    destination: str
    size_bytes: int
    priority: int = 1


@dataclass(frozen=True, kw_only=True)
class NetworkOperation:
    """Committed identity owned by the manager; callers register callback timing."""

    token: str
    generation: int
    sequence: int
    packet_generation: int
    packet_id: str
    link_id: str
    kind: OperationKind
    started_at: float
    due_at: float


@dataclass(frozen=True, kw_only=True)
class NetworkNotification:
    """Exactly one terminal record per accepted packet, delivered by its owner."""

    packet_id: str
    generation: int
    pid: str
    source: str
    destination: str
    timestamp: float
    outcome: PacketState
    reason: TerminalReason
    message: str | None = None


@dataclass(frozen=True, kw_only=True)
class NetworkUpdate:
    """Ordered instructions/notifications; no execution or clock advancement."""

    operations: tuple[NetworkOperation, ...] = ()
    notifications: tuple[NetworkNotification, ...] = ()

    def __post_init__(self) -> None:
        """Defensively freeze output batches and reject duplicate identities."""
        for name, model, identity in (
            ("operations", NetworkOperation, "token"),
            ("notifications", NetworkNotification, "packet_id"),
        ):
            values = getattr(self, name)
            if not isinstance(values, (list, tuple)):
                raise ValueError(f"{name} must be a tuple or list")
            if any(not isinstance(value, model) for value in values):
                raise ValueError(f"{name} contains an invalid record")
            ids = [getattr(value, identity) for value in values]
            if any(not isinstance(value, str) for value in ids):
                raise ValueError(f"{name} identities must be strings")
            if len(set(ids)) != len(ids):
                raise ValueError(f"{name} contains duplicate identities")
            object.__setattr__(self, name, tuple(values))


@dataclass(kw_only=True)
class PacketRuntime:
    """Manager-owned history. current_node commits only after propagation.

    AT_NODE is a stable admission/rerouting boundary. queue timing applies only
    while QUEUED. operation_token applies only during transmission/propagation.
    Time accumulators exclude current operation's uncommitted elapsed portion.
    completed_hops counts completed propagation boundaries, not attempted hops.
    """

    packet_id: str
    generation: int
    sequence: int
    pid: str
    spec: NetworkRequestSpec
    submitted_at: float
    current_node: str
    state: PacketState = PacketState.AT_NODE
    operation_generation: int = 0
    link_id: str | None = None
    operation_token: str | None = None
    queued_at: float | None = None
    enqueue_sequence: int | None = None
    completed_hops: int = 0
    queue_wait_time: float = 0
    transmission_time: float = 0
    propagation_time: float = 0
    finished_at: float | None = None
    reason: TerminalReason | None = None


@dataclass(kw_only=True)
class NodeRuntime:
    """Availability only; outgoing link queues own buffered packets."""

    config: NodeConfig
    state: Availability = Availability.UP


@dataclass(kw_only=True)
class LinkRuntime:
    """Waiting IDs exclude the single transmitter and all propagating packets."""

    config: LinkConfig
    state: Availability = Availability.UP
    queue: list[str] = field(default_factory=list)
    transmitting_packet_id: str | None = None
    propagating_packet_ids: list[str] = field(default_factory=list)


@dataclass(kw_only=True)
class NetworkRuntime:
    """Manager-owned registries and allocation counters, never a second clock.

    Operations contain only live callbacks. Terminal notifications remain in
    history; acknowledgment is separate. Reset uses the factory with a greater
    generation, clearing all registries and restarting deterministic sequences.
    """

    config: NetworkConfig
    generation: int = 0
    nodes: dict[str, NodeRuntime] = field(default_factory=dict)
    links: dict[str, LinkRuntime] = field(default_factory=dict)
    packets: dict[str, PacketRuntime] = field(default_factory=dict)
    operations: dict[str, NetworkOperation] = field(default_factory=dict)
    notifications: dict[str, NetworkNotification] = field(default_factory=dict)
    delivered_notification_ids: set[str] = field(default_factory=set)
    next_packet_sequence: int = 0
    next_operation_sequence: int = 0
    next_enqueue_sequence: int = 0


def packet_identifier(sequence: int) -> str:
    """Stable replay ID; run generation separately prevents reset collisions."""
    from .validation import _integer

    _integer(sequence, "sequence")
    return f"packet-{sequence}"


def callback_token(generation: int, sequence: int) -> str:
    """Deterministic token; the manager must never reuse its sequence."""
    from .validation import _integer

    _integer(generation, "generation")
    _integer(sequence, "sequence")
    return f"network-{generation}-{sequence}"


def create_network_runtime(
    config: NetworkConfig, *, generation: int = 0,
) -> NetworkRuntime:
    """Validate the entire topology before allocating independent runtime state."""
    from .validation import _integer, validate_network_config

    validate_network_config(config)
    _integer(generation, "generation")
    return NetworkRuntime(
        config=config, generation=generation,
        nodes={node.node_id: NodeRuntime(config=node) for node in config.nodes},
        links={link.link_id: LinkRuntime(config=link) for link in config.links},
    )


def create_packet_runtime(
    spec: NetworkRequestSpec, config: NetworkConfig, *, pid: str,
    sequence: int, generation: int, now: float,
) -> PacketRuntime:
    """Create a detached validated packet; do not register or enqueue anything."""
    from .validation import _integer, _number, _text, validate_request_spec

    validate_request_spec(spec, config)
    _text(pid, "pid")
    _integer(generation, "generation")
    _number(now, "now")
    return PacketRuntime(
        packet_id=packet_identifier(sequence), generation=generation,
        sequence=sequence, pid=pid, spec=spec, submitted_at=now,
        current_node=spec.source,
    )


def snapshot_network(runtime: NetworkRuntime) -> dict[str, object]:
    """Validate and return detached strict-JSON data, hiding callback identities.

    No notification delivery, timing projection, or state mutation occurs.
    NetworkManager.snapshot adds projected metrics and detached event history.
    """
    from .validation import validate_runtime_state

    validate_runtime_state(runtime)

    def encode(value: object) -> object:
        if isinstance(value, Enum):
            return value.value
        if is_dataclass(value):
            return {
                item.name: encode(getattr(value, item.name))
                for item in fields(value)
                if item.name not in ("token", "operation_token")
            }
        if isinstance(value, dict):
            return {key: encode(item) for key, item in sorted(value.items())}
        if isinstance(value, (list, tuple)):
            return [encode(item) for item in value]
        return value

    return {
        "config": encode(runtime.config), "nodes": encode(runtime.nodes),
        "links": encode(runtime.links), "packets": encode(runtime.packets),
        "operations": [encode(op) for _, op in sorted(runtime.operations.items())],
        "notifications": encode(runtime.notifications),
        "delivered_notification_ids": sorted(runtime.delivered_notification_ids),
    }
