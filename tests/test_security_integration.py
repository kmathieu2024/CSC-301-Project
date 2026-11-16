from src.core.process import Process
from src.core.simulation import SimulationEngine
from src.managers.file import FileManager
from src.managers.security import SecurityManager


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


def test_denied_file_request_creates_alert_and_does_not_change_file_system():
    security = SecurityManager()
    security.create_role("reader", ["file:read"])
    security.add_user("P1", ["reader"])
    file_manager = FileManager()
    process = Process("P1", 0, 2, file_requests={0: "CREATE /secret.txt"})

    results = SimulationEngine(
        [process], file_manager=file_manager, security_manager=security
    ).run()

    assert "secret.txt" not in file_manager._root.children
    file_event = next(event for event in results["events"] if event["manager"] == "file")
    assert file_event["outcome"] == "error"
    assert "not authorized" in file_event["message"].lower()
    assert len(results["security_alerts"]) == 1
    alert = results["security_alerts"][0]
    assert alert["event_type"] == "security_alert"
    assert alert["severity"] == "warning"
    assert alert["outcome"] == "denied"


def test_audit_history_survives_simulation_reset_and_grows_on_replay():
    security = SecurityManager()
    security.add_user("P1")
    process = Process("P1", 0, 1, file_requests={0: "CREATE /secret.txt"})
    engine = SimulationEngine([process], security_manager=security)

    engine.run()
    first_count = len(engine.results()["audit_log"])
    engine.reset()
    engine.run()
    second_results = engine.results()

    assert len(second_results["audit_log"]) == first_count + 1
    assert len(second_results["security_alerts"]) == 2
    current_security_events = [
        event for event in second_results["events"]
        if event["manager"] == "security"
    ]
    assert len(current_security_events) == 1


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
