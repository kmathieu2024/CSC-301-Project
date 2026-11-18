"""Pure directed shortest paths over validated stable network boundaries.

Costs are exact sums of the configured numbers (including the exact binary value
of floats). Fraction prevents overflow and lost positive increments in path-cost
comparisons. No epsilon, rounding, congestion weights, or hop-limit pruning is
used. Hop limits govern packet forwarding, not topology reachability.
"""

from fractions import Fraction
from heapq import heappop, heappush

from .models import Availability, LinkConfig, NetworkRuntime
from .validation import _text, validate_runtime_state


def shortest_path(
    runtime: NetworkRuntime, source: str, destination: str,
) -> tuple[str, ...] | None:
    """Return link IDs, () for an available local destination, or None if unreachable.

    Dijkstra orders labels by (total cost, complete link-ID sequence). Positive
    costs make every predecessor cheaper than its successor, including when
    costs would overflow or disappear with floating-point summation. Only UP
    nodes and UP directed links participate. Unknown endpoints/malformed runtime
    raise ValueError; known DOWN endpoints are unreachable, even when equal.

    Inputs must be stable and consistent under Phase 1 validation. A future
    failure transition must detach/finalize affected work before routing against
    the new availability state. Results reserve nothing: reselect if it changes.
    No config, queue, packet, callback, counter, or clock is mutated.
    """
    validate_runtime_state(runtime)
    for name, value in (("source", source), ("destination", destination)):
        _text(value, name)
        if value not in runtime.nodes:
            raise ValueError(f"Unknown routing {name}: {value}")
    if any(runtime.nodes[node].state != Availability.UP
           for node in (source, destination)):
        return None
    if source == destination:
        return ()

    outgoing: dict[str, list[LinkConfig]] = {}
    for link in runtime.links.values():
        config = link.config
        if (link.state == Availability.UP
                and runtime.nodes[config.source].state == Availability.UP
                and runtime.nodes[config.destination].state == Availability.UP):
            outgoing.setdefault(config.source, []).append(config)
    for links in outgoing.values():
        links.sort(key=lambda link: link.link_id)

    best = {source: (Fraction(0), ())}
    frontier = [(Fraction(0), (), source)]
    while frontier:
        cost, path, node = heappop(frontier)
        if best[node] != (cost, path):
            continue
        if node == destination:
            return path
        for link in outgoing.get(node, ()):
            candidate = cost + Fraction(link.routing_cost), path + (link.link_id,)
            previous = best.get(link.destination)
            if previous is None or candidate < previous:
                best[link.destination] = candidate
                heappush(frontier, (*candidate, link.destination))
    return None


def next_hop(
    runtime: NetworkRuntime, source: str, destination: str,
) -> str | None:
    """Return the first link ID, or None for local/unreachable destinations.

    Use shortest_path to distinguish local delivery (()) from unreachable (None).
    This selects only a link; it does not enqueue, reroute, or consume hop budget.
    """
    path = shortest_path(runtime, source, destination)
    return path[0] if path else None
