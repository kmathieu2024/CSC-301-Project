// Render Security Manager data returned in each simulation snapshot.
function renderSecurityManager(results) {
    const status = document.getElementById("security-status");
    const usersBody = document.getElementById("security-users-body");
    const rolesBody = document.getElementById("security-roles-body");
    const eventsBody = document.getElementById("security-events-body");
    const auditBody = document.getElementById("security-audit-body");

    status.textContent = results.security_enabled
        ? "Security is enabled. Access is deny-by-default."
        : "Security is disabled. File Manager uses its backward-compatible allow-all policy.";

    function renderRows(body, records, columns, emptyMessage) {
        body.replaceChildren();
        if (!records || records.length === 0) {
            const row = document.createElement("tr");
            const cell = document.createElement("td");
            cell.colSpan = columns.length;
            cell.textContent = emptyMessage;
            row.appendChild(cell);
            body.appendChild(row);
            return;
        }
        records.forEach(function (record) {
            const row = document.createElement("tr");
            columns.forEach(function (column) {
                const cell = document.createElement("td");
                const value = column(record);
                cell.textContent = value === null || value === undefined || value === ""
                    ? "—"
                    : typeof value === "object" ? JSON.stringify(value) : String(value);
                row.appendChild(cell);
            });
            body.appendChild(row);
        });
    }

    renderRows(usersBody, results.security_users || [], [
        user => user.username,
        user => (user.roles || []).join(", ") || "No roles (deny by default)",
        user => user.active ? "Active" : "Disabled"
    ], "No users configured.");

    renderRows(rolesBody, results.security_roles || [], [
        role => role.name,
        role => (role.permissions || []).join(", ") || "No permissions"
    ], "No roles configured.");

    renderRows(eventsBody, results.security_events || [], [
        event => event.timestamp,
        event => event.entity_id,
        event => event.action,
        event => event.resource,
        event => event.outcome,
        event => event.message
    ], "No security events recorded.");

    renderRows(auditBody, results.audit_log || [], [
        record => record.timestamp,
        record => record.actor,
        record => record.action,
        record => record.entity_id,
        record => record.outcome,
        record => record.message
    ], "No audit records available.");
}
