function showPage(pageId, button) {

    const pages = document.querySelectorAll(".page");

    pages.forEach(page => {
        page.classList.remove("active-page");
    });

    document.getElementById(pageId).classList.add("active-page");


    const buttons = document.querySelectorAll(".nav");

    buttons.forEach(btn => {
        btn.classList.remove("active");
    });

    button.classList.add("active");
}

const algorithmSelect = document.getElementById("algorithm");

function updateConfigurationFields() {
    const selectedAlgorithm = algorithmSelect.value;

    document.getElementById("quantum-field").hidden =
        selectedAlgorithm !== "RR";

    document.getElementById("aging-field").hidden =
        selectedAlgorithm !== "PRIORITY";
}

algorithmSelect.addEventListener("change", updateConfigurationFields);

updateConfigurationFields();

// Store the processes entered by the user.
const processes = [];

const processForm = document.getElementById("process-form");
const processError = document.getElementById("process-error");
const processTableBody = document.getElementById("process-table-body");

// Handle clicking Add Process or pressing Enter in the form.
processForm.addEventListener("submit", function (event) {
    // Prevent the form from refreshing the page.
    event.preventDefault();
    if (busy) return;
    processError.textContent = "";

    if (!validateConfiguration()) {
        processError.textContent =
            "Fix the scheduling configuration before adding a process.";
        return;
    }

    const pid = document.getElementById("process-id").value.trim();
    const arrivalInput = document.getElementById("arrival-time").value;
    const burstInput = document.getElementById("burst-time").value;
    const priorityInput = document.getElementById("process-priority").value;

    // Check for empty fields before converting numbers.
    // Number("") would otherwise become 0.
    if (pid === "" || arrivalInput === "" ||
        burstInput === "" || priorityInput === "") {
        processError.textContent = "Please fill in all process fields.";
        return;
    }

    const arrivalTime = Number(arrivalInput);
    const burstTime = Number(burstInput);
    const priority = Number(priorityInput);

    if (!Number.isSafeInteger(arrivalTime) || arrivalTime < 0) {
        processError.textContent =
            "Arrival time must be a whole number of 0 or greater.";
        return;
    }

    if (!Number.isSafeInteger(burstTime) || burstTime < 1) {
        processError.textContent =
            "Burst time must be a whole number of 1 or greater.";
        return;
    }

    if (!Number.isSafeInteger(priority) || priority < 1) {
        processError.textContent =
            "Priority must be a whole number of 1 or greater.";
        return;
    }

    // Each process must have a unique ID.
    const duplicateExists = processes.some(function (process) {
        return process.pid === pid;
    });

    if (duplicateExists) {
        processError.textContent = "That process ID already exists.";
        return;
    }

    // Use the same field names as the Python process model.
    processes.push({
        pid: pid,
        arrival_time: arrivalTime,
        burst_time: burstTime,
        priority: priority
    });

    renderProcessTable();
    inputChanged();
    processForm.reset();
    document.getElementById("process-id").focus();
});

// Rebuild the table using the current processes.
function renderProcessTable() {
    renderReferenceEditor();
    renderFileRequestEditor();
    processTableBody.replaceChildren();

    if (processes.length === 0) {
        const row = document.createElement("tr");
        const cell = document.createElement("td");

        cell.colSpan = 5;
        cell.textContent = "No processes added yet.";

        row.appendChild(cell);
        processTableBody.appendChild(row);
        return;
    }

    processes.forEach(function (process, index) {
        const row = document.createElement("tr");

        const values = [
            process.pid,
            process.arrival_time,
            process.burst_time,
            process.priority
        ];

        values.forEach(function (value) {
            const cell = document.createElement("td");
            cell.textContent = value;
            row.appendChild(cell);
        });

        const actionCell = document.createElement("td");
        const removeButton = document.createElement("button");

        removeButton.type = "button";
        removeButton.textContent = "Remove";
        removeButton.setAttribute("aria-label", "Remove " + process.pid);

        removeButton.addEventListener("click", function () {
            if (busy) return;
            processes.splice(index, 1);
            processError.textContent = "";
            renderProcessTable();
            inputChanged();
        });

        actionCell.appendChild(removeButton);
        row.appendChild(actionCell);
        processTableBody.appendChild(row);
    });
}

const processFileInput = document.getElementById("process-file");
const uploadStatus = document.getElementById("upload-status");

function loadWorkload(data) {
    const validAlgorithms = ["FCFS", "SJF", "SRTF", "RR", "PRIORITY"];

    if (data === null || typeof data !== "object" ||
        Array.isArray(data)) {
        throw new Error("The file must contain a JSON object.");
    }

    if (!validAlgorithms.includes(data.algorithm)) {
        throw new Error(
            "Algorithm must be FCFS, SJF, SRTF, RR, or PRIORITY."
        );
    }

    validateMemoryAssociations(data);
    const importedMemory = validateMemory(data.memory);
    const importedConfiguration = data.configuration;
    if (importedConfiguration !== undefined) {
        if (!isObject(importedConfiguration)) throw new Error("Configuration must be an object.");
        for (const [key, minimum] of [["context_switch_cost", 0], ["time_quantum", 1], ["aging_interval", 1]]) {
            if (importedConfiguration[key] !== undefined &&
                (!Number.isSafeInteger(importedConfiguration[key]) || importedConfiguration[key] < minimum))
                throw new Error(key + " must be a whole number of " + minimum + " or greater.");
        }
    }

    if (!Array.isArray(data.processes) || data.processes.length === 0) {
        throw new Error("The file must contain a nonempty processes list.");
    }

    const uploadedProcesses = [];
    const usedIds = new Set();

    data.processes.forEach(function (process, index) {
        const rowLabel = "Process " + (index + 1) + ": ";

        if (process === null || typeof process !== "object" ||
            Array.isArray(process)) {
            throw new Error(rowLabel + "must be a process object.");
        }

        if (typeof process.pid !== "string" ||
            process.pid.trim() === "") {
            throw new Error(rowLabel + "ID must be nonempty text.");
        }

        const pid = process.pid.trim();

        if (usedIds.has(pid)) {
            throw new Error(rowLabel + "duplicate ID " + pid + ".");
        }

        if (!Number.isSafeInteger(process.arrival_time) ||
            process.arrival_time < 0) {
            throw new Error(
                rowLabel + "arrival time must be a whole number of 0 or greater."
            );
        }

        if (!Number.isSafeInteger(process.burst_time) ||
            process.burst_time < 1) {
            throw new Error(
                rowLabel + "burst time must be a whole number of 1 or greater."
            );
        }

        // Match the Python model's default priority when omitted.
        const priority =
            process.priority === undefined ? 1 : process.priority;

        if (!Number.isSafeInteger(priority) || priority < 1) {
            throw new Error(
                rowLabel + "priority must be a whole number of 1 or greater."
            );
        }

        usedIds.add(pid);

        // Keep memory references and file requests from uploaded processes.
        uploadedProcesses.push({
            ...process,
            pid: pid,
            arrival_time: process.arrival_time,
            burst_time: process.burst_time,
            priority: priority,
            memory_references: validateReferences(
                process.memory_references === undefined ? {} : process.memory_references,
                process.burst_time
            ),
            file_requests: validateFileRequests(
                process.file_requests === undefined ? {} : process.file_requests,
                process.burst_time
            )
        });
    });

    if (!importedMemory.enabled && uploadedProcesses.some(p => Object.keys(p.memory_references).length))
        throw new Error("Enable memory simulation or remove memory references before importing.");
    // File reading is asynchronous: do not replace inputs if Run began meanwhile.
    if (busy) throw new Error("Wait for the simulation to pause before importing.");
    // Update the dashboard only after every process passes validation.
    // Clear Add drafts too: they have no fileRequestEdit object for redraw cleanup.
    cancelFileRequestEdit();
    applyMemoryConfiguration(importedMemory);
    if (importedConfiguration) {
        for (const [key, id] of [["context_switch_cost", "context-switch-cost"], ["time_quantum", "time-quantum"], ["aging_interval", "aging-interval"]]) {
            if (importedConfiguration[key] !== undefined) document.getElementById(id).value = importedConfiguration[key];
        }
    }
    processes.length = 0;

    uploadedProcesses.forEach(function (process) {
        processes.push(process);
    });

    algorithmSelect.value = data.algorithm;
    updateConfigurationFields();
    renderProcessTable();
    inputChanged();

}

processFileInput.addEventListener("change", async function () {
    const file = processFileInput.files[0];

    if (busy || !file) {
        return;
    }

    processError.textContent = "";
    uploadStatus.textContent = "";

    try {
        // Reading a file takes time, so await waits for its text.
        const fileText = await file.text();
        const data = parseInputJson(fileText);

        loadWorkload(data);

        uploadStatus.textContent =
            "Loaded " + processes.length + " processes from " + file.name + ".";
    } catch (error) {
        if (error instanceof SyntaxError) {
            processError.textContent = "The file contains invalid JSON.";
        } else {
            processError.textContent = "Upload failed: " + error.message;
        }
    } finally {
        // Allow selecting the same file again after correcting it.
        processFileInput.value = "";
    }
});

const simulationError = document.getElementById("simulation-error");
const simulationStatus = document.getElementById("simulation-status");

function collectSimulationInput() {
    if (processes.length === 0) {
        throw new Error("Add or upload at least one process first.");
    }

    const algorithm = algorithmSelect.value;
    const validAlgorithms = ["FCFS", "SJF", "SRTF", "RR", "PRIORITY"];

    if (!validAlgorithms.includes(algorithm)) {
        throw new Error("Select a supported scheduling algorithm.");
    }

    const configuration = collectConfiguration(algorithm);

    // Create separate copies so backend changes won't alter the input table.
    // Preserve memory references and file requests for each process.
    const processCopies = processes.map(function (process) {
        return {
            ...process,
            pid: process.pid,
            arrival_time: process.arrival_time,
            burst_time: process.burst_time,
            priority: process.priority,
            memory_references: validateReferences(
                process.memory_references === undefined ? {} : process.memory_references,
                process.burst_time
            ),
            file_requests: validateFileRequests(
                process.file_requests === undefined ? {} : process.file_requests,
                process.burst_time
            )
        };
    });

    // Send the scheduling and memory settings to the simulator.
    return {
        algorithm: algorithm,
        configuration: configuration,
        processes: processCopies,
        memory: collectMemoryConfiguration()
    };
}

function readWholeNumber(inputId, fieldName, minimum) {
    const input = document.getElementById(inputId);
    const text = input.value.trim();
    const value = Number(text);

    if (text === "" || !Number.isSafeInteger(value) || value < minimum) {
        throw new Error(
            fieldName + " must be a whole number of " + minimum + " or greater."
        );
    }

    return value;
}


let started = false;
let running = false;
let busy = false;
let savedInput = "";
let resultsJson = "";

// Capture the HTML's initial placeholders so invalidation restores every display
// consistently, without resetting the backend or manufacturing simulation results.
const initialResultDisplays = Array.from(document.querySelectorAll(
    ".simulation-results .metric p, #process-metrics-body, #gantt-chart, #gantt-empty, " +
    "#memory .metric p, #memory-frame-grid, #memory-mapping-body, #memory-history-body, " +
    "#memory-transition-body, #file-storage-used, #file-tree-container, #file-activity-body"
), element => ({element, initial: element.cloneNode(true)}));

function inputChanged() {
    clearManagerTestMode();
    started = false;
    savedInput = "";
    resultsJson = "";
    updateManagerExportButtons();
    updateManagerTestButtons();
    for (const {element, initial} of initialResultDisplays) {
        element.replaceChildren(...Array.from(initial.childNodes, node => node.cloneNode(true)));
        element.hidden = initial.hidden;
    }
    document.getElementById("export-results-button").disabled = true;
    document.getElementById("step-button").disabled = false;
    simulationError.textContent = "";
    simulationStatus.textContent = "Inputs changed. Run or Step to start a new simulation.";
}

let lockedWorkloadControls = [];

function lockWorkloadEditing() {
    lockedWorkloadControls = Array.from(document.querySelectorAll(
        "#process input, #process select, #process-form button, #process-table-body button, " +
        "#memory input, #memory select, #memory button, " +
        "#file-request-form input, #file-request-form textarea, #file-request-form select, #file-request-form button, #file-request-table-body button"
    )).filter(control => !control.closest("#memory-comparison"))
        .map(control => ({control, disabled: control.disabled}));
    for (const {control} of lockedWorkloadControls) control.disabled = true;
}

function unlockWorkloadEditing() {
    for (const {control, disabled} of lockedWorkloadControls) control.disabled = disabled;
    lockedWorkloadControls = [];
}



// Send information to Python.
async function sendRequest(action, data = {}) {
    const response = await fetch("/api/" + action, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(data)
    });

    // A preview proxy or server error may return plain text/HTML instead of JSON.
    // Report the actual HTTP response rather than exposing JSON.parse's error.
    const body = await response.text();
    let result;
    try {
        result = JSON.parse(body);
    } catch (error) {
        const detail = body.trim().slice(0, 160) || "Empty response";
        throw new Error(
            "Unexpected response from /api/" + action + " (HTTP " + response.status + "). " +
            "The server returned non-JSON content: " + detail +
            " Open http://127.0.0.1:5000/ in an external browser and check the server terminal."
        );
    }

    if (!response.ok) {
        throw new Error(result.error || "Simulation failed");
    }

    return result;
}


// Show simulation progress.
function showResults(result) {
    renderMemoryManager(result);
    renderFileManager(result);
    resultsJson = JSON.stringify(result, null, 4);
    updateManagerExportButtons();
    updateManagerTestButtons();
    document.getElementById("export-results-button").disabled = false;

    const completed = result.processes.filter(function (process) {
        return process.state === "TERMINATED";
    }).length;

    const total = result.processes.length;
    const finished = total > 0 && completed === total;
    renderManagerTestResult(result, finished);
    document.getElementById("step-button").disabled = finished;

    simulationStatus.textContent =
        "Simulation time: " + result.current_time +
        (finished ? " | Completed" : "");

    document.getElementById("result-current-time").textContent =
        result.current_time;

    document.getElementById("result-completed").textContent =
        completed + " / " + total;

    // The backend only calculates these averages after completion.
    document.getElementById("result-average-wait").textContent =
        finished ? result.average_waiting_time.toFixed(2) : "—";

    document.getElementById("result-average-turnaround").textContent =
        finished ? result.average_turnaround_time.toFixed(2) : "—";

    // Utilization excludes idle time and context-switch overhead.
    document.getElementById("result-utilization").textContent =
        result.current_time > 0
            ? result.cpu_utilization.toFixed(2) + "%"
            : "—";

    document.getElementById("result-context-switch-overhead").textContent =
        result.context_switch_overhead ?? "�";

    const throughput = result.current_time > 0
        ? completed / result.current_time
        : 0;

    document.getElementById("result-throughput").textContent =
        result.current_time > 0 ? throughput.toFixed(3) : "—";

    renderGanttChart(result);
    renderProcessMetrics(result);

    return finished;
}


// Download a snapshot of the results currently displayed on the dashboard.
function exportResults() {
    if (!resultsJson) return;

    try {
        downloadText("simulation-results.json", resultsJson + "\n", "application/json");
    } catch (error) {
        simulationError.textContent = "Export failed: " + error.message;
    }
}


// Start the simulation if needed.
async function startSimulation() {
    if (!validateConfiguration()) {
        document.querySelector('input[aria-invalid="true"]').focus();
        return false;
    }

    if (!validateMemoryControls()) {
        const notice = document.getElementById("memory-start-error");
        notice.textContent = "The simulation could not start. Correct the highlighted memory inputs below.";
        notice.hidden = false;
        showPage("memory", document.querySelector("button[onclick*=\"'memory'\"]"));
        notice.focus();
        notice.scrollIntoView({block: "start"});
        return false;
    }
    if (fileRequestValidationActive && !validateFileRequestFields()) {
        showPage("file", document.querySelector("button[onclick*=\"'file'\"]"));
        document.querySelector('#file-request-form [aria-invalid="true"]')?.focus();
        return false;
    }
    if (activeManagerTest && managerTestFinished) {
        // Every completed scenario retry starts with a new engine/file system.
        started = false;
        savedInput = "";
        clearManagerTestVerdict();
    }
    const input = collectSimulationInput();
    const text = JSON.stringify(input);
    lockWorkloadEditing();

    if (!started || savedInput !== text) {
        const result = await sendRequest("start", input);
        started = true;
        savedInput = text;
        showResults(result);
    }

    return true;
}


// Run one step.
async function stepSimulation() {
    if (running || busy) return;

    busy = true;
    updateManagerExportButtons();
    updateManagerTestButtons();
    simulationError.textContent = "";

    try {
        if (await startSimulation()) {
            showResults(await sendRequest("step"));
        }
    } catch (error) {
        simulationError.textContent = error.message;
    } finally {
        busy = false;
        unlockWorkloadEditing();
        updateManagerExportButtons();
        updateManagerTestButtons();
    }
}


// Run until finished or paused.
async function runSimulation() {
    if (running || busy) return;

    busy = true;
    updateManagerExportButtons();
    updateManagerTestButtons();
    simulationError.textContent = "";

    try {
        if (!await startSimulation()) return;

        running = true;
        document.getElementById("pause-button").disabled = false;

        while (running) {
            const finished = showResults(await sendRequest("step"));

            if (finished) break;

            await new Promise(resolve => setTimeout(resolve, 200));
        }
    } catch (error) {
        simulationError.textContent = error.message;
    } finally {
        running = false;
        busy = false;
        unlockWorkloadEditing();
        updateManagerExportButtons();
        updateManagerTestButtons();
        document.getElementById("pause-button").disabled = true;
    }
}


// Pause the simulation.
function pauseSimulation() {
    running = false;
    simulationStatus.textContent += " | Paused";
}


// Reset the simulation.
async function resetSimulation() {
    if (busy) {
        running = false;
        return;
    }

    busy = true;
    updateManagerExportButtons();
    updateManagerTestButtons();
    simulationError.textContent = "";

    try {
        if (started) {
            showResults(await sendRequest("reset"));
            if (activeManagerTest) {
                // Reset still preserves files; the next scenario attempt is fresh.
                started = false;
                savedInput = "";
            }
        } else {
            simulationStatus.textContent = "Simulation not started.";
        }
    } catch (error) {
        simulationError.textContent = error.message;
    } finally {
        busy = false;
        updateManagerExportButtons();
        updateManagerTestButtons();
    }
}


// Connect buttons.
document.getElementById("run-button").onclick = runSimulation;
document.getElementById("pause-button").onclick = pauseSimulation;
document.getElementById("step-button").onclick = stepSimulation;
document.getElementById("reset-button").onclick = resetSimulation;
document.getElementById("export-results-button").onclick = exportResults;

function collectConfiguration(algorithm) {
    const configuration = {
        context_switch_cost: readWholeNumber(
            "context-switch-cost", "Context-switch cost", 0
        )
    };

    if (algorithm === "RR") {
        configuration.time_quantum = readWholeNumber(
            "time-quantum", "Time quantum", 1
        );
    }

    if (algorithm === "PRIORITY") {
        configuration.aging_interval = readWholeNumber(
            "aging-interval", "Aging interval", 1
        );
    }

    return configuration;
}

function validateConfiguration() {
    const algorithm = algorithmSelect.value;
    let allValid = true;

    const fields = [
        {
            id: "context-switch-cost",
            name: "Context-switch cost",
            minimum: 0,
            relevant: true
        },
        {
            id: "time-quantum",
            name: "Time quantum",
            minimum: 1,
            relevant: algorithm === "RR"
        },
        {
            id: "aging-interval",
            name: "Aging interval",
            minimum: 1,
            relevant: algorithm === "PRIORITY"
        }
    ];

    fields.forEach(function (field) {
        const input = document.getElementById(field.id);
        const error = document.getElementById(field.id + "-error");

        // Clear previous errors, including settings now hidden.
        error.textContent = "";
        input.removeAttribute("aria-invalid");

        if (!field.relevant) {
            return;
        }

        try {
            readWholeNumber(field.id, field.name, field.minimum);
        } catch (validationError) {
            error.textContent = validationError.message;
            input.setAttribute("aria-invalid", "true");
            allValid = false;
        }
    });

    return allValid;
}

const configurationInputIds = [
    "context-switch-cost",
    "time-quantum",
    "aging-interval"
];

configurationInputIds.forEach(function (inputId) {
    document.getElementById(inputId).addEventListener("input", function () {
        if (busy) return;
        inputChanged();
        validateConfiguration();
    });
});

algorithmSelect.addEventListener("change", function () {
    if (busy) return;
    inputChanged();
    validateConfiguration();
});

function renderGanttChart(result) {
    const chart = document.getElementById("gantt-chart");
    const emptyMessage = document.getElementById("gantt-empty");

    chart.replaceChildren();
    emptyMessage.hidden = result.timeline.length > 0;

    if (result.timeline.length === 0) {
        return;
    }

    const colors = [
        "#c4b5fd",
        "#93c5fd",
        "#86efac",
        "#f9a8d4",
        "#a5f3fc",
        "#fcd34d"
    ];

    // Keep each process's color consistent across updates.
    const processColors = new Map();

    result.processes.forEach(function (process, index) {
        processColors.set(process.pid, colors[index % colors.length]);
    });

    // Use one scale for every block to preserve duration proportions.
    // Expand short timelines so their labels have room.
    const shortestDuration = Math.min(
        ...result.timeline.map(function (interval) {
            return interval.end - interval.start;
        })
    );

    const pixelsPerUnit = Math.max(40, 90 / shortestDuration);

    result.timeline.forEach(function (interval) {
        const duration = interval.end - interval.start;
        const block = document.createElement("div");

        block.className = "gantt-block";
        block.style.width = duration * pixelsPerUnit + "px";
        block.setAttribute("role", "listitem");

        let label = interval.pid;

        if (interval.pid === "IDLE") {
            label = "Idle";
            block.style.backgroundColor = "#e5e7eb";
        } else if (interval.pid === "CS") {
            label = "Context switch";
            block.style.backgroundColor = "#fde4b5";
        } else {
            block.style.backgroundColor = processColors.get(interval.pid);
        }

        const description =
            label + ": " + interval.start + "–" + interval.end +
            " (duration " + duration + ")";

        block.title = description;
        block.setAttribute("aria-label", description);

        const name = document.createElement("strong");
        name.textContent = label;

        const times = document.createElement("small");
        times.textContent = interval.start + "–" + interval.end;

        block.appendChild(name);
        block.appendChild(times);
        chart.appendChild(block);
    });
}

function renderProcessMetrics(result) {
    const tableBody = document.getElementById("process-metrics-body");
    tableBody.replaceChildren();

    result.processes.forEach(function (process) {
        const row = document.createElement("tr");

        const pidCell = document.createElement("td");
        pidCell.textContent = process.pid;
        row.appendChild(pidCell);

        const stateCell = document.createElement("td");
        const badge = document.createElement("span");

        badge.className =
            "state-badge state-" + process.state.toLowerCase();
        badge.textContent = process.state;

        stateCell.appendChild(badge);
        row.appendChild(stateCell);

        const completed = process.state === "TERMINATED";

        const values = [
            process.arrival_time,
            process.burst_time,
            process.start_time,
            process.completion_time,
            completed ? process.waiting_time : null,
            completed ? process.turnaround_time : null,
            process.response_time,
            process.dispatch_count,
            process.preemption_count
        ];

        values.forEach(function (value) {
            const cell = document.createElement("td");

            // Zero is a valid metric; only missing values become dashes.
            cell.textContent = value === null || value === undefined
                ? "—"
                : value;

            row.appendChild(cell);
        });

        tableBody.appendChild(row);
    });
}
// Display the file system tree and file operation history.

function renderFileManager(results) {
    // Use the snapshot's aggregate metric, never an estimate from inputs or DOM.
    document.getElementById("file-storage-used").textContent = results.file_storage_used ?? "\u2014";
    const container = document.getElementById("file-tree-container");
    const directoryStates = new Map(Array.from(container.querySelectorAll("details[data-path]"),
        details => [details.dataset.path, details.open]));
    container.replaceChildren();

    function createTreeNode(node, ancestors = []) {
        const item = document.createElement("div");
        item.className = "file-tree-node";

        if (node.type === "directory") {
            const details = document.createElement("details");
            const path = [...ancestors, node.name];
            details.dataset.path = JSON.stringify(path);
            details.open = directoryStates.get(details.dataset.path) ?? (node.name === "/");

            const summary = document.createElement("summary");
            summary.textContent = "📁 " + node.name + (node.owner == null ? "" : " | Owner: " + node.owner);
            details.appendChild(summary);

            const children = document.createElement("div");
            children.className = "file-tree-children";

            (node.children || []).forEach(function (child) {
                children.appendChild(createTreeNode(child, path));
            });

            details.appendChild(children);
            item.appendChild(details);
        } else {
            item.textContent = "📄 " + node.name +
                " (" + node.size + " bytes)" + (node.owner == null ? "" : " | Owner: " + node.owner);
        }

        return item;
    }

    if (results.file_tree) {
        container.appendChild(createTreeNode(results.file_tree));
    } else {
        container.textContent = "File Manager is not available.";
    }

    const tbody = document.getElementById("file-activity-body");
    tbody.replaceChildren();

    const events = (results.events || []).filter(function (event) {
        return event.manager === "file";
    });

    if (events.length === 0) {
        const row = document.createElement("tr");
        const cell = document.createElement("td");
        cell.colSpan = 5;
        cell.textContent = "No file operations recorded yet.";
        row.appendChild(cell);
        tbody.appendChild(row);
        return;
    }

    events.forEach(function (event) {
        const row = document.createElement("tr");

        // Show the file command and whether it succeeded.
        const values = [
            event.timestamp ?? "—",
            event.entity_id ?? "—",
            event.action ?? "—",
            event.outcome === "success" ? "Success" : event.outcome === "error" ? "Error" : (event.outcome ?? "Unknown"),
            event.message === "" || event.message === null || event.message === undefined
                ? "No output" : event.message
        ];

        values.forEach(function (value) {
            const cell = document.createElement("td");
            cell.textContent = typeof value === "object"
                ? JSON.stringify(value)
                : String(value);
            row.appendChild(cell);
        });

        tbody.appendChild(row);
    });
}
