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


def test_blocking_and_memory_requests_share_a_workload(client):
    workload = scenario(blocking_requests={"1": 3}, memory_references={"0": 0})
    workload["memory"]["fault_latency"] = 2
    assert client.post("/api/start", json=workload).status_code == 200
    result = client.post("/api/run").get_json()
    assert result["memory_metrics"]["faults"] == 1
    assert simulator_api.engine.processes[0].blocked_time == 5
