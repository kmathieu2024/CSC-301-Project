// Display memory statistics and frame allocation for the current simulation.

function renderMemoryManager(results) {
    const metrics = results.memory_metrics;
    const frames = results.memory_frames || [];

    const values = {
        "memory-references": metrics?.references ?? "—",
        "memory-hits": metrics?.hits ?? "—",
        "memory-faults": metrics?.faults ?? "—",
        "memory-evictions": metrics?.evictions ?? "—",
        "memory-fault-rate": metrics
            ? (metrics.fault_rate * 100).toFixed(1) + "%"
            : "—",
        "memory-used-frames": metrics?.used_frames ?? "—",
        "memory-hit-rate": metrics
            ? ((metrics.references ? metrics.hits / metrics.references : 0) * 100).toFixed(1) + "%"
            : "—",
        "memory-total-fault-latency": metrics?.fault_latency ?? "—"
    };

    for (const [id, value] of Object.entries(values)) {
        document.getElementById(id).textContent = value;
    }

    renderMemoryMappings(frames, Boolean(metrics));
    renderMemoryHistory(results.events || [], Boolean(metrics));
    renderProcessTransitions(results.events || []);

    const container = document.getElementById("memory-frame-grid");
    container.replaceChildren();

    if (!metrics) {
        container.textContent =
            "Memory Manager is not enabled for this simulation.";
        return;
    }

    frames.forEach(function (frame) {
        const card = document.createElement("div");
        const occupied = frame.pid !== null;

        card.className = occupied
            ? "memory-frame occupied"
            : "memory-frame empty";

        const heading = document.createElement("strong");
        heading.textContent = "Frame " + frame.frame;

        const content = document.createElement("p");
        content.textContent = occupied
            ? "Process: " + frame.pid + " | Page: " + frame.page
            : "Empty";

        card.append(heading, content);
        container.appendChild(card);
    });
}
// Rebuild from the accumulated snapshot, never append to earlier snapshots.
// textContent keeps process IDs and explicitly recorded messages inert.
function renderMemoryTable(bodyId, rows, columnCount, emptyMessage) {
    const body = document.getElementById(bodyId);
    body.replaceChildren();
    if (!rows.length) {
        const cell = body.insertRow().insertCell();
        cell.colSpan = columnCount;
        cell.textContent = emptyMessage;
        return;
    }
    for (const values of rows) {
        const row = body.insertRow();
        for (const value of values) row.insertCell().textContent = value ?? "—";
    }
}

function renderMemoryMappings(frames, enabled) {
    const residents = frames.filter(frame => frame.pid !== null);
    renderMemoryTable("memory-mapping-body", residents.map(frame => [
        frame.pid, frame.page, frame.frame
    ]), 3, enabled
        ? "No resident pages. Pages are released when processes terminate."
        : "Memory simulation is disabled; no resident mappings.");
}

function renderMemoryHistory(events, enabled) {
    const references = events.filter(event => event.manager === "memory" && event.action === "reference");
    renderMemoryTable("memory-history-body", references.map(event => [
        event.timestamp, event.entity_id, event.address, event.page, event.frame,
        event.physical_address,
        event.event_type === "page_hit" ? "Hit" : event.event_type === "page_fault" ? "Fault" : event.event_type,
        event.evicted?.[0], event.evicted?.[1], event.duration
    ]), 10, enabled ? "No executed memory references yet." : "Memory simulation is disabled; no memory activity.");
}

function renderProcessTransitions(events) {
    // Preserve backend order, even when multiple transitions share a timestamp.
    const transitions = events.filter(event => event.manager === "process" &&
        event.previous_state && event.new_state && event.previous_state !== event.new_state);
    renderMemoryTable("memory-transition-body", transitions.map(event => [
        event.timestamp, event.entity_id, event.previous_state, event.new_state,
        event.event_type, event.action
    ]), 6, "No process state transitions recorded yet.");
}
