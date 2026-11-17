import pytest

from src.managers.security import SecurityError, SecurityManager


def test_users_roles_and_permissions_are_managed():
    security = SecurityManager()
    security.create_role("reader", ["file:read", "file:list"])
    security.create_role("admin", ["*"])
    security.add_user("alice", ["reader"])
    security.add_user("root", ["admin"])

    assert security.users == [
        {"username": "alice", "roles": ["reader"], "active": True},
        {"username": "root", "roles": ["admin"], "active": True},
    ]
    assert security.authorize("alice", "read", "/docs/readme.txt") is True
    assert security.authorize("alice", "write", "/docs/readme.txt") is False
    assert security.authorize("root", "delete", "/docs/readme.txt") is True


def test_path_scoped_permissions_and_default_deny():
    security = SecurityManager()
    security.create_role("private_reader", ["file:/private/*:read"])
    security.add_user("alice", ["private_reader"])
    assert security.authorize("alice", "read", "/private/notes.txt") is True
    assert security.authorize("alice", "read", "/public/notes.txt") is False
    assert security.authorize("unknown", "read", "/private/notes.txt") is False


def test_disabled_user_is_denied_until_reenabled():
    security = SecurityManager()
    security.create_role("reader", ["file:read"])
    security.add_user("alice", ["reader"])
    security.disable_user("alice")
    assert security.authorize("alice", "read", "/notes.txt") is False
    security.enable_user("alice")
    assert security.authorize("alice", "read", "/notes.txt") is True


def test_role_and_user_configuration_is_validated():
    security = SecurityManager()
    with pytest.raises(SecurityError):
        security.create_role("reader", ["not-a-permission"])
    security.create_role("reader", ["file:read"])
    with pytest.raises(SecurityError):
        security.create_role("reader", ["file:read"])
    with pytest.raises(SecurityError):
        security.add_user("alice", ["missing-role"])
    security.add_user("alice", ["reader"])
    with pytest.raises(SecurityError):
        security.assign_role("alice", "reader")


def test_audit_log_is_append_only_and_returns_defensive_copies():
    security = SecurityManager()
    security.create_role("reader", ["file:read"])
    security.add_user("alice", ["reader"])
    assert security.authorize("alice", "read", "/notes.txt") is True
    assert security.authorize("alice", "write", "/notes.txt") is False

    original_length = len(security.audit_log)
    snapshot = security.audit_log
    snapshot[-1]["action"] = "tampered"
    assert len(security.audit_log) == original_length
    assert security.audit_log[-1]["action"] == "write"
    assert len(security.security_alerts) == 1
    assert security.security_alerts[0]["outcome"] == "denied"


def test_security_event_timestamp_and_scenario_context_are_recorded():
    security = SecurityManager()
    security.create_role("writer", ["file:write"])
    security.add_user("alice", ["writer"])
    security.set_context(12, "security-test")
    assert security.authorize("alice", "write", "/notes.txt") is True
    event = security.security_events[-1]
    assert event["timestamp"] == 12
    assert event["scenario_id"] == "security-test"


def test_resource_scoped_permissions_support_other_manager_types():
    security = SecurityManager()
    security.create_role("device_operator", ["device:open"])
    security.add_user("worker", ["device_operator"])
    assert security.authorize("worker", "open", "disk-0", resource="device") is True
    assert security.authorize("worker", "write", "disk-0", resource="device") is False
    assert security.security_events[-1]["resource_type"] == "device"


def test_user_and_role_removal_keep_audit_records():
    security = SecurityManager()
    security.create_role("reader", ["file:read"])
    security.add_user("alice", ["reader"])
    with pytest.raises(SecurityError):
        security.delete_role("reader")
    security.remove_user("alice")
    security.delete_role("reader")
    assert security.users == []
    assert security.roles == []
    actions = [record["action"] for record in security.audit_log]
    assert "remove_user" in actions
    assert "delete_role" in actions


def test_permission_updates_and_role_revocation_are_audited():
    security = SecurityManager()
    security.create_role("reader", ["file:read"])
    security.add_user("alice", ["reader"])
    security.grant_permission("reader", "file:list")
    assert security.authorize("alice", "list", "/") is True
    security.revoke_permission("reader", "file:list")
    assert security.authorize("alice", "list", "/") is False
    security.revoke_role("alice", "reader")
    assert security.authorize("alice", "read", "/notes.txt") is False
