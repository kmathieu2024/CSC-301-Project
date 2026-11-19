# Security Manager

The Security Manager implements role-based access control (RBAC) for the OS
simulator. It provides user accounts, roles, permissions, authorization checks,
security alerts, and an append-only audit history. File operations, Device
requests, and Network sends use its existing authorization interface.

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

The process ID is used as the File, Device, and Network username. Process IDs not
explicitly listed in `users` are registered without roles and denied protected requests.
When `enabled` is false or the `security` object is omitted, File Manager retains
its backward-compatible allow-all policy; Device and Network also retain legacy
behavior. Disabled policies do not validate unused role/user definitions.

Dashboard workload import applies an explicit Security policy to the editor,
including a disabled policy. If the workload omits Security, the existing editor
is retained and sent with the candidate. Explicit null means disabled, consistent
with the API. Bare policy documents remain unsupported by workload import.

## Permissions

Permissions use `resource:action` syntax. Examples include `file:read`,
`file:write`, `file:*`, and `*`. Path-scoped permissions use
`resource:/path/pattern:action`, such as `file:/private/*:read`. `fnmatch`-style
path patterns are supported. Authorization is deny-by-default.

Device operation mappings are Disk READ/WRITE to `device:read`/`device:write`,
Printer PRINT to `device:print`, and Keyboard READ to `device:read`. The engine
checks before request submission or blocking, with resource `device`, the lower
case action, and target `/devices/<encoded-id>`. Dots, slashes, percent signs, and
other path syntax are encoded as data. For example,
`device:/devices/keyboard:read` grants only that target; `device:*` grants all
Device operations. Denied requests are marked handled and recorded as DENIED
engine outcomes with no request ID, queue entry, callback, or blocked time.
Other eligible work at the same CPU offset continues. Standalone DeviceManager
algorithms and notifications do not perform authorization themselves.

Network sends use resource `network`, action `send`, and canonical encoded
`/nodes/<source>/to/<destination>` targets. `network:send` grants sends broadly.
Each File/Device/Network decision is audited once. Existing Security-enabled
Device workloads must explicitly grant Device permissions; disabled workloads
are unchanged.

## Candidate validation and import atomicity

`POST /api/validate` accepts the same workload as `/api/start`, through the same
candidate builder. Success returns `valid: true` and `security_enabled`;
failure returns HTTP 400, `code: "invalid_workload"`, and `error`. It constructs
only a disposable candidate: no accepted engine, callback, result, or audit
history is changed. Start replaces the engine only after construction and its
initial snapshot succeed.

The dashboard awaits candidate validation before synchronously committing all
imported inputs; imports require serving the dashboard through Flask, not a
`file://` preview. A successful import updates dashboard inputs; the existing
Run/Step controls start the new simulation afterward. Rejection preserves all
accepted configuration, drafts, results, exports, and execution state. Generation
checks prevent an older response from committing after a newer import, editor
change, Start/Step, Reset, or Pause. No Start/Step/Reset/Results endpoint is called
to validate an import. Configuration audit records created in a disposable
candidate never enter accepted history. Starting a different workload creates a
new manager/history; Reset of that run retains its existing history.

Button-driven Edit/Cancel/Clear and draft submissions invalidate pending imports
through the same generation mechanism as input edits. Successful Save/Remove
actions retain their existing invalidation path; the submission fallback avoids
invalidating twice. Navigation/read-only controls and the import's own authorized
form resets do not invalidate it. Superseded uploads cannot replace newer status
or errors, and cleanup clears only the File object owned by that invocation,
even when two selected files have the same name. Current validation failures
still show the backend's actionable error.

The six-manager shared-offset regression grants `file:create`, `device:read`, and
`network:send`. It retains final time 6 and blocked time 5, now with three allowed
authorization records: File at 0, Device at 2, and Network at 3. DM-A07 remains
deferred; this change does not alter Device result-driven notification delivery.

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
