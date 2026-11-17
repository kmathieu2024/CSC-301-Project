import pytest

import app as simulator_api


class KnownInputGap(AssertionError):
    pass


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(simulator_api, "engine", None)
    return simulator_api.app.test_client()


def scenario(**requests):
    return {
        "algorithm": "fcfs",
        "configuration": {},
        "memory": {"enabled": True},
        "processes": [
            {"pid": "P1", "arrival_time": 0, "burst_time": 3, **requests}
        ],
    }


@pytest.mark.parametrize(
    "field, first, second",
    [
        ("memory_references", 0, 4),
        pytest.param(
            "file_requests", "CREATE first.txt", "CREATE second.txt",
            marks=pytest.mark.xfail(
                strict=True, raises=KnownInputGap,
                reason="Equivalent offset keys silently overwrite a request",
            ),
        ),
    ],
)
def test_equivalent_request_offsets_are_rejected(client, field, first, second):
    response = client.post(
        "/api/start", json=scenario(**{field: {"0": first, "00": second}})
    )

    if response.status_code == 200:
        process = simulator_api.engine.processes[0]
        if getattr(process, field) == {0: second}:
            raise KnownInputGap(f"{field}: accepted the input and kept only offset 0")

    assert response.status_code == 400
    assert response.get_json()["error"]
    assert simulator_api.engine is None


def test_blocking_requests_reach_the_engine(client):
    response = client.post("/api/start", json=scenario(blocking_requests={"1": 3}))
    assert response.status_code == 200
    process = simulator_api.engine.processes[0]

    response = client.post("/api/run")
    assert response.status_code == 200
    result = response.get_json()
    transitions = [
        (event["event_type"], event["previous_state"], event["new_state"], event["timestamp"])
        for event in result["events"]
        if event["entity_id"] == "P1" and event["event_type"] in {"block", "unblock"}
    ]

    assert process.blocking_requests == {1: 3}
    assert transitions == [
        ("block", "RUNNING", "WAITING", 1),
        ("unblock", "WAITING", "READY", 4),
    ]
    assert process.blocked_time == 3


@pytest.mark.parametrize("requests", [
    {"01": 2}, {"3": 1}, {"0": 0}, {"0": True}, [],
])
def test_invalid_blocking_requests_are_rejected(client, requests):
    response = client.post("/api/start", json=scenario(blocking_requests=requests))
    assert response.status_code == 400
    assert simulator_api.engine is None


@pytest.mark.parametrize("duration", [True, False, "2", None, 0, -1, float("nan"), float("inf"), -float("inf")])
def test_invalid_blocking_durations_are_rejected(client, duration):
    with pytest.raises(ValueError, match="Blocking duration must be a positive finite number"):
        simulator_api.validate_blocking_requests({"0": duration}, 3)
    response = client.post("/api/start", json=scenario(blocking_requests={"0": duration}))
    assert response.status_code == 400
    assert response.get_json()["error"] == "Blocking duration must be a positive finite number"
    assert simulator_api.engine is None


@pytest.mark.parametrize("duration", [1, 0.5])
def test_positive_blocking_durations_are_accepted(client, duration):
    response = client.post("/api/start", json=scenario(blocking_requests={"0": duration}))
    assert response.status_code == 200
    assert simulator_api.engine.processes[0].blocking_requests == {0: duration}


def test_blocking_and_memory_requests_share_a_workload(client):
    workload = scenario(blocking_requests={"1": 3}, memory_references={"0": 0})
    workload["memory"]["fault_latency"] = 2
    assert client.post("/api/start", json=workload).status_code == 200
    result = client.post("/api/run").get_json()
    assert result["memory_metrics"]["faults"] == 1
    assert simulator_api.engine.processes[0].blocked_time == 5


def test_security_policy_is_loaded_and_authorizes_file_request(client):
    payload = scenario(file_requests={"0": "CREATE /api-note.txt"})
    payload["security"] = {
        "enabled": True,
        "roles": [{"name": "creator", "permissions": ["file:create"]}],
        "users": [{"username": "P1", "roles": ["creator"]}],
    }

    started = client.post("/api/start", json=payload)
    assert started.status_code == 200
    assert started.get_json()["security_enabled"] is True

    response = client.post("/api/run")
    assert response.status_code == 200
    results = response.get_json()
    assert results["security_users"] == [
        {"username": "P1", "roles": ["creator"], "active": True}
    ]
    assert len(results["security_events"]) == 1
    assert results["security_events"][0]["outcome"] == "allowed"
    assert any(event["manager"] == "security" for event in results["events"])
    assert results["audit_log"]


def test_security_policy_denies_unassigned_process_by_default(client):
    payload = scenario(file_requests={"0": "CREATE /blocked.txt"})
    payload["security"] = {
        "enabled": True,
        "roles": [{"name": "reader", "permissions": ["file:read"]}],
        "users": [],
    }

    assert client.post("/api/start", json=payload).status_code == 200
    results = client.post("/api/run").get_json()
    assert results["security_enabled"] is True
    assert results["security_users"] == [
        {"username": "P1", "roles": [], "active": True}
    ]
    assert results["security_alerts"][0]["outcome"] == "denied"
    assert results["security_alerts"][0]["severity"] == "warning"
    file_event = next(event for event in results["events"] if event["manager"] == "file")
    assert file_event["outcome"] == "error"
    assert "not authorized" in file_event["message"].lower()


def test_security_policy_input_must_be_an_object(client):
    payload = scenario()
    payload["security"] = ["enabled"]
    response = client.post("/api/start", json=payload)
    assert response.status_code == 400
    assert response.get_json()["error"] == "Security settings must be an object"
    assert simulator_api.engine is None
