// Manager exports use the same serialized snapshot as the existing JSON export.
function displayedManagerEvents(manager) {
    return resultsJson ? (JSON.parse(resultsJson).events || []).filter(event => event.manager === manager) : [];
}

function updateManagerExportButtons() {
    const memory = displayedManagerEvents("memory");
    const files = displayedManagerEvents("file");
    document.getElementById("export-memory-csv").disabled = busy || running || !memory.some(event => event.action === "reference");
    document.getElementById("export-memory-log").disabled = busy || running || !memory.length;
    for (const format of ["csv", "log"])
        document.getElementById("export-file-" + format).disabled = busy || running || !files.length;
}

function downloadText(filename, text, contentType) {
    const blob = new Blob([text], {type: contentType + ";charset=utf-8"});
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    try {
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
    } finally {
        link.remove();
        // Give the browser time to begin the download before releasing the URL.
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
}

function managerCsv(headers, rows) {
    // Quote every field so commas, quotes, Unicode and embedded newlines survive.
    const quote = value => '"' + String(value ?? "").replaceAll('"', '""') + '"';
    return [headers, ...rows].map(row => row.map(quote).join(",")).join("\r\n") + "\r\n";
}

function exportManager(manager, format) {
    if (busy || running) return;
    const events = displayedManagerEvents(manager);
    const selected = manager === "memory" && format === "csv"
        ? events.filter(event => event.action === "reference") : events;
    if (!selected.length) return;
    try {
        if (format === "log") {
            // One original event per line preserves fields and event order exactly.
            downloadText(manager + "-events.log", selected.map(event => JSON.stringify(event)).join("\n") + "\n", "text/plain");
        } else if (manager === "memory") {
            downloadText("memory-history.csv", managerCsv([
                "timestamp", "pid", "virtual_address", "page", "frame", "physical_address",
                "hit_fault", "evicted_pid", "evicted_page", "modeled_fault_latency"
            ], selected.map(event => [event.timestamp, event.entity_id, event.address,
                event.page, event.frame, event.physical_address,
                event.event_type === "page_hit" ? "Hit" : event.event_type === "page_fault" ? "Fault" : event.event_type,
                event.evicted?.[0], event.evicted?.[1], event.duration])), "text/csv");
        } else {
            downloadText("file-activity.csv", managerCsv([
                "timestamp", "pid", "command", "outcome", "details"
            ], selected.map(event => [event.timestamp, event.entity_id, event.action,
                event.outcome, event.message])), "text/csv");
        }
    } catch (error) {
        simulationError.textContent = "Export failed: " + error.message;
    }
}

for (const manager of ["memory", "file"]) {
    for (const format of ["csv", "log"])
        document.getElementById("export-" + manager + "-" + format).onclick = () => exportManager(manager, format);
}
