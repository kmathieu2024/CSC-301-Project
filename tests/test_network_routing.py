"""Deterministic directed routing, checked independently against simple paths."""

from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from itertools import permutations

import pytest

from src.managers.network import (
    Availability, LinkConfig, NetworkConfig, NetworkRuntime, NodeConfig,
    create_network_runtime, next_hop, shortest_path,
)


def graph(edges: list[tuple[str, str, str, float]]) -> NetworkRuntime:
    """Build a topology with stable identities and intentionally unsorted inputs."""
    return create_network_runtime(NetworkConfig(
        nodes=tuple(NodeConfig(node_id=node) for node in ("s", "a", "b", "t")),
        links=tuple(LinkConfig(
            link_id=identity, source=source, destination=destination,
            routing_cost=cost, bandwidth_bytes_per_time_unit=1,
        ) for identity, source, destination, cost in edges),
    ))


@pytest.mark.parametrize("edges,expected", [
    ([], None),
    ([("direct", "s", "t", 1)], ("direct",)),
    ([("sa", "s", "a", 1), ("at", "a", "t", 1)], ("sa", "at")),
    ([("direct", "s", "t", 3), ("sa", "s", "a", 1),
      ("at", "a", "t", 1)], ("sa", "at")),
    ([("direct", "s", "t", 1), ("sa", "s", "a", 1),
      ("at", "a", "t", 1)], ("direct",)),
    ([("z", "s", "t", 2), ("a", "s", "a", 1),
      ("y", "a", "t", 1)], ("a", "y")),
    ([("sa", "s", "a", 1), ("z", "a", "t", 2),
      ("a", "a", "b", 1), ("zz", "b", "t", 1)], ("sa", "a", "zz")),
    ([("a", "s", "a", 1), ("z", "a", "t", 1),
      ("b", "s", "b", 1), ("aa", "b", "t", 1)], ("a", "z")),
    ([("b", "s", "t", 1), ("a", "s", "t", 1)], ("a",)),
    ([("loop", "s", "s", 1), ("sa", "s", "a", 1),
      ("as", "a", "s", 1), ("at", "a", "t", 1)], ("sa", "at")),
])
def test_paths_costs_and_complete_lexicographic_ties(
    edges: list[tuple[str, str, str, float]], expected: tuple[str, ...] | None,
) -> None:
    runtime = graph(edges)
    before = deepcopy(runtime)
    for _ in range(3):
        assert shortest_path(runtime, "s", "t") == expected
        assert next_hop(runtime, "s", "t") == (expected[0] if expected else None)
    assert runtime == before


def test_directed_links_require_explicit_reverse_connectivity() -> None:
    runtime = graph([("st", "s", "t", 1)])
    assert shortest_path(runtime, "t", "s") is None
    runtime = graph([("st", "s", "t", 1), ("ts", "t", "s", 3)])
    assert shortest_path(runtime, "t", "s") == ("ts",)


@pytest.mark.parametrize("failed", ["sa", "at", "a", "s", "t"])
def test_unavailable_nodes_and_links(failed: str) -> None:
    runtime = graph([("sa", "s", "a", 1), ("at", "a", "t", 1),
                     ("fallback", "s", "t", 4)])
    if failed in runtime.links:
        runtime.links[failed].state = Availability.DOWN
    else:
        runtime.nodes[failed].state = Availability.DOWN
    before = deepcopy(runtime)
    expected = None if failed in ("s", "t") else ("fallback",)
    assert shortest_path(runtime, "s", "t") == expected
    assert runtime == before


def test_local_delivery_requires_available_known_node() -> None:
    runtime = graph([])
    assert shortest_path(runtime, "s", "s") == ()
    assert next_hop(runtime, "s", "s") is None
    runtime.nodes["s"].state = Availability.DOWN
    assert shortest_path(runtime, "s", "s") is None


@pytest.mark.parametrize("bad", ["unknown", "", True, None, [], {}])
@pytest.mark.parametrize("endpoint", ["source", "destination"])
def test_invalid_endpoints_raise_value_error(bad: object, endpoint: str) -> None:
    kwargs = {"source": "s", "destination": "t", endpoint: bad}
    with pytest.raises(ValueError):
        shortest_path(graph([]), **kwargs)


@pytest.mark.parametrize("problem", ["missing_node", "wrong_state", "wrong_config"])
def test_malformed_runtime_is_not_reported_as_unreachable(problem: str) -> None:
    runtime = graph([("st", "s", "t", 1)])
    if problem == "missing_node":
        runtime.nodes.pop("s")
    elif problem == "wrong_state":
        runtime.links["st"].state = "DOWN"
    else:
        runtime.links["st"].config = replace(runtime.links["st"].config, routing_cost=0)
    with pytest.raises(ValueError):
        shortest_path(runtime, "s", "t")


@pytest.mark.parametrize("cost", [1e308, 5e-324])
def test_cost_sums_do_not_overflow_or_underflow(cost: float) -> None:
    runtime = graph([("z", "s", "a", cost), ("at", "a", "t", cost),
                     ("a", "s", "b", cost), ("bt", "b", "t", cost)])
    assert shortest_path(runtime, "s", "t") == ("a", "bt")


def test_positive_increment_is_not_lost_at_large_cost() -> None:
    runtime = graph([("a", "s", "a", 1e308), ("at", "a", "t", 1),
                     ("z", "s", "t", 1e308)])
    assert shortest_path(runtime, "s", "t") == ("z",)


def test_float_costs_use_exact_configured_values_not_epsilon_ties() -> None:
    runtime = graph([("a", "s", "a", 0.1), ("at", "a", "t", 0.2),
                     ("z", "s", "t", 0.3)])
    assert shortest_path(runtime, "s", "t") == ("z",)


def test_routing_does_not_prune_by_packet_hop_limit() -> None:
    runtime = graph([("sa", "s", "a", 1), ("at", "a", "t", 1)])
    runtime.config = replace(runtime.config, hop_limit=1)
    assert shortest_path(runtime, "s", "t") == ("sa", "at")


def test_input_permutations_match_exhaustive_simple_path_oracle() -> None:
    edges = [("sa", "s", "a", 1), ("at", "a", "t", 2),
             ("sb", "s", "b", 2), ("bt", "b", "t", 1),
             ("ab", "a", "b", 1)]
    candidates = []

    def visit(node: str, seen: set[str], cost: Fraction,
              path: tuple[str, ...]) -> None:
        if node == "t":
            candidates.append((cost, path))
            return
        for identity, source, destination, weight in edges:
            if source == node and destination not in seen:
                visit(destination, seen | {destination}, cost + Fraction(weight),
                      path + (identity,))

    visit("s", {"s"}, Fraction(0), ())
    expected = min(candidates)[1]
    for ordering in permutations(edges):
        runtime = graph(list(ordering))
        runtime.nodes = dict(reversed(tuple(runtime.nodes.items())))
        runtime.links = dict(reversed(tuple(runtime.links.items())))
        before = deepcopy(runtime)
        assert shortest_path(runtime, "s", "t") == expected
        assert runtime == before
