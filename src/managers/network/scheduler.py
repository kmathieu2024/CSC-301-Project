"""Pure nonpreemptive selection from validated waiting-only link queues.

Selection returns an ID rather than exposing mutable packet records. None means
no new transmission: empty queue, unavailable link/endpoints, or busy transmitter.
Propagating packets do not hold the transmitter. Validate before every early
return so malformed records are rejected rather than hidden by empty/busy state.
Priority has no aging and may starve lower-priority traffic.
"""

from .models import Availability, NetworkRuntime, QueuePolicy
from .validation import _text, validate_runtime_state


def _select(
    runtime: NetworkRuntime, link_id: str, required_policy: QueuePolicy | None,
) -> str | None:
    validate_runtime_state(runtime)
    _text(link_id, "link_id")
    if link_id not in runtime.links:
        raise ValueError(f"Unknown link_id: {link_id}")
    link = runtime.links[link_id]
    policy = link.config.queue_policy
    if required_policy is not None and policy != required_policy:
        raise ValueError(f"Link {link_id} requires {required_policy.value} policy")
    if (link.state != Availability.UP
            or runtime.nodes[link.config.source].state != Availability.UP
            or runtime.nodes[link.config.destination].state != Availability.UP
            or link.transmitting_packet_id is not None or not link.queue):
        return None
    if policy == QueuePolicy.FIFO:
        return min(link.queue, key=lambda packet_id: (
            runtime.packets[packet_id].queued_at,
            runtime.packets[packet_id].enqueue_sequence,
        ))
    return min(link.queue, key=lambda packet_id: (
        runtime.packets[packet_id].spec.priority,
        runtime.packets[packet_id].queued_at,
        runtime.packets[packet_id].enqueue_sequence,
    ))


def select_next(runtime: NetworkRuntime, link_id: str) -> str | None:
    """Select a waiting packet using the configured FIFO/priority policy.

    Raises ValueError for invalid topology, registry/queue ownership, generations,
    packet phases, or an unknown link. Does not mutate or reserve any work.
    """
    return _select(runtime, link_id, None)


def select_fifo(runtime: NetworkRuntime, link_id: str) -> str | None:
    """Select by (link enqueue time, enqueue sequence); require FIFO config."""
    return _select(runtime, link_id, QueuePolicy.FIFO)


def select_priority(runtime: NetworkRuntime, link_id: str) -> str | None:
    """Select by (priority, enqueue time, sequence); require PRIORITY config."""
    return _select(runtime, link_id, QueuePolicy.PRIORITY)
