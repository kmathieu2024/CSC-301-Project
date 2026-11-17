# Security Manager

The Security Manager implements role-based access control (RBAC) for the OS
simulator. It provides user accounts, roles, permissions, authorization checks,
security alerts, and an append-only audit history. Its current integration point
is the File Manager's `authorize(user, action, path)` policy interface.

This is a teaching simulator feature. It does not implement passwords, sessions,
cryptographic identity, operating-system account isolation, or production-grade
security boundaries.

## Policy format

Security is opt-in so existing simulator workloads remain compatible. Add a
`security` object to the `/api/start` request:

```json
{
  "security": {
    "enabled": true,
    "roles": [
      {"name": "admin", "permissions": ["*"]},
      {"name": "reader", "permissions": ["file:read", "file:list", "file:metadata"]},
      {"name": "private-reader", "permissions": ["file:/private/*:read"]}
    ],
    "users": [
      {"username": "P1", "roles": ["admin"]},
      {"username": "P2", "roles": ["reader"]}
    ]
  }
}
```

The process ID is used as the File Manager username. Process IDs not explicitly
listed in `users` are registered without roles and denied protected requests.
When `enabled` is false or the `security` object is omitted, File Manager retains
its backward-compatible allow-all policy.

## Permissions

Permissions use `resource:action` syntax. Examples include `file:read`,
`file:write`, `file:*`, and `*`. Path-scoped permissions use
`resource:/path/pattern:action`, such as `file:/private/*:read`. `fnmatch`-style
path patterns are supported. Authorization is deny-by-default.

## Management API

The manager exposes `create_role`, `delete_role`, `add_user`, `remove_user`,
`assign_role`, `revoke_role`, `grant_permission`, `revoke_permission`,
`disable_user`, and `enable_user`. `authorize(user, action, path)` implements the
File Manager's policy interface. Other manager integrations can pass a resource
type using `authorize(..., resource="device")`.

## Events and audit records

Every authorization decision generates a security event and an audit record.
Allowed decisions use event type `authorization`; denied decisions use
`security_alert` with warning severity. User, role, and permission configuration
changes are also written to the audit log.

`SimulationEngine.results()` and the Flask API expose `security_enabled`,
`security_users`, `security_roles`, `security_events`, `security_alerts`, and
`audit_log`. Security events also enter the shared `events` collection with
`manager: "security"`, including simulation timestamp and scenario ID. Resetting
a simulation does not erase audit history; a replay appends new records.

## Tests

```bash
python -m pytest tests/test_security_manager.py tests/test_security_integration.py -v
python -m pytest tests/test_api_inputs.py -v
python -m pytest tests/ -v
```
