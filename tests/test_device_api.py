"""Device JSON boundary, atomic Flask creation, and deterministic demonstration."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from flask.testing import FlaskClient

import app as simulator_api
from src.managers.device import DeviceRequestSpec


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> FlaskClient:
    """Isolate the existing application-level engine between API tests."""
    monkeypatch.setattr(simulator_api, "engine", None)
    return simulator_api.app.test_client()


def workload() -> dict:
    """A small SCAN request with explicit optional defaults omitted."""
    return {
        "algorithm": "FCFS", "configuration": {}, "memory": {"enabled": False},
        "devices": [{"device_id": "disk0", "device_type": "DISK", "policy": "SCAN",
                     "disk": {"min_cylinder": 0, "max_cylinder": 99,
                              "initial_head": 50}}],
        "processes": [{"pid": "P1", "arrival_time": 0, "burst_time": 3,
                       "device_requests": {"1": {"device_id": "disk0",
                                                 "operation": "READ",
                                                 "cylinder": 60}}}],
    }


def demo() -> dict:
    """Load the canonical fixture also served to the browser."""
    return json.loads(Path("test_data/milestone4/devices.json").read_text())


def peripheral_workload(duration: float, arrival: float = 0) -> dict:
    """Construct an offset-zero workload for numeric boundary regressions."""
    data = workload()
    data["devices"] = [{"device_id": "disk0", "device_type": "KEYBOARD",
                        "default_service_time": duration}]
    data["processes"][0].update(
        arrival_time=arrival, burst_time=1,
        device_requests={"0": {"device_id": "disk0", "operation": "READ"}},
    )
    return data


def malformed_enum_workload(field: str, shape: str) -> dict:
    """Use otherwise valid inputs so enum coercion is the only import error."""
    data = workload()
    valid = {"device_type": "KEYBOARD", "policy": "FCFS",
             "initial_direction": "UP", "operation": "READ"}[field]
    bad = {"array": [valid], "object": {"value": valid},
           "boolean": True, "null": None}[shape]
    if field == "device_type":
        data = peripheral_workload(1)
        data["devices"][0][field] = bad
    elif field == "initial_direction":
        data["devices"][0]["disk"][field] = bad
    elif field == "operation":
        data["processes"][0]["device_requests"]["1"][field] = bad
    else:
        data["devices"][0][field] = bad
    return data


@pytest.mark.parametrize("policy", ["FCFS", "SSTF", "SCAN"])
def test_typed_requests_defaults_and_existing_endpoints(
    client: FlaskClient, policy: str,
) -> None:
    """Requests survive parsing and use the existing start/step/run/reset flow."""
    data = workload()
    data["devices"][0]["policy"] = policy
    assert client.post("/api/start", json=data).status_code == 200
    engine = simulator_api.engine
    assert isinstance(engine.processes[0].device_requests[1], DeviceRequestSpec)
    assert client.post("/api/step").status_code == 200
    result = client.post("/api/run").get_json()
    assert result["current_time"] == 14
    assert result["processes"][0]["blocked_time"] == 11
    assert result["device_metrics"]["devices"]["disk0"]["submitted"] == 1
    assert result["devices"]["disk0"]["config"]["disk"]["initial_direction"] == "UP"
    assert "token" not in json.dumps(result)
    assert "generation" not in json.dumps(result)
    reset = client.post("/api/reset").get_json()
    assert reset["device_requests"] == {}
    assert client.post("/api/run").get_json() == result


@pytest.mark.parametrize("kind,operation", [("PRINTER", "PRINT"), ("KEYBOARD", "READ")])
def test_peripheral_defaults(client: FlaskClient, kind: str, operation: str) -> None:
    """Nondisks default to FCFS and one service unit without geometry."""
    data = workload()
    data["devices"] = [{"device_id": "disk0", "device_type": kind}]
    data["processes"][0]["device_requests"]["1"] = {
        "device_id": "disk0", "operation": operation,
    }
    assert client.post("/api/start", json=data).status_code == 200
    result = client.post("/api/run").get_json()
    assert result["current_time"] == 4


@pytest.mark.parametrize("field,value", [
    ("device_type", "NETWORK"), ("device_type", True), ("policy", "LOOK"),
    ("device_id", ""), ("device_id", " d"),
    ("default_service_time", -1), ("default_service_time", True),
    ("default_service_time", float("nan")), ("default_service_time", float("inf")),
    ("disk", None), ("unknown", 1),
])
def test_bad_device_configuration_preserves_engine(
    client: FlaskClient, field: str, value: object,
) -> None:
    """Invalid replacements never overwrite an existing simulation."""
    assert client.post("/api/start", json=workload()).status_code == 200
    original = simulator_api.engine
    before = original.results()
    invalid = workload()
    invalid["devices"][0][field] = value
    response = client.post("/api/start", json=invalid)
    assert response.status_code == 400
    assert response.get_json()["error"]
    assert simulator_api.engine is original
    assert original.results() == before


@pytest.mark.parametrize("field,value", [
    ("min_cylinder", -1), ("max_cylinder", 0), ("max_cylinder", True),
    ("initial_head", 100), ("initial_head", 1.5),
    ("initial_direction", "LEFT"), ("seek_time_per_cylinder", -1),
    ("seek_time_per_cylinder", True), ("max_cylinder", 9007199254740992),
])
def test_bad_geometry(client: FlaskClient, field: str, value: object) -> None:
    """Geometry uses domain bounds and browser-safe integer validation."""
    data = workload()
    data["devices"][0]["disk"][field] = value
    assert client.post("/api/start", json=data).status_code == 400


@pytest.mark.parametrize("kind", ["PRINTER", "KEYBOARD"])
@pytest.mark.parametrize("policy", ["SSTF", "SCAN"])
def test_nondisk_policies_rejected(client: FlaskClient, kind: str, policy: str) -> None:
    """Disk-only policies cannot be applied to peripherals."""
    data = workload()
    data["devices"] = [{"device_id": "disk0", "device_type": kind, "policy": policy}]
    assert client.post("/api/start", json=data).status_code == 400


@pytest.mark.parametrize("offset", ["-1", "01", "1.0", "1e0", "3", "9007199254740992"])
def test_bad_offsets(client: FlaskClient, offset: str) -> None:
    """Aliases are rejected rather than overwritten during integer conversion."""
    data = workload()
    request = data["processes"][0]["device_requests"].pop("1")
    data["processes"][0]["device_requests"][offset] = request
    assert client.post("/api/start", json=data).status_code == 400


@pytest.mark.parametrize("field,value", [
    ("device_id", "missing"), ("operation", "PRINT"), ("operation", None),
    ("cylinder", True), ("cylinder", 100), ("service_time", -1),
    ("service_time", False), ("extra", 1),
])
def test_bad_request_specs(client: FlaskClient, field: str, value: object) -> None:
    """Request conversion validates target, operation, timing, and unknown fields."""
    data = workload()
    data["processes"][0]["device_requests"]["1"][field] = value
    assert client.post("/api/start", json=data).status_code == 400


def test_duplicate_ids_keys_and_normalization(client: FlaskClient) -> None:
    """Exact duplicates and noncanonical aliases both fail explicitly."""
    data = workload()
    data["devices"].append(deepcopy(data["devices"][0]))
    assert client.post("/api/start", json=data).status_code == 400
    text = json.dumps(workload())
    request = json.dumps(workload()["processes"][0]["device_requests"]["1"])
    for other in ("1", "01"):
        duplicate = text.replace(
            '"device_requests": {', f'"device_requests": {{"{other}": {request}, ',
        )
        response = client.post(
            "/api/start", data=duplicate, content_type="application/json",
        )
        assert response.status_code == 400


@pytest.mark.parametrize("devices,requests", [
    (None, {}), ({}, {}), ([], []), ([], {"0": {}}),
])
def test_malformed_containers(
    client: FlaskClient, devices: object, requests: object,
) -> None:
    """Malformed arrays/maps cannot be silently discarded."""
    data = workload()
    data["devices"] = devices
    data["processes"][0]["device_requests"] = requests
    assert client.post("/api/start", json=data).status_code == 400


def test_without_devices_and_legacy_fields(client: FlaskClient) -> None:
    """Existing memory and file parsing are preserved for legacy workloads."""
    data = workload()
    del data["devices"]
    del data["processes"][0]["device_requests"]
    data["memory"] = {"enabled": True}
    data["processes"][0].update(memory_references={"0": 0},
                                file_requests={"1": "CREATE /unchanged"})
    assert client.post("/api/start", json=data).status_code == 200
    result = client.post("/api/run").get_json()
    assert result["device_metrics"] is None
    assert result["memory_metrics"]["references"] == 1
    assert result["file_tree"]["children"][0]["name"] == "unchanged"


def test_demo_expected_values_and_independent_interval_audit(
    client: FlaskClient,
) -> None:
    """Audit frozen expectations using timeline integrals and request timestamps."""
    assert client.get("/device-demo.json").get_json() == demo()
    assert client.post("/api/start", json=demo()).status_code == 200
    result = client.post("/api/run").get_json()
    assert result["current_time"] == 319
    assert [(p["completion_time"], p["blocked_time"]) for p in result["processes"]] == [
        (319, 315), (47, 43), (12, 8),
    ]
    assert len(result["device_requests"]) == 12
    expected = {"disk0": (5, 316, 306, 101), "printer0": (4, 12, 0, 0),
                "keyboard0": (3, 3, 0, 0)}
    for device, (completed, busy, movement, area) in expected.items():
        metric = result["device_metrics"]["devices"][device]
        operations = [op for op in result["device_timeline"]
                      if op["device_id"] == device]
        requests = [r for r in result["device_requests"].values()
                    if r["spec"]["device_id"] == device]
        assert metric["completed"] == completed
        assert metric["busy_time"] == busy == sum(
            op["end"] - op["started_at"] for op in operations
        )
        assert metric["head_movement"] == movement == sum(
            op["head_movement"] for op in operations
        )
        assert metric["queue_area"] == area == sum(
            r["start_time"] - r["submitted_at"] for r in requests
        )
        assert metric["utilization"] == pytest.approx(busy / 319 * 100)
    first = result["device_metrics"]["requests"]["request-0"]
    assert (first["service_start_time"], first["finish_time"]) == (10, 12)
    second = result["device_metrics"]["requests"]["request-6"]
    assert (second["submitted_at"], second["start_time"], second["finish_time"]) == (
        13, 53, 134,
    )
    assert client.post("/api/reset").status_code == 200
    assert client.post("/api/run").get_json() == result


@pytest.mark.parametrize("device_id", ["token", "generation", "__proto__"])
def test_valid_ids_survive_internal_field_filtering(
    client: FlaskClient, device_id: str,
) -> None:
    """User identities must not be mistaken for private callback field names."""
    data = workload()
    data["devices"][0]["device_id"] = device_id
    data["processes"][0]["device_requests"]["1"]["device_id"] = device_id
    assert client.post("/api/start", json=data).status_code == 200
    result = client.post("/api/run").get_json()
    assert result["devices"][device_id]["head_position"] == 60
    assert result["device_metrics"]["devices"][device_id]["completed"] == 1
    assert result["current_time"] == 14


def test_execution_failure_is_sticky_and_resettable(client: FlaskClient) -> None:
    """Overflow yields actionable errors on every Step/Run rather than a hang."""
    assert client.post("/api/start", json=peripheral_workload(1e308, 1e308)).status_code == 200
    assert client.post("/api/step").status_code == 200
    failure = client.post("/api/step")
    assert failure.status_code == 400
    body = failure.get_json()
    assert body["code"] == "simulation_failed"
    assert body["reset_required"] is True
    assert body["cause"]["type"] == "ValueError"
    assert "finite" in body["cause"]["message"]
    assert "Traceback" not in failure.get_data(as_text=True)
    for endpoint in ("step", "run", "step"):
        response = client.post("/api/" + endpoint)
        assert response.status_code == 400
        assert response.get_json() == body
    reset = client.post("/api/reset")
    assert reset.status_code == 200
    assert reset.get_json()["execution_error"] is None
    assert client.post("/api/step").status_code == 200
    # Reset clears failure; repeating the invalid workload fails explicitly again.
    assert client.post("/api/run").status_code == 400
    assert client.post("/api/start", json=peripheral_workload(1)).status_code == 200
    assert client.post("/api/run").get_json()["current_time"] == 2


@pytest.mark.parametrize("duration", [1e-320, 1e308, 0])
def test_numeric_boundaries_produce_strict_json(
    client: FlaskClient, duration: float,
) -> None:
    """Both partial and complete responses avoid nonstandard JSON constants."""
    def strict(response):
        assert response.status_code == 200
        result = json.loads(response.get_data(as_text=True),
                            parse_constant=lambda value: pytest.fail(value))
        json.dumps(result, allow_nan=False)
        return result

    strict(client.post("/api/start", json=peripheral_workload(duration)))
    strict(client.post("/api/step"))
    strict(client.post("/api/step"))
    partial = strict(client.post("/api/step"))
    if duration == 1e-320:
        assert partial["device_metrics"]["devices"]["disk0"]["throughput"] is None
    result = strict(client.post("/api/run"))
    assert result["device_metrics"]["devices"]["disk0"]["completed"] == 1


@pytest.mark.parametrize("field", ["device_type", "policy", "initial_direction", "operation"])
@pytest.mark.parametrize("shape", ["array", "object", "boolean", "null"])
def test_nonstring_enums_preserve_accepted_simulation(
    client: FlaskClient, field: str, shape: str,
) -> None:
    """Backend rejects the same malformed enum shapes as dashboard imports."""
    client.post("/api/start", json=workload())
    before = client.post("/api/run").get_json()
    engine = simulator_api.engine
    response = client.post("/api/start", json=malformed_enum_workload(field, shape))
    assert response.status_code == 400
    assert response.get_json()["error"]
    assert simulator_api.engine is engine
    assert client.post("/api/run").get_json() == before
