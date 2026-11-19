"""Candidate validation shares Start rules without changing an accepted run."""

from copy import deepcopy
from itertools import product

import pytest

import app as simulator_api
from tests.test_network_api import workload


@pytest.fixture
def client(monkeypatch):
    """Give each test a fresh API engine."""
    monkeypatch.setattr(simulator_api, "engine", None)
    return simulator_api.app.test_client()


@pytest.mark.parametrize("invalid", [False, True])
def test_validate_does_not_inspect_or_mutate_accepted_run(
    client, monkeypatch, invalid: bool,
) -> None:
    data = workload()
    data["security"] = {"enabled": True, "roles": [
        {"name": "sender", "permissions": ["network:send"]}],
        "users": [{"username": "P", "roles": ["sender"]}]}
    assert client.post("/api/start", json=data).status_code == 200
    client.post("/api/step")
    client.post("/api/step")
    accepted = simulator_api.engine
    before = deepcopy((accepted.event_queue, accepted.events, accepted.timeline,
                       accepted.network_manager.snapshot(accepted.current_time),
                       accepted.security_manager.audit_log,
                       [vars(p) for p in accepted.processes]))

    def forbidden():
        raise AssertionError("Validation must not read accepted results")

    monkeypatch.setattr(accepted, "results", forbidden)
    candidate = deepcopy(data)
    if invalid:
        candidate["security"]["users"][0]["roles"] = ["missing"]
    response = client.post("/api/validate", json=candidate)
    assert response.status_code == (400 if invalid else 200)
    if invalid:
        assert response.get_json()["code"] == "invalid_workload"
        assert "role" in response.get_json()["error"].lower()
    else:
        assert response.get_json() == {"valid": True, "security_enabled": True}
    assert simulator_api.engine is accepted
    assert before == (accepted.event_queue, accepted.events, accepted.timeline,
                      accepted.network_manager.snapshot(accepted.current_time),
                      accepted.security_manager.audit_log,
                      [vars(p) for p in accepted.processes])


@pytest.mark.parametrize("security", [None, {}, {"enabled": False},
    {"enabled": False, "roles": "unused", "users": 42}])
def test_validate_preserves_disabled_policy_semantics(client, security) -> None:
    data = workload()
    data["security"] = security
    assert client.post("/api/validate", json=data).get_json() == {
        "valid": True, "security_enabled": False,
    }
    assert simulator_api.engine is None
    assert client.post("/api/start", json=data).status_code == 200


@pytest.mark.parametrize("problem", ["device", "memory", "network", "process",
                                     "permission", "duplicate_user", "enabled"])
def test_validate_and_start_share_candidate_rejection(client, problem: str) -> None:
    data = workload()
    if problem == "device":
        data["devices"] = [{"device_id": "d", "device_type": "INVALID"}]
    elif problem == "memory":
        data["memory"] = {"enabled": True, "frame_count": 0}
    elif problem == "network":
        data["network"]["links"][0]["destination"] = "missing"
    elif problem == "process":
        data["processes"][0]["burst_time"] = -1
    elif problem == "permission":
        data["security"] = {"enabled": True, "roles": [
            {"name": "bad", "permissions": ["bad"]}]}
    elif problem == "duplicate_user":
        data["security"] = {"enabled": True, "users": [
            {"username": "P"}, {"username": "P"}]}
    else:
        data["security"] = {"enabled": 1}
    validation = client.post("/api/validate", json=data)
    start = client.post("/api/start", json=data)
    assert validation.status_code == start.status_code == 400
    assert validation.get_json()["error"] == start.get_json()["error"]
    assert simulator_api.engine is None


def test_validate_rejects_duplicate_keys_and_non_workload_documents(client) -> None:
    for raw in ('{"security":{}}', '{"algorithm":"FCFS","algorithm":"RR"}',
                '[]', 'not JSON'):
        response = client.post("/api/validate", data=raw)
        assert response.status_code == 400
        assert response.get_json()["code"] == "invalid_workload"


def test_validate_never_calls_execution_or_results_methods(client, monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("Validation is construction only")

    for name in ("results", "step", "run"):
        monkeypatch.setattr(simulator_api.SimulationEngine, name, forbidden)
    assert client.post("/api/validate", json=workload()).status_code == 200
    assert simulator_api.engine is None


@pytest.mark.parametrize("file_allowed,device_allowed,network_allowed",
                         list(product([False, True], repeat=3)))
def test_six_manager_policy_controls_each_request_once_and_replays(
    client, file_allowed: bool, device_allowed: bool, network_allowed: bool,
) -> None:
    data = workload()
    data["memory"] = {"enabled": True, "frame_count": 1, "fault_latency": 1}
    data["devices"] = [{"device_id": "kbd", "device_type": "KEYBOARD",
                        "default_service_time": 1}]
    permissions = [p for p, allowed in zip(
        ["file:create", "device:read", "network:send"],
        [file_allowed, device_allowed, network_allowed],
    ) if allowed]
    data["security"] = {"enabled": True, "roles": [
        {"name": "worker", "permissions": permissions}],
        "users": [{"username": "P", "roles": ["worker"]}]}
    data["processes"][0].update(
        file_requests={"0": "CREATE /once"}, blocking_requests={"0": 1},
        memory_references={"0": 0}, device_requests={"0": {
            "device_id": "kbd", "operation": "READ"}},
    )
    assert client.post("/api/start", json=data).status_code == 200
    r = client.post("/api/run").get_json()
    assert r["current_time"] == 3 + int(device_allowed) + 2 * int(network_allowed)
    assert r["processes"][0]["blocked_time"] == r["current_time"] - 1
    assert [(e["resource_type"], e["outcome"]) for e in r["security_events"]] == [
        (resource, "allowed" if allowed else "denied") for resource, allowed in zip(
            ["file", "device", "network"],
            [file_allowed, device_allowed, network_allowed],
        )
    ]
    assert r["device_metrics"]["devices"]["kbd"]["submitted"] == int(device_allowed)
    assert r["network_metrics"]["submitted"] == int(network_allowed)
    if not device_allowed:
        assert r["device_requests"] == {}
        assert not r["devices"]["kbd"]["queue"]
        assert r["devices"]["kbd"]["active_operation"] is None
        assert next(iter(r["device_outcomes"].values()))["outcome"] == "DENIED"
    if network_allowed:
        submitted = r["network_packets"]["packet-0"]["submitted_at"]
        assert submitted == 2 + int(device_allowed)
    initial_audit = len(r["audit_log"])
    assert client.post("/api/reset").status_code == 200
    replay = client.post("/api/run").get_json()
    assert replay["current_time"] == r["current_time"]
    assert replay["device_outcomes"] == r["device_outcomes"]
    assert len(replay["security_events"]) == 6
    assert len(replay["audit_log"]) == initial_audit + 3
    denied = 3 - sum((file_allowed, device_allowed, network_allowed))
    assert len(replay["security_alerts"]) == 2 * denied
