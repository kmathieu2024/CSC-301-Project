from src.core.process import Process
from src.core.simulation import SimulationEngine
from src.managers.file import FileManager
from src.managers.security import SecurityManager
import pytest

from src.managers.device import (
    DeviceConfig, DeviceManager, DeviceRequestSpec, DeviceType, DiskGeometry,
    ScanDirection, SchedulingPolicy,
)


@pytest.mark.parametrize("kind,operation", [
    (DeviceType.DISK, "READ"), (DeviceType.DISK, "WRITE"),
    (DeviceType.PRINTER, "PRINT"), (DeviceType.KEYBOARD, "READ"),
])
@pytest.mark.parametrize("allowed", [False, True])
def test_every_device_operation_is_authorized_once_before_side_effects(
    kind: DeviceType, operation: str, allowed: bool,
) -> None:
    """Every supported operation uses its explicit permission and encoded ID."""
    identity = "../a:b/%device"
    target = "/devices/%2E%2E%2Fa%3Ab%2F%25device"
    permission = f"device:{target}:{operation.lower()}" if allowed else "file:*"
    security = SecurityManager()
    security.create_role("worker", [permission])
    security.add_user("P", ["worker"])
    config = DeviceConfig(
        device_id=identity, device_type=kind, policy=SchedulingPolicy.FCFS,
        default_service_time=1,
        disk=(DiskGeometry(min_cylinder=0, max_cylinder=9, initial_head=0,
                           initial_direction=ScanDirection.UP,
                           seek_time_per_cylinder=1)
              if kind == DeviceType.DISK else None),
    )
    device = DeviceManager([config])
    process = Process("P", 0, 2, device_requests={0: DeviceRequestSpec(
        device_id=identity, operation=operation,
        cylinder=1 if kind == DeviceType.DISK else None,
    )})
    engine = SimulationEngine([process], device_manager=device,
                              security_manager=security)
    result = engine.run()
    records = security.security_events
    assert len(records) == 1
    observed = tuple(records[0][key] for key in ("resource_type", "action", "resource"))
    assert observed == (
        "device", operation.lower(), target,
    )
    assert records[0]["outcome"] == ("allowed" if allowed else "denied")
    assert result["device_metrics"]["devices"][identity]["submitted"] == int(allowed)
    duration = 2 if kind == DeviceType.DISK else 1
    assert process.blocked_time == (duration if allowed else 0)
    if not allowed:
        assert not device.requests and not engine.event_queue
        assert engine._device_submitted[("P", 0)] is None
        assert next(iter(result["device_outcomes"].values()))["outcome"] == "DENIED"
    decisions = [e for e in security.audit_log if e.get("resource_type") == "device"]
    assert len(decisions) == 1


@pytest.mark.parametrize("permission,allowed", [
    ("device:*", True), ("*", True),
    ("device:/devices/*:read", True), ("device:write", False),
])
def test_device_permissions_use_existing_wildcards(
    permission: str, allowed: bool,
) -> None:
    from tests.test_device_integration import manager, spec

    security = SecurityManager()
    security.create_role("worker", [permission])
    security.add_user("P", ["worker"])
    engine = SimulationEngine([Process("P", 0, 1, device_requests={0: spec()})],
                              device_manager=manager(service=1),
                              security_manager=security)
    assert engine.run()["device_metrics"]["devices"]["d0"]["submitted"] == int(allowed)
    assert len(security.security_events) == 1


@pytest.mark.parametrize("boundary", ["authorize", "submit"])
def test_device_security_failure_keeps_reset_required_contract(
    monkeypatch, boundary: str,
) -> None:
    from src.core.simulation import SimulationExecutionError
    from tests.test_device_integration import manager, spec

    security = SecurityManager()
    security.create_role("worker", ["device:read"])
    security.add_user("P", ["worker"])
    device = manager(service=1)
    process = Process("P", 0, 1, device_requests={0: spec()})
    engine = SimulationEngine([process], device_manager=device,
                              security_manager=security)
    owner, method = ((security, "authorize") if boundary == "authorize"
                     else (device, "submit_request"))
    original = getattr(owner, method)
    failure = RuntimeError("injected " + boundary)

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(owner, method, fail)
    with pytest.raises(SimulationExecutionError) as caught:
        engine.run()
    assert caught.value.__cause__ is failure
    assert not device.requests and process.blocked_time == 0
    assert not engine._device_waiting
    with pytest.raises(SimulationExecutionError):
        engine.step()
    monkeypatch.setattr(owner, method, original)
    engine.reset()
    assert engine.run()["device_metrics"]["devices"]["d0"]["completed"] == 1


def test_security_manager_authorizes_file_requests_and_exposes_events():
    security = SecurityManager()
    security.create_role("creator", ["file:create"])
    security.add_user("P1", ["creator"])
    file_manager = FileManager()
    process = Process("P1", 0, 2, file_requests={0: "CREATE /notes.txt"})
    engine = SimulationEngine(
        [process], file_manager=file_manager, security_manager=security
    )
    results = engine.run()

    assert file_manager._resolve("/notes.txt").content == ""
    assert results["security_enabled"] is True
    assert results["security_users"] == [
        {"username": "P1", "roles": ["creator"], "active": True}
    ]
    assert len(results["security_events"]) == 1
    assert results["security_events"][0]["outcome"] == "allowed"
    assert any(event["manager"] == "security" for event in results["events"])
    assert results["audit_log"][-1]["event_type"] == "authorization"


def test_denied_file_request_creates_security_alert_and_file_error():
    security = SecurityManager()
    security.add_user("P1")
    process = Process("P1", 0, 1, file_requests={0: "CREATE /secret.txt"})
    security.create_role("inspector", ["file:list", "file:read"])
    security.add_user("inspector", ["inspector"])
    file_manager = FileManager()
    file_manager.create_file("/existing.txt", "unchanged")
    directory_before = file_manager.list_directory("/")
    results = SimulationEngine(
        [process], file_manager=file_manager, security_manager=security
    ).run()

    assert file_manager.list_directory("/", user="inspector") == directory_before
    assert file_manager.read_file("/existing.txt", user="inspector") == "unchanged"
    assert results["security_alerts"][0]["event_type"] == "security_alert"
    assert results["security_alerts"][0]["severity"] == "warning"
    assert results["security_alerts"][0]["outcome"] == "denied"
    file_event = next(event for event in results["events"] if event["manager"] == "file")
    assert file_event["outcome"] == "error"
    assert "not authorized" in file_event["message"].lower()


def test_audit_history_survives_reset_and_grows_on_replay():
    security = SecurityManager()
    security.add_user("P1")
    process = Process("P1", 0, 1, file_requests={0: "CREATE /secret.txt"})
    engine = SimulationEngine([process], security_manager=security)

    engine.run()
    first_audit = engine.results()["audit_log"]
    engine.reset()
    engine.run()
    results = engine.results()

    assert len(results["audit_log"]) == len(first_audit) + 1
    assert results["audit_log"][:-1] == first_audit
    assert len(results["security_alerts"]) == 2
    this_runs_events = [event for event in results["events"] if event["manager"] == "security"]
    assert len(this_runs_events) == 1


def test_security_manager_is_optional_for_existing_file_manager_behavior():
    process = Process("P1", 0, 1, file_requests={0: "CREATE /notes.txt"})
    results = SimulationEngine([process]).run()

    assert results["security_enabled"] is False
    assert results["security_events"] == []
    assert results["security_alerts"] == []
    assert results["audit_log"] == []
    file_events = [event for event in results["events"] if event["manager"] == "file"]
    assert len(file_events) == 1
    assert file_events[0]["outcome"] == "success"


def test_unexpected_file_operation_exception_keeps_authorization_and_failure_events():
    from src.core.simulation import SimulationExecutionError

    class ExplodingFileManager(FileManager):
        @staticmethod
        def _check_permissions(node, action):
            raise RuntimeError("unexpected permission backend failure")

    security = SecurityManager()
    security.create_role("creator", ["file:create"])
    security.add_user("P1", ["creator"])
    process = Process("P1", 0, 1, file_requests={0: "CREATE /notes.txt"})
    engine = SimulationEngine(
        [process], file_manager=ExplodingFileManager(), security_manager=security
    )

    try:
        engine.run()
    except SimulationExecutionError as error:
        assert "unexpected permission backend failure" in str(error)
    else:
        raise AssertionError("The unexpected file operation exception should abort the run")

    results = engine.results()
    security_event = next(
        event for event in results["events"] if event["manager"] == "security"
    )
    file_event = next(
        event for event in results["events"] if event["manager"] == "file"
    )
    assert security_event["outcome"] == "allowed"
    assert file_event["outcome"] == "error"
    assert "unexpected permission backend failure" in file_event["message"]
    assert results["audit_log"][-1]["event_type"] == "authorization"
    assert results["audit_log"][-1]["outcome"] == "allowed"
