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
      {
        "name": "reader",
        "permissions": ["file:read", "file:list", "file:metadata"]
      },
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
listed in `users` are registered without roles, which means protected requests
are denied by default. All referenced roles must be defined before users are
created.

Permission syntax:

- `*` grants every action for every resource.
- `file:*` grants every file action.
- `file:read` grants one action for the File Manager.
- `file:/private/*:read` grants an action only for matching paths.
- The resource prefix can be extended for other managers, for example
  `device:open` or `network:send`, when those managers integrate with the same
  authorization interface.

Roles can be created with `create_role`, users with `add_user`, and role
assignments can be changed with `assign_role` and `revoke_role`. Permissions can
be changed with `grant_permission` and `revoke_permission`. Users can be
suspended and re-enabled with `disable_user` and `enable_user`.

## Events and audit records

Every authorization decision generates a security event and an audit record.
Allowed decisions use event type `authorization`; denied decisions use
`security_alert` with warning severity. User/role/permission configuration
changes are also written to the audit log.

`SimulationEngine.results()` and the Flask API expose:

- `security_enabled`
- `security_users` and `security_roles`
- `security_events` and `security_alerts`
- `audit_log`

Security events also enter the shared `events` collection with
`manager: "security"`, so integrations can consume a common event stream. Each
record includes the simulation timestamp and scenario ID. Resetting a
simulation does not erase the audit history; a replay appends new records.

The dashboard's Security Manager tab accepts the same JSON policy and displays
users, roles, authorization events, alerts, and audit records. The policy is
included in exported simulation results.

## Running the tests

```bash
python -m pytest tests/test_security_manager.py tests/test_security_integration.py -v
python -m pytest tests/ -v
```
