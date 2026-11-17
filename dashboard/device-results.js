// Render detached Phase 4A snapshots. All workload text is inserted as textContent.
let lastDeviceSnapshot = null;
const deviceFormat = value => Number.isFinite(value) ? Number(value.toFixed(3)).toString() : "—";
const devicePercent = value => Number.isFinite(value) ? deviceFormat(value) + "%" : "—";

function clearDeviceResults() {
    lastDeviceSnapshot = null;
    renderDeviceResults(null);
}

function deviceTable(id, rows, columns, empty) {
    renderMemoryTable(id, rows, columns, empty);
}

function renderDeviceResults(result) {
    lastDeviceSnapshot = result;
    const devices = Object.entries(result?.devices || {});
    const requests = Object.entries(result?.device_requests || {});
    const metrics = result?.device_metrics;
    const elapsed = metrics?.elapsed_time ?? 0;
    document.getElementById("device-result-status").textContent = !result
        ? "Start a simulation to view device results."
        : devices.length ? `Device snapshot at time ${result.current_time}.` : "No devices configured for this simulation.";
    const summary = document.getElementById("device-summary");
    summary.replaceChildren();
    const terminal = requests.filter(([,r]) => r.finish_time !== null);
    const costs = metrics?.requests || {};
    const totals = Object.values(metrics?.devices || {});
    const total = key => totals.every(m => Number.isFinite(m[key]))
        ? totals.reduce((sum, m) => sum + m[key], 0) : null;
    const utilizationTotal = total("utilization");
    const completedTotal = total("completed");
    const values = [
        ["Completed / failed / cancelled", devices.length ? `${total("completed")} / ${total("failed")} / ${total("cancelled")}` : "—"],
        ["Average terminal queue wait (units)", terminal.length ? deviceFormat(terminal.reduce((s,[id]) => s + costs[id].queue_waiting_time, 0) / terminal.length) : "—"],
        ["Average terminal turnaround (units)", terminal.length ? deviceFormat(terminal.reduce((s,[id]) => s + costs[id].turnaround_time, 0) / terminal.length) : "—"],
        ["Aggregate throughput (completed / elapsed unit)", devices.length && elapsed > 0 && Number.isFinite(completedTotal)
            ? deviceFormat(completedTotal / elapsed) : "—"],
        ["Mean utilization across devices", devices.length && elapsed > 0 && Number.isFinite(utilizationTotal)
            ? devicePercent(utilizationTotal / devices.length) : "—"]
    ];
    for (const [label, value] of values) {
        const card = document.createElement("div"); card.className = "metric";
        const heading = document.createElement("h3"), text = document.createElement("p");
        heading.textContent = label; text.textContent = value;
        card.append(heading, text); summary.append(card);
    }
    deviceTable("device-status-body", devices.map(([id, d]) => [id, d.config.device_type, d.config.policy,
        d.state, d.active_request_id, d.queue.length, d.head_position, d.direction]), 8, "No registered devices.");
    // Status text remains legible without relying on color alone.
    for (const row of document.getElementById("device-status-body").rows) {
        if (row.cells.length !== 8) continue;
        const cell = row.cells[3];
        const badge = document.createElement("span"); badge.className = "state-badge device-state-" + cell.textContent.toLowerCase();
        badge.textContent = cell.textContent; cell.replaceChildren(badge);
    }
    deviceTable("device-metrics-body", devices.map(([id]) => {
        const m = metrics?.devices[id];
        if (!m) return [id, ...Array(12).fill("—")];
        const finished = requests.filter(([,r]) => r.spec.device_id === id && r.finish_time !== null);
        const turnaround = finished.length ? finished.reduce((sum,[key]) => sum + costs[key].turnaround_time, 0) / finished.length : null;
        return [id, m.completed, m.failed, m.cancelled, finished.length ? deviceFormat(m.average_waiting_time) : "—",
            deviceFormat(turnaround), elapsed > 0 ? devicePercent(m.utilization) : "—",
            elapsed > 0 ? deviceFormat(m.throughput) : "—", deviceFormat(m.busy_time), deviceFormat(m.idle_time),
            deviceFormat(m.unavailable_time), `${m.current_queue_length} / ${m.max_queue_length} / ${elapsed > 0 ? deviceFormat(m.average_queue_length) : "—"}`,
            devices.find(([key]) => key === id)[1].config.device_type === "DISK" ? m.head_movement : "—"];
    }), 13, "No device metrics available.");
    renderDeviceHistory();
    renderDeviceTimeline(result);
    deviceTable("device-events-body", (result?.device_events || []).map(e => [e.timestamp, e.device_id,
        e.entity_id, e.request_id, e.event_type, e.outcome, e.message]), 7, "No device events recorded.");
}

function renderDeviceHistory() {
    const result = lastDeviceSnapshot;
    const filter = document.getElementById("device-history-filter").value.toLowerCase();
    const metrics = result?.device_metrics?.requests || {};
    deviceTable("device-history-body", Object.entries(result?.device_requests || {}).filter(([id,r]) =>
        `${id} ${r.pid} ${r.spec.device_id}`.toLowerCase().includes(filter)).map(([id,r]) => {
        const m = metrics[id];
        return [id, r.pid, r.spec.device_id, r.spec.operation, r.submitted_at, r.start_time,
            m?.service_start_time, r.finish_time, deviceFormat(m?.queue_waiting_time), deviceFormat(m?.seek_time),
            `${deviceFormat(m?.service_time)} / ${r.resolved_service_time}`, r.state, m?.reason];
    }), 13, filter ? "No matching device requests." : "No device requests submitted yet.");
}

function renderDeviceTimeline(result) {
    const container = document.getElementById("device-timeline"); container.replaceChildren();
    const intervals = [];
    for (const [id, r] of Object.entries(result?.device_requests || {})) {
        const end = r.start_time ?? r.finish_time ?? result.current_time;
        if (end > r.submitted_at) intervals.push({device: r.spec.device_id, label: `${id} Queue waiting`,
            start: r.submitted_at, end, kind: "wait"});
    }
    const kinds = {REQUEST_SEEK: ["seek", "Disk seek"], SERVICE: ["service", "Request service"], SCAN_ENDPOINT: ["endpoint", "SCAN endpoint"]};
    for (const operation of result?.device_timeline || []) {
        const [kind, label] = kinds[operation.kind];
        intervals.push({device: operation.device_id, label: `${operation.request_id ?? "Device"} ${label} (${operation.status})`,
            start: operation.started_at, end: operation.end, kind});
    }
    for (const event of result?.device_events || []) {
        if (["device_failed", "device_cancelled", "device_failure"].includes(event.event_type))
            intervals.push({device: event.device_id, label: `${event.request_id ?? "Device"} ${event.event_type}: ${event.message}`,
                start: event.timestamp, end: event.timestamp, kind: "failure"});
    }
    intervals.sort((a,b) => a.start-b.start);
    const scale = result?.current_time || 1;
    for (const interval of intervals) {
        const row = document.createElement("div"); row.className = "device-timeline-row";
        const label = document.createElement("div");
        label.textContent = `${interval.device} · ${interval.label} | ${interval.start}–${interval.end}`;
        const track = document.createElement("div"); track.className = "device-track";
        const bar = document.createElement("div"); bar.className = "device-bar device-" + interval.kind;
        bar.style.marginLeft = 100 * interval.start / scale + "%";
        bar.style.width = 100 * (interval.end-interval.start) / scale + "%";
        bar.title = label.textContent;
        track.append(bar); row.append(label, track); container.append(row);
    }
    if (!intervals.length) container.textContent = "No device activity recorded.";
}

document.getElementById("device-history-filter").oninput = renderDeviceHistory;
