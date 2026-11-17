"""Role-based access control and security auditing for the OS simulator.

This manager is deliberately deterministic and small enough for an instructional
simulator. It is not a substitute for operating-system credentials, sessions, or
production security controls.
"""
from __future__ import annotations

from fnmatch import fnmatchcase
import math
import posixpath
from typing import Iterable


class SecurityError(ValueError):
    """Raised for invalid security configuration or management operations."""


class SecurityManager:
    """Manage users, roles, permissions, authorization, alerts, and auditing.

    Permissions use ``resource:action`` strings (for example ``file:read``).
    ``*`` and resource wildcards (``file:*``) are supported, as are path-scoped
    rules such as ``file:/private/*:read``. Unknown and unassigned users are
    denied by default. Audit records are append-only and exposed as copies.
    """

    def __init__(self) -> None:
        self._users: dict[str, dict[str, object]] = {}
        self._roles: dict[str, set[str]] = {}
        self._security_events: list[dict[str, object]] = []
        self._audit_log: list[dict[str, object]] = []
        self.current_time: int | float = 0
        self.scenario_id = "default"

    @staticmethod
    def _validate_name(name: str, kind: str) -> str:
        if not isinstance(name, str) or not name.strip():
            raise SecurityError(f"{kind} name must be a non-empty string")
        if name != name.strip():
            raise SecurityError(f"{kind} name cannot contain surrounding whitespace")
        return name

    @staticmethod
    def _canonicalize_path(path: str) -> str:
        """Normalize virtual POSIX paths without changing their case.

        FileManager treats repeated separators, ``.`` and ``..`` as path
        syntax and clamps traversal at its virtual root. Match those semantics
        before evaluating path-scoped permissions. A leading double slash is
        collapsed because the simulator has one root, not POSIX network roots.
        """
        normalized = posixpath.normpath(path)
        if path.startswith("/"):
            return "/" + normalized.lstrip("/")
        return normalized

    @classmethod
    def _normalize_permission(cls, permission: str) -> str:
        """Normalize permission syntax while preserving path-name case."""
        if not isinstance(permission, str) or not permission.strip():
            raise SecurityError("Permissions must be non-empty strings")
        value = permission.strip()
        if value == "*":
            return "*"
        parts = value.split(":")
        if len(parts) == 2 and all(part.strip() for part in parts):
            resource, action = (part.strip().lower() for part in parts)
            return f"{resource}:{action}"
        if len(parts) >= 3 and parts[0].strip() and parts[-1].strip():
            resource = parts[0].strip().lower()
            path_pattern = ":".join(parts[1:-1]).strip()
            action = parts[-1].strip().lower()
            if not path_pattern:
                raise SecurityError(
                    "Permission must be '*', 'resource:*', 'resource:action', "
                    "or 'resource:/path/pattern:action'"
                )
            path_pattern = cls._canonicalize_path(path_pattern)
            return f"{resource}:{path_pattern}:{action}"
        raise SecurityError(
            "Permission must be '*', 'resource:*', 'resource:action', "
            "or 'resource:/path/pattern:action'"
        )

    def set_context(self, timestamp: int | float, scenario_id: str = "default") -> None:
        """Set the timestamp and scenario attached to subsequent events."""
        if (isinstance(timestamp, bool) or not isinstance(timestamp, (int, float))
                or not math.isfinite(timestamp) or timestamp < 0):
            raise ValueError("Security event timestamp must be a nonnegative finite number")
        if not isinstance(scenario_id, str) or not scenario_id:
            raise ValueError("scenario_id must be a non-empty string")
        self.current_time = timestamp
        self.scenario_id = scenario_id

    def _record_audit(
        self,
        action: str,
        actor: str,
        target: str,
        outcome: str = "success",
        message: str = "",
    ) -> None:
        self._audit_log.append({
            "timestamp": self.current_time,
            "manager": "security",
            "event_type": "audit",
            "actor": actor,
            "entity_id": target,
            "action": action,
            "outcome": outcome,
            "message": message,
            "scenario_id": self.scenario_id,
        })

    def _record_security_event(
        self,
        user: str,
        action: str,
        path: str,
        resource: str,
        allowed: bool,
        message: str,
    ) -> None:
        event = {
            "timestamp": self.current_time,
            "manager": "security",
            "event_type": "authorization" if allowed else "security_alert",
            "entity_id": user,
            "action": action,
            "resource_type": resource,
            "resource": path,
            "outcome": "allowed" if allowed else "denied",
            "severity": "info" if allowed else "warning",
            "message": message,
            "scenario_id": self.scenario_id,
        }
        self._security_events.append(event)
        # Each authorization decision is also recorded in the durable audit log.
        self._audit_log.append({
            "timestamp": event["timestamp"],
            "manager": "security",
            "event_type": event["event_type"],
            "actor": user,
            "entity_id": user,
            "action": action,
            "resource_type": resource,
            "resource": path,
            "outcome": event["outcome"],
            "message": message,
            "scenario_id": self.scenario_id,
        })

    def create_role(
        self, name: str, permissions: Iterable[str] = (), *, actor: str = "system"
    ) -> None:
        """Create a role with an initial collection of permission strings."""
        name = self._validate_name(name, "Role")
        if name in self._roles:
            raise SecurityError(f"Role already exists: {name}")
        if isinstance(permissions, (str, bytes)):
            raise SecurityError("Role permissions must be an iterable of strings")
        try:
            normalized = {self._normalize_permission(item) for item in permissions}
        except TypeError as exc:
            raise SecurityError("Role permissions must be an iterable of strings") from exc
        self._roles[name] = normalized
        self._record_audit("create_role", actor, name)

    def add_user(
        self, username: str, roles: Iterable[str] = (), *, actor: str = "system"
    ) -> None:
        """Register a user and optionally assign roles that already exist."""
        username = self._validate_name(username, "User")
        if username in self._users:
            raise SecurityError(f"User already exists: {username}")
        if isinstance(roles, (str, bytes)):
            raise SecurityError("User roles must be an iterable of role names")
        try:
            assigned = set(roles)
        except TypeError as exc:
            raise SecurityError("User roles must be an iterable of role names") from exc
        if any(not isinstance(role, str) or role not in self._roles for role in assigned):
            raise SecurityError("Every assigned role must already exist")
        self._users[username] = {"roles": assigned, "active": True}
        self._record_audit("add_user", actor, username)
        for role in sorted(assigned):
            self._record_audit("assign_role", actor, f"{username}:{role}")

    def remove_user(self, username: str, *, actor: str = "system") -> None:
        """Remove an account while retaining its historical audit records."""
        if username not in self._users:
            raise SecurityError(f"Unknown user: {username}")
        del self._users[username]
        self._record_audit("remove_user", actor, username)

    def delete_role(self, role: str, *, actor: str = "system") -> None:
        """Delete a role only when it is not assigned to any user."""
        if role not in self._roles:
            raise SecurityError(f"Unknown role: {role}")
        if any(role in account["roles"] for account in self._users.values()):
            raise SecurityError(f"Cannot delete assigned role: {role}")
        del self._roles[role]
        self._record_audit("delete_role", actor, role)

    def assign_role(self, username: str, role: str, *, actor: str = "system") -> None:
        """Assign an existing role to an existing user."""
        if username not in self._users:
            raise SecurityError(f"Unknown user: {username}")
        if role not in self._roles:
            raise SecurityError(f"Unknown role: {role}")
        roles = self._users[username]["roles"]
        assert isinstance(roles, set)
        if role in roles:
            raise SecurityError(f"User {username} already has role {role}")
        roles.add(role)
        self._record_audit("assign_role", actor, f"{username}:{role}")

    def revoke_role(self, username: str, role: str, *, actor: str = "system") -> None:
        """Remove a role assignment from a user."""
        if username not in self._users:
            raise SecurityError(f"Unknown user: {username}")
        roles = self._users[username]["roles"]
        assert isinstance(roles, set)
        if role not in roles:
            raise SecurityError(f"User {username} does not have role {role}")
        roles.remove(role)
        self._record_audit("revoke_role", actor, f"{username}:{role}")

    def grant_permission(self, role: str, permission: str, *, actor: str = "system") -> None:
        """Add a permission to an existing role."""
        if role not in self._roles:
            raise SecurityError(f"Unknown role: {role}")
        normalized = self._normalize_permission(permission)
        self._roles[role].add(normalized)
        self._record_audit("grant_permission", actor, f"{role}:{normalized}")

    def revoke_permission(self, role: str, permission: str, *, actor: str = "system") -> None:
        """Remove a permission from an existing role."""
        if role not in self._roles:
            raise SecurityError(f"Unknown role: {role}")
        normalized = self._normalize_permission(permission)
        if normalized not in self._roles[role]:
            raise SecurityError(f"Role {role} does not grant {normalized}")
        self._roles[role].remove(normalized)
        self._record_audit("revoke_permission", actor, f"{role}:{normalized}")

    def disable_user(self, username: str, *, actor: str = "system") -> None:
        """Disable an account without deleting it or its role assignments."""
        if username not in self._users:
            raise SecurityError(f"Unknown user: {username}")
        self._users[username]["active"] = False
        self._record_audit("disable_user", actor, username)

    def enable_user(self, username: str, *, actor: str = "system") -> None:
        """Re-enable a previously disabled account."""
        if username not in self._users:
            raise SecurityError(f"Unknown user: {username}")
        self._users[username]["active"] = True
        self._record_audit("enable_user", actor, username)

    @staticmethod
    def _permission_matches(permission: str, resource: str, action: str, path: str) -> bool:
        if permission == "*":
            return True
        parts = permission.split(":")
        if len(parts) == 2:
            permission_resource, permission_action = parts
            return (permission_resource in (resource, "*")
                    and permission_action in (action, "*"))
        if len(parts) >= 3:
            permission_resource = parts[0]
            permission_path = ":".join(parts[1:-1])
            permission_action = parts[-1]
            return (
                permission_resource in (resource, "*")
                and permission_action in (action, "*")
                and fnmatchcase(path, permission_path)
            )
        return False

    def authorize(
        self, user: object, action: str, path: str, *, resource: str = "file"
    ) -> bool:
        """Return whether a user may perform an action on a resource.

        Unknown users and users without matching permission are denied. Every
        decision creates a security event and an audit record. The default
        ``resource`` is ``file`` to satisfy FileManager's authorizer interface.
        """
        username = str(user) if user is not None else "anonymous"
        if not isinstance(action, str) or not action.strip():
            raise ValueError("Authorization action must be a non-empty string")
        if not isinstance(path, str) or not path.strip():
            raise ValueError("Authorization resource must be a non-empty path")
        if not isinstance(resource, str) or not resource.strip():
            raise ValueError("Authorization resource type must be a non-empty string")
        action = action.strip().lower()
        resource = resource.strip().lower()
        path = self._canonicalize_path(path)

        account = self._users.get(username)
        if account is None:
            allowed = False
            message = f"Unknown user '{username}' was denied {action} access to {path}"
        elif not account["active"]:
            allowed = False
            message = f"Disabled user '{username}' was denied {action} access to {path}"
        else:
            roles = account["roles"]
            assert isinstance(roles, set)
            permissions: set[str] = set()
            for role in roles:
                permissions.update(self._roles[role])
            allowed = any(
                self._permission_matches(permission, resource, action, path)
                for permission in permissions
            )
            message = (
                f"User '{username}' is authorized to {action} {path}"
                if allowed else
                f"User '{username}' lacks {resource}:{action} permission for {path}"
            )

        self._record_security_event(username, action, path, resource, allowed, message)
        return allowed

    @property
    def users(self) -> list[dict[str, object]]:
        """Return a JSON-safe snapshot of configured users and role assignments."""
        return [
            {"username": username, "roles": sorted(account["roles"]),
             "active": bool(account["active"])}
            for username, account in sorted(self._users.items())
        ]

    @property
    def roles(self) -> list[dict[str, object]]:
        """Return a JSON-safe snapshot of roles and permissions."""
        return [
            {"name": name, "permissions": sorted(permissions)}
            for name, permissions in sorted(self._roles.items())
        ]

    @property
    def security_events(self) -> tuple[dict[str, object], ...]:
        """Return defensive copies of authorization events and alerts."""
        return tuple(event.copy() for event in self._security_events)

    @property
    def security_alerts(self) -> tuple[dict[str, object], ...]:
        """Return defensive copies of denied authorization events."""
        return tuple(
            event.copy() for event in self._security_events
            if event["event_type"] == "security_alert"
        )

    @property
    def audit_log(self) -> tuple[dict[str, object], ...]:
        """Return a defensive copy of the append-only audit history."""
        return tuple(record.copy() for record in self._audit_log)


__all__ = ["SecurityError", "SecurityManager"]
