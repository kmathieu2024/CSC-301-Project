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


@pytest.mark.xfail(
    strict=True, raises=KnownInputGap,
    reason="The start endpoint drops submitted blocking requests",
)
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

    if process.blocking_requests == {} and transitions == []:
        raise KnownInputGap("Accepted the input but P1 never blocked")

    assert process.blocking_requests == {1: 3}
    assert transitions == [
        ("block", "RUNNING", "WAITING", 1),
        ("unblock", "WAITING", "READY", 4),
    ]
    assert process.blocked_time == 3


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


def test_security_policy_is_deny_by_default_for_unassigned_process(client):
    payload = scenario(file_requests={"0": "CREATE /blocked.txt"})
    payload["security"] = {
        "enabled": True,
        "roles": [{"name": "reader", "permissions": ["file:read"]}],
        "users": [],
    }
    assert client.post("/api/start", json=payload).status_code == 200

    results = client.post("/api/run").get_json()
    assert results["security_users"] == [
        {"username": "P1", "roles": [], "active": True}
    ]
    assert results["security_alerts"][0]["outcome"] == "denied"
    file_event = next(event for event in results["events"] if event["manager"] == "file")
    assert file_event["outcome"] == "error"


def test_invalid_security_configuration_is_rejected_without_replacing_engine(client):
    initial = client.post("/api/start", json=scenario())
    assert initial.status_code == 200
    existing_engine = simulator_api.engine

    payload = scenario()
    payload["security"] = {
        "enabled": True,
        "roles": [{"name": "reader", "permissions": ["malformed"]}],
        "users": [],
    }
    response = client.post("/api/start", json=payload)

    assert response.status_code == 400
    assert response.get_json()["error"]
    assert simulator_api.engine is existing_engine
