"""Network JSON conversion, atomic Flask candidates, and deterministic controls."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
import math
from pathlib import Path
from typing import Any

import pytest
from flask.testing import FlaskClient

import app as simulator_api
from src.managers.network import NetworkRequestSpec, QueuePolicy, TopologyAction
from src.managers.network.json_inputs import (
    parse_network_requests, parse_network_settings,
)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> FlaskClient:
    """Keep the application's accepted engine isolated between API cases."""
    monkeypatch.setattr(simulator_api, "engine", None)
    return simulator_api.app.test_client()


def workload() -> dict[str, Any]:
    """One 100-byte send: transmission 0-1, propagation 1-2, CPU 2-3."""
    return {
        "algorithm": "FCFS", "configuration": {}, "memory": {"enabled": False},
        "network": {
            "nodes": [{"node_id": node} for node in ("a", "b", "c", "d")],
            "links": [{"link_id": "ab", "source": "a", "destination": "b",
                       "bandwidth_bytes_per_time_unit": 100,
                       "propagation_delay": 1, "queue_capacity_packets": 4}],
        },
        "processes": [{"pid": "P", "arrival_time": 0, "burst_time": 1,
                       "network_requests": {"0": {
                           "source": "a", "destination": "b", "size_bytes": 100,
                       }}}],
    }


def event(action: str, timestamp: float, **target: Any) -> dict[str, Any]:
    """Canonical JSON control, without accepted aliases."""
    return {"action": action, "timestamp": timestamp, **target}


def test_json_conversion_is_typed_defensive_and_immutable() -> None:
    data = workload()
    data["network"]["events"] = [event("LINK_DOWN", 0.5, link_id="ab")]
    before = deepcopy(data)
    settings = parse_network_settings(data["network"])
    requests = parse_network_requests(data["processes"][0]["network_requests"],
                                      1, settings)
    assert data == before
    assert settings.config.hop_limit == 16
    assert settings.config.links[0].routing_cost == 1
    assert settings.config.links[0].queue_policy == QueuePolicy.FIFO
    assert requests == {0: NetworkRequestSpec(source="a", destination="b",
                                            size_bytes=100)}
    assert settings.events[0].action == TopologyAction.LINK_DOWN
    with pytest.raises(FrozenInstanceError):
        settings.enabled = False
    data["network"]["nodes"].clear()
    data["network"]["events"].clear()
    assert len(settings.config.nodes) == 4
    assert len(settings.events) == 1


@pytest.mark.parametrize("multi", [False, True])
def test_start_step_run_reset_and_strict_results(
    client: FlaskClient, multi: bool,
) -> None:
    data = workload()
    if multi:
        data["network"]["links"].append({
            **data["network"]["links"][0], "link_id": "bc", "source": "b",
            "destination": "c",
        })
        data["processes"][0]["network_requests"]["0"]["destination"] = "c"
    start = client.post("/api/start", json=data)
    assert start.status_code == 200
    assert start.get_json()["network_metrics"]["throughput"] is None
    assert isinstance(simulator_api.engine.processes[0].network_requests[0],
                      NetworkRequestSpec)
    assert client.post("/api/step").status_code == 200
    result = client.post("/api/run").get_json()
    assert result["current_time"] == (5 if multi else 3)
    assert result["processes"][0]["blocked_time"] == (4 if multi else 2)
    assert result["network_metrics"]["delivered"] == 1
    assert result["network_packets"]["packet-0"]["path"] == (
        ["ab", "bc"] if multi else ["ab"]
    )
    assert len(result["network_timeline"]) == (4 if multi else 2)
    json.dumps(result, allow_nan=False)
    assert "generation" not in json.dumps(result["network_packets"])
    assert "operation_token" not in json.dumps(result)
    assert "token" not in result["network_timeline"][0]
    assert client.post("/api/step").get_json() == result
    reset = client.post("/api/reset").get_json()
    assert reset["network_packets"] == {}
    assert client.post("/api/run").get_json() == result


@pytest.mark.parametrize("policy,order", [
    ("FIFO", ["P0", "P1", "P2"]), ("PRIORITY", ["P0", "P2", "P1"]),
])
def test_fifo_priority_contention_through_api(
    client: FlaskClient, policy: str, order: list[str],
) -> None:
    data = workload()
    data["network"]["links"][0].update(queue_policy=policy, propagation_delay=0)
    data["processes"] = [{
        "pid": f"P{i}", "arrival_time": 0, "burst_time": 1,
        "network_requests": {"0": {"source": "a", "destination": "b",
                                     "size_bytes": 100, "priority": priority}},
    } for i, priority in enumerate((9, 3, 0))]
    assert client.post("/api/start", json=data).status_code == 200
    result = client.post("/api/run").get_json()
    outcomes = sorted(result["network_outcomes"].values(),
                      key=lambda item: (item["timestamp"], item["packet_id"]))
    assert [item["pid"] for item in outcomes] == order
    assert result["network_metrics"]["delivered"] == 3
    assert result["current_time"] == 4


@pytest.mark.parametrize("capacity", [0, 1])
def test_buffer_capacity_drops_and_immediate_outcomes(
    client: FlaskClient, capacity: int,
) -> None:
    data = workload()
    data["algorithm"] = "RR"
    data["configuration"]["time_quantum"] = 1
    data["network"]["links"][0]["queue_capacity_packets"] = capacity
    data["processes"] = [{**deepcopy(data["processes"][0]), "pid": f"P{i}"}
                         for i in range(3)]
    assert client.post("/api/start", json=data).status_code == 200
    result = client.post("/api/run").get_json()
    assert result["network_metrics"]["delivered"] == capacity + 1
    assert result["network_metrics"]["dropped"] == 2 - capacity
    assert sum(n["reason"] == "QUEUE_FULL"
               for n in result["network_outcomes"].values()) == 2 - capacity


@pytest.mark.parametrize("when,tx,prop", [(0.5, 0.5, 0), (1.5, 1, 0.5)])
@pytest.mark.parametrize("target", ["link", "node"])
def test_scheduled_failure_and_recovery_never_revives_packet(
    client: FlaskClient, when: float, tx: float, prop: float, target: str,
) -> None:
    data = workload()
    identity = {"link_id": "ab"} if target == "link" else {"node_id": "a"}
    prefix = "LINK" if target == "link" else "NODE"
    # Deliberately unsorted input: event time, not list order, determines execution.
    data["network"]["events"] = [event(prefix + "_UP", 4, **identity),
                                  event(prefix + "_DOWN", when, **identity)]
    assert client.post("/api/start", json=data).status_code == 200
    result = client.post("/api/run").get_json()
    assert result["current_time"] == 4
    assert result["network_metrics"]["failed"] == 1
    assert result["network_metrics"]["delivered"] == 0
    assert result["network_outcomes"]["packet-0"]["reason"] == prefix + "_FAILURE"
    packet = result["network_metrics"]["packets"]["packet-0"]
    assert packet["transmission_time"] == tx and packet["propagation_time"] == prop
    assert result["processes"][0]["blocked_time"] == when
    domain = "links" if target == "link" else "nodes"
    key = "ab" if target == "link" else "a"
    assert result["network_topology"][domain][key]["state"] == "UP"
    assert result["network_topology"]["events"] == data["network"]["events"]
    client.post("/api/reset")
    assert client.post("/api/run").get_json() == result


def failure_demo() -> dict[str, Any]:
    """Hand-calculated fallback: A fails at .5, B delivers via AC/CB at 2.5."""
    return json.loads(
        Path("test_data/milestone4/network_failure.json").read_text()
    )


def test_deterministic_failure_demo_reroutes_and_matches_hand_calculation(
    client: FlaskClient,
) -> None:
    assert client.post("/api/start", json=failure_demo()).status_code == 200
    result = client.post("/api/run").get_json()
    assert result["current_time"] == 3.5
    assert result["network_metrics"]["failed"] == 1
    assert result["network_metrics"]["delivered"] == 1
    assert result["network_packets"]["packet-1"]["path"] == ["ac", "cb"]
    assert [p["blocked_time"] for p in result["processes"]] == [0.5, 2.5]
    assert [p["completion_time"] for p in result["processes"]] == [1.5, 3.5]
    assert result["network_outcomes"]["packet-0"]["timestamp"] == 0.5
    assert result["network_outcomes"]["packet-1"]["timestamp"] == 2.5
    links = result["network_metrics"]["links"]
    assert [links[key]["busy_time"] for key in ("ab", "ac", "cb")] == [0.5, 1, 1]
    assert result["network_metrics"]["delivered_payload_bytes"] == 100
    assert result["network_metrics"]["goodput"] == 100 / 3.5
    assert result["network_metrics"]["throughput"] == 1 / 3.5
    assert [links[key]["wire_bytes"] for key in ("ab", "ac", "cb")] == [50, 100, 100]
    assert (
        [links[key]["serialized_bytes"] for key in ("ab", "ac", "cb")] == [0, 100, 100]
    )
    assert [links[key]["idle_time"] for key in ("ab", "ac", "cb")] == [0.5, 2.5, 2.5]
    assert [links[key]["unavailable_time"] for key in ("ab", "ac", "cb")] == [2.5, 0, 0]
    assert links["ab"]["queue_length_integral"] == 0.5
    assert links["ab"]["average_queue_length"] == 0.5 / 3.5
    client.post("/api/reset")
    for _ in range(30):
        response = client.post("/api/step")
        assert response.status_code == 200
        if response.get_json()["current_time"] == 3.5:
            break
    else:
        raise AssertionError("Step failed to finish demonstration")
    assert client.post("/api/run").get_json() == result


# Paths select one malformed value without changing unrelated input fields.
_INVALID = [
    (("network",), None), (("network",), []),
    (("network", "enabled"), 1), (("network", "enabled"), "true"),
    (("network", "nodes"), {}), (("network", "links"), {}),
    (("network", "events"), {}), (("network", "nodes", 0), "a"),
    (("network", "nodes", 0, "node_id"), True),
    (("network", "links", 0, "source"), "missing"),
    (("network", "links", 0, "destination"), "missing"),
    (("network", "links", 0, "bandwidth_bytes_per_time_unit"), 0),
    (("network", "links", 0, "bandwidth_bytes_per_time_unit"), True),
    (("network", "links", 0, "propagation_delay"), -1),
    (("network", "links", 0, "routing_cost"), 0),
    (("network", "links", 0, "routing_cost"), False),
    (("network", "links", 0, "queue_capacity_packets"), True),
    (("network", "links", 0, "queue_capacity_packets"), -1),
    (("network", "links", 0, "queue_capacity_packets"), 1.5),
    (("network", "links", 0, "queue_capacity_packets"), 2**53),
    (("network", "links", 0, "queue_policy"), "fifo"),
    (("network", "links", 0, "queue_policy"), ["FIFO"]),
    (("network", "links", 0, "queue_policy"), {"value": "FIFO"}),
    (("network", "links", 0, "queue_policy"), True),
    (("network", "links", 0, "queue_policy"), None),
    (("network", "hop_limit"), 0), (("network", "hop_limit"), True),
    (("network", "hop_limit"), 1.5), (("network", "hop_limit"), 2**53),
    (("network", "unknown"), 1),
    (("network", "links", 0, "unknown"), 1),
    (("processes", 0, "network_requests"), []),
    (("processes", 0, "network_requests", "0"), []),
    (("processes", 0, "network_requests", "0", "source"), "missing"),
    (("processes", 0, "network_requests", "0", "destination"), "missing"),
    (("processes", 0, "network_requests", "0", "size_bytes"), True),
    (("processes", 0, "network_requests", "0", "size_bytes"), 0),
    (("processes", 0, "network_requests", "0", "size_bytes"), 1.5),
    (("processes", 0, "network_requests", "0", "size_bytes"), 2**53),
    (("processes", 0, "network_requests", "0", "priority"), True),
    (("processes", 0, "network_requests", "0", "priority"), -1),
    (("processes", 0, "network_requests", "0", "priority"), 2**53),
    (("processes", 0, "network_requests", "0", "operation"), "SEND"),
]
_INVALID += [(("network", "links", 0, field), bad)
             for field in ("bandwidth_bytes_per_time_unit", "routing_cost",
                           "propagation_delay", "queue_capacity_packets")
             for bad in (math.nan, math.inf, -math.inf)]
_INVALID += [(("processes", 0, "network_requests", "0", field), bad)
             for field in ("size_bytes", "priority")
             for bad in (math.nan, math.inf, -math.inf)]


@pytest.mark.parametrize("path,value", _INVALID)
def test_invalid_candidate_preserves_accepted_input_results_and_callbacks(
    client: FlaskClient, path: tuple[Any, ...], value: Any,
) -> None:
    assert client.post("/api/start", json=workload()).status_code == 200
    before = client.post("/api/run").get_json()
    accepted = simulator_api.engine
    queue = accepted.event_queue.copy()
    requests = accepted.processes[0].network_requests.copy()
    data = workload()
    parent = data
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    response = client.post("/api/start", json=data)
    assert response.status_code == 400
    assert response.get_json()["error"]
    assert "Traceback" not in response.get_data(as_text=True)
    assert simulator_api.engine is accepted
    assert accepted.event_queue == queue
    assert accepted.processes[0].network_requests == requests
    assert client.post("/api/run").get_json() == before


@pytest.mark.parametrize("offset", ["01", "00", "+0", "-1", "0.0", " 0", "١",
                                    "1", "9007199254740992", "9" * 5000])
def test_invalid_cpu_offset_forms(client: FlaskClient, offset: str) -> None:
    data = workload()
    data["processes"][0]["network_requests"] = {
        offset: data["processes"][0]["network_requests"]["0"],
    }
    assert client.post("/api/start", json=data).status_code == 400
    assert simulator_api.engine is None


@pytest.mark.parametrize("duplicate", ["node", "link", "offset_key", "offset_alias"])
def test_duplicate_identifiers_or_offsets(client: FlaskClient, duplicate: str) -> None:
    data = workload()
    if duplicate == "node":
        data["network"]["nodes"].append(deepcopy(data["network"]["nodes"][0]))
    elif duplicate == "link":
        data["network"]["links"].append(deepcopy(data["network"]["links"][0]))
    elif duplicate == "offset_alias":
        data["processes"][0]["network_requests"]["00"] = deepcopy(
            data["processes"][0]["network_requests"]["0"],
        )
    if duplicate == "offset_key":
        raw = json.dumps(data).replace('"0": {', '"0": {}, "0": {', 1)
        response = client.post("/api/start", data=raw, content_type="application/json")
    else:
        response = client.post("/api/start", json=data)
    assert response.status_code == 400
    assert simulator_api.engine is None


@pytest.mark.parametrize("bad_event", [
    None, [], {}, event("NO_SUCH_ACTION", 1, link_id="ab"),
    event(["LINK_DOWN"], 1, link_id="ab"), event(True, 1, link_id="ab"),
    event(None, 1, link_id="ab"), event({"x": "LINK_DOWN"}, 1, link_id="ab"),
    event("LINK_DOWN", True, link_id="ab"), event("LINK_DOWN", -1, link_id="ab"),
    event("LINK_DOWN", math.nan, link_id="ab"),
    event("LINK_DOWN", math.inf, link_id="ab"),
    event("NODE_UP", -math.inf, node_id="a"),
    event("LINK_DOWN", "1", link_id="ab"), event("LINK_DOWN", 1),
    event("LINK_UP", 1, link_id="missing"), event("NODE_DOWN", 1, node_id="missing"),
    event("LINK_DOWN", 1, node_id="a"), event("NODE_DOWN", 1, link_id="ab"),
    event("LINK_DOWN", 1, link_id="ab", node_id=None),
    {"time": 1, "type": "LINK_DOWN", "link_id": "ab"},
])
def test_malformed_topology_controls_are_atomic(
    client: FlaskClient, bad_event: Any,
) -> None:
    client.post("/api/start", json=workload())
    accepted = simulator_api.engine
    before = accepted.results()
    data = workload()
    data["network"]["events"] = [bad_event]
    response = client.post("/api/start", json=data)
    assert response.status_code == 400
    assert simulator_api.engine is accepted and accepted.results() == before


@pytest.mark.parametrize("enabled", [None, False, True])
def test_legacy_absent_disabled_and_empty_network(
    client: FlaskClient, enabled: Any,
) -> None:
    data = workload()
    data["processes"][0].pop("network_requests")
    if enabled is None:
        data.pop("network")
    else:
        data["network"] = {"enabled": enabled}
    assert client.post("/api/start", json=data).status_code == 200
    result = client.post("/api/run").get_json()
    assert result["current_time"] == 1
    assert result["network_packets"] == {}
    assert (result["network_metrics"] is not None) == (enabled is True)


@pytest.mark.parametrize("mode", [
    "absent", "disabled", "disabled_events", "top_level", "top_level_events",
])
def test_network_input_is_never_silently_dropped(
    client: FlaskClient, mode: str,
) -> None:
    data = workload()
    if mode == "absent":
        data.pop("network")
    elif mode == "disabled":
        data["network"]["enabled"] = False
    elif mode == "disabled_events":
        data["processes"][0].pop("network_requests")
        data["network"].update(
            enabled=False, events=[event("LINK_DOWN", 0, link_id="ab")],
        )
    elif mode == "top_level_events":
        data["network_events"] = []
    else:
        data["network_requests"] = {}
    assert client.post("/api/start", json=data).status_code == 400


@pytest.mark.parametrize("permission,allowed", [
    ("network:send", True), ("network:/nodes/a/to/b:send", True),
    ("network:/nodes/a/to/c:send", False), ("file:read", False),
])
def test_security_authorization_and_denial_through_existing_api(
    client: FlaskClient, permission: str, allowed: bool,
) -> None:
    data = workload()
    data["security"] = {"enabled": True,
                        "roles": [{"name": "sender", "permissions": [permission]}],
                        "users": [{"username": "P", "roles": ["sender"]}]}
    assert client.post("/api/start", json=data).status_code == 200
    result = client.post("/api/run").get_json()
    assert result["network_metrics"]["submitted"] == int(allowed)
    assert result["processes"][0]["blocked_time"] == (2 if allowed else 0)
    records = result["security_events"]
    assert len(records) == 1
    assert records[0]["resource_type"] == "network"
    assert records[0]["outcome"] == ("allowed" if allowed else "denied")
    assert list(result["network_outcomes"].values())[0]["outcome"] == (
        "DELIVERED" if allowed else "DENIED"
    )


def test_combined_managers_and_security_preserve_shared_offset_order(
    client: FlaskClient,
) -> None:
    data = workload()
    data["memory"] = {"enabled": True, "frame_count": 1,
                      "page_size": 4, "fault_latency": 1}
    data["devices"] = [{"device_id": "keyboard", "device_type": "KEYBOARD",
                        "default_service_time": 1}]
    data["security"] = {
        "enabled": True,
        "roles": [{"name": "worker", "permissions": ["file:create", "network:send"]}],
        "users": [{"username": "P", "roles": ["worker"]}],
    }
    data["processes"][0].update(
        file_requests={"0": "CREATE /once"}, blocking_requests={"0": 1},
        memory_references={"0": 0}, device_requests={"0": {
            "device_id": "keyboard", "operation": "READ",
        }},
    )
    assert client.post("/api/start", json=data).status_code == 200
    result = client.post("/api/run").get_json()
    assert result["current_time"] == 6
    assert result["processes"][0]["blocked_time"] == 5
    assert result["network_packets"]["packet-0"]["submitted_at"] == 3
    assert result["memory_metrics"]["references"] == 1
    assert result["device_metrics"]["devices"]["keyboard"]["submitted"] == 1
    assert [(e["resource_type"], e["timestamp"])
            for e in result["security_events"]] == [
        ("file", 0), ("network", 3),
    ]
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("field", ["bandwidth_bytes_per_time_unit", "link_id",
                                   "source", "destination"])
def test_missing_required_link_fields_rejected(client: FlaskClient, field: str) -> None:
    data = workload()
    del data["network"]["links"][0][field]
    assert client.post("/api/start", json=data).status_code == 400


def test_optional_defaults_and_recovery_allow_only_fresh_requests(
    client: FlaskClient,
) -> None:
    data = workload()
    link = data["network"]["links"][0]
    for field in ("propagation_delay", "queue_capacity_packets"):
        del link[field]
    data["network"]["events"] = [event("LINK_DOWN", 0.5, link_id="ab"),
                                  event("LINK_UP", 2, link_id="ab")]
    later = deepcopy(data["processes"][0])
    later.update(pid="later", arrival_time=3)
    data["processes"].append(later)
    assert client.post("/api/start", json=data).status_code == 200
    result = client.post("/api/run").get_json()
    assert result["network_metrics"]["failed"] == 1
    assert result["network_metrics"]["delivered"] == 1
    assert result["network_packets"]["packet-0"]["state"] == "FAILED"
    assert result["network_outcomes"]["packet-1"]["timestamp"] == 4
    assert result["current_time"] == 5
    assert result["network_topology"]["links"]["ab"]["config"]["queue_policy"] == "FIFO"


@pytest.mark.parametrize("field,bad", [("routing_cost", 0),
                                      ("queue_policy", ["FIFO"]),
                                      ("bandwidth_bytes_per_time_unit", True)])
def test_disabled_settings_are_still_validated(
    client: FlaskClient, field: str, bad: Any,
) -> None:
    data = workload()
    data["processes"][0].pop("network_requests")
    data["network"]["enabled"] = False
    data["network"]["links"][0][field] = bad
    assert client.post("/api/start", json=data).status_code == 400


def test_rejected_candidate_preserves_active_accepted_callbacks(
    client: FlaskClient,
) -> None:
    assert client.post("/api/start", json=workload()).status_code == 200
    client.post("/api/step")
    before = client.post("/api/step").get_json()
    engine = simulator_api.engine
    queue = engine.event_queue.copy()
    operations = engine.network_manager.runtime.operations
    data = workload()
    data["network"]["events"] = [event("LINK_DOWN", 1, link_id="missing")]
    assert client.post("/api/start", json=data).status_code == 400
    assert simulator_api.engine is engine
    assert engine.event_queue == queue
    assert engine.network_manager.runtime.operations == operations
    assert engine.results() == before
    assert client.post("/api/run").get_json()["current_time"] == 3


def test_unrepresentable_rates_are_null_and_strict_json_compliant(
    client: FlaskClient,
) -> None:
    data = workload()
    data["processes"][0]["burst_time"] = 5e-324
    data["processes"][0]["network_requests"]["0"]["destination"] = "a"
    assert client.post("/api/start", json=data).status_code == 200
    response = client.post("/api/run")
    assert response.status_code == 200
    result = response.get_json()
    assert result["current_time"] == 5e-324
    assert result["network_metrics"]["delivered"] == 1
    assert result["network_metrics"]["throughput"] is None
    assert result["network_metrics"]["goodput"] is None
    assert "Infinity" not in response.get_data(as_text=True)
    assert "NaN" not in response.get_data(as_text=True)
    json.dumps(result, allow_nan=False)


def test_large_intermediate_partial_snapshot_keeps_finite_queue_average(
    client: FlaskClient,
) -> None:
    data = workload()
    data["network"]["links"][0].update(
        bandwidth_bytes_per_time_unit=1e-308, propagation_delay=0,
    )
    data["processes"][0]["network_requests"]["0"]["size_bytes"] = 1
    data["processes"] = [{**deepcopy(data["processes"][0]), "pid": f"P{i}"}
                         for i in range(5)]
    data["processes"].append({"pid": "late", "arrival_time": 5e307, "burst_time": 1})
    assert client.post("/api/start", json=data).status_code == 200
    for _ in range(15):
        response = client.post("/api/step")
        assert response.status_code == 200
        result = response.get_json()
        if result["current_time"] == 5e307:
            break
    else:
        raise AssertionError("Did not reach partial snapshot")
    link = result["network_metrics"]["links"]["ab"]
    assert link["queue_length_integral"] is None
    assert link["average_queue_length"] == 4
    assert link["utilization"] == 1
    json.dumps(result, allow_nan=False)


def test_runtime_timestamp_failure_is_reset_required_without_orphaned_packets(
    client: FlaskClient,
) -> None:
    data = workload()
    data["processes"][0]["arrival_time"] = 1e308
    assert client.post("/api/start", json=data).status_code == 200
    response = client.post("/api/run")
    assert response.status_code == 400
    body = response.get_json()
    assert body["code"] == "simulation_failed" and body["reset_required"] is True
    assert "Traceback" not in response.get_data(as_text=True)
    assert simulator_api.engine.network_manager.runtime.operations == {}
    assert simulator_api.engine._network_waiting == {}
    assert client.post("/api/step").status_code == 400
    assert client.post("/api/run").status_code == 400
    assert client.post("/api/reset").get_json()["execution_error"] is None
    data["processes"][0]["arrival_time"] = 0
    assert client.post("/api/start", json=data).status_code == 200
    assert client.post("/api/run").get_json()["current_time"] == 3


@pytest.mark.parametrize("enabled", [False, True])
def test_security_disabled_and_default_deny_remain_compatible(
    client: FlaskClient, enabled: bool,
) -> None:
    data = workload()
    data["security"] = {"enabled": enabled}
    data["network"]["events"] = [event("LINK_UP", 4, link_id="ab")]
    assert client.post("/api/start", json=data).status_code == 200
    result = client.post("/api/run").get_json()
    assert result["current_time"] == 4
    assert result["network_metrics"]["submitted"] == int(not enabled)
    assert len(result["security_events"]) == int(enabled)
    assert result["security_enabled"] == enabled
    if enabled:
        assert list(result["network_outcomes"].values())[0]["outcome"] == "DENIED"


def test_invalid_settings_never_construct_a_network_manager(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("NetworkManager constructed before validation")

    monkeypatch.setattr(simulator_api, "NetworkManager", unexpected)
    data = workload()
    data["network"]["links"][0]["routing_cost"] = -1
    assert client.post("/api/start", json=data).status_code == 400


@pytest.mark.parametrize("field", [
    "arrival_time", "time_quantum", "context_switch_cost",
])
@pytest.mark.parametrize("number", [math.nan, math.inf, -math.inf])
def test_network_candidate_rejects_nonfinite_json_numbers_in_shared_settings(
    client: FlaskClient, field: str, number: float,
) -> None:
    data = workload()
    if field == "arrival_time":
        data["processes"][0][field] = number
    else:
        data["configuration"][field] = number
    response = client.post("/api/start", json=data)
    assert response.status_code == 400
    assert simulator_api.engine is None


@pytest.mark.parametrize("reverse_link", [False, True])
def test_directed_connectivity_requires_explicit_reverse_link(
    client: FlaskClient, reverse_link: bool,
) -> None:
    data = workload()
    data["processes"][0]["network_requests"]["0"].update(source="b", destination="a")
    if reverse_link:
        data["network"]["links"].append({
            **data["network"]["links"][0], "link_id": "ba",
            "source": "b", "destination": "a",
        })
    assert client.post("/api/start", json=data).status_code == 200
    result = client.post("/api/run").get_json()
    reason = result["network_outcomes"]["packet-0"]["reason"]
    assert reason == ("DELIVERED" if reverse_link else "NO_ROUTE")


def test_scheduled_intermediate_node_failure_reroutes_upstream_waiter(
    client: FlaskClient,
) -> None:
    data = workload()
    direct = data["network"]["links"][0]
    direct.update(routing_cost=3, propagation_delay=0)
    data["network"]["links"] += [
        {**direct, "link_id": "ac", "destination": "c", "routing_cost": 1},
        {**direct, "link_id": "cb", "source": "c", "routing_cost": 1},
    ]
    data["network"]["events"] = [event("NODE_DOWN", 0.5, node_id="c"),
                                  event("NODE_UP", 2, node_id="c")]
    data["processes"] = [{**deepcopy(data["processes"][0]), "pid": pid}
                         for pid in ("A", "B")]
    assert client.post("/api/start", json=data).status_code == 200
    result = client.post("/api/run").get_json()
    assert result["network_metrics"]["failed"] == 1
    assert result["network_metrics"]["delivered"] == 1
    assert result["network_packets"]["packet-1"]["path"] == ["ab"]
    assert [p["blocked_time"] for p in result["processes"]] == [0.5, 1.5]
    assert result["current_time"] == 2.5
