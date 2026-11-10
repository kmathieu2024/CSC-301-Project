"""Isolated comparison API: fault_rate uses a 0–1 fraction, not a percentage."""

from copy import deepcopy

import pytest

import app as api


REQUEST = {"addresses": [0, 4, 0, 8, 0], "frame_count": 2, "page_size": 4}
EXPECTED = {
    "fifo": {"references": 5, "hits": 1, "faults": 4, "evictions": 2, "fault_rate": 0.8},
    "lru": {"references": 5, "hits": 2, "faults": 3, "evictions": 1, "fault_rate": 0.6},
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, "engine", None)
    return api.app.test_client()


def test_known_trace_and_repeat_comparisons_start_empty(client):
    for _ in range(2):
        response = client.post("/api/memory/compare", json=REQUEST)
        assert response.status_code == 200
        assert response.get_json() == EXPECTED
        assert api.engine is None


@pytest.mark.parametrize("field,value", [
    ("addresses", None), ("addresses", []), ("addresses", "0,4"),
    ("addresses", {}), ("addresses", [True]), ("addresses", [False]),
    ("addresses", [-1]), ("addresses", [1.5]), ("addresses", [1.0]),
    ("addresses", ["4"]), ("addresses", [None]), ("addresses", [[0]]),
    ("frame_count", None), ("frame_count", True), ("frame_count", False),
    ("frame_count", 0), ("frame_count", -1), ("frame_count", 2.0),
    ("frame_count", "2"), ("frame_count", []),
    ("page_size", None), ("page_size", True), ("page_size", False),
    ("page_size", 0), ("page_size", -1), ("page_size", 4.0),
    ("page_size", "4"), ("page_size", {}),
])
def test_invalid_fields_return_json_errors(client, field, value):
    payload = {**REQUEST, field: value}
    response = client.post("/api/memory/compare", json=payload)
    assert response.status_code == 400
    assert field.rstrip("es") in response.get_json()["error"]


@pytest.mark.parametrize("field", REQUEST)
def test_required_fields(client, field):
    payload = dict(REQUEST)
    del payload[field]
    response = client.post("/api/memory/compare", json=payload)
    assert response.status_code == 400
    assert response.get_json()["error"]


@pytest.mark.parametrize("body", [
    "", "{", "null", "[]", "true", "42", '"trace"',
    '{"addresses":[0],"addresses":[4],"frame_count":2,"page_size":4}',
])
def test_invalid_json_and_nonobjects(client, body):
    response = client.post("/api/memory/compare", data=body, content_type="application/json")
    assert response.status_code == 400
    assert response.is_json
    assert response.get_json()["error"]


def test_comparison_leaves_in_progress_simulation_unchanged(client):
    workload = {
        "algorithm": "rr",
        "configuration": {"time_quantum": 1},
        "memory": {"frame_count": 2, "page_size": 4},
        "processes": [{
            "pid": "P1", "arrival_time": 0, "burst_time": 5,
            "memory_references": {"0": 0, "2": 4},
            "file_requests": {"0": "CREATE /kept.txt", "3": "READ /kept.txt"},
        }],
    }
    response = client.post("/api/start", json=workload)
    assert response.status_code == 200, response.get_json()
    client.post("/api/step")  # Dispatch.
    assert client.post("/api/step").status_code == 200  # Execute one quantum.
    engine = api.engine
    assert engine.file_manager.read_file("/kept.txt") == ""
    before = deepcopy(engine.results())
    assert before["processes"][0]["state"] != "TERMINATED"
    file_manager = engine.file_manager
    memory_manager = engine.memory_manager

    assert client.post("/api/memory/compare", json=REQUEST).get_json() == EXPECTED
    assert client.post("/api/memory/compare", json={**REQUEST, "addresses": [-1]}).status_code == 400
    assert api.engine is engine
    assert engine.file_manager is file_manager
    assert engine.memory_manager is memory_manager
    assert engine.results() == before
    assert file_manager.read_file("/kept.txt") == ""
    # The paused generator is also untouched: continuing matches a fresh control run.
    completed = client.post("/api/run").get_json()
    client.post("/api/start", json=workload)
    assert client.post("/api/run").get_json() == completed
