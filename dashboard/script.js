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

    if (!Number.isSafeInteger(priority) || priority < 0) {
        processError.textContent =
            "Priority must be a whole number of 0 or greater.";
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
    processForm.reset();
    document.getElementById("process-id").focus();
});

// Rebuild the table using the current processes.
function renderProcessTable() {
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
            processes.splice(index, 1);
            processError.textContent = "";
            renderProcessTable();
        });

        actionCell.appendChild(removeButton);
        row.appendChild(actionCell);
        processTableBody.appendChild(row);
    });
}

const processFileInput = document.getElementById("process-file");
const uploadStatus = document.getElementById("upload-status");

processFileInput.addEventListener("change", async function () {
    const file = processFileInput.files[0];

    if (!file) {
        return;
    }

    processError.textContent = "";
    uploadStatus.textContent = "";

    try {
        // Reading a file takes time, so await waits for its text.
        const fileText = await file.text();
        const data = JSON.parse(fileText);

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

        collectConfiguration(data.algorithm);

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
                process.priority === undefined ? 0 : process.priority;

            if (!Number.isSafeInteger(priority) || priority < 0) {
                throw new Error(
                    rowLabel + "priority must be a whole number of 0 or greater."
                );
            }

            usedIds.add(pid);

            uploadedProcesses.push({
                pid: pid,
                arrival_time: process.arrival_time,
                burst_time: process.burst_time,
                priority: priority
            });
        });

        // Update the dashboard only after every process passes validation.
        processes.length = 0;

        uploadedProcesses.forEach(function (process) {
            processes.push(process);
        });

        algorithmSelect.value = data.algorithm;
        updateConfigurationFields();
        renderProcessTable();

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
    const processCopies = processes.map(function (process) {
        return {
            pid: process.pid,
            arrival_time: process.arrival_time,
            burst_time: process.burst_time,
            priority: process.priority
        };
    });

    return {
        algorithm: algorithm,
        configuration: configuration,
        processes: processCopies
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


// Send information to Python.
async function sendRequest(action, data = {}) {
    const response = await fetch("/api/" + action, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(data)
    });

    const result = await response.json();

    if (!response.ok) {
        throw new Error(result.error || "Simulation failed");
    }

    return result;
}


// Show simulation progress.
function showResults(result) {
    resultsJson = JSON.stringify(result, null, 4);
    document.getElementById("export-results-button").disabled = false;

    const completed = result.processes.filter(function (process) {
        return process.state === "TERMINATED";
    }).length;

    const total = result.processes.length;
    const finished = total > 0 && completed === total;
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
        const file = new Blob([resultsJson + "\n"], {
            type: "application/json"
        });
        const url = URL.createObjectURL(file);
        const link = document.createElement("a");
        link.href = url;
        link.download = "simulation-results.json";
        document.body.appendChild(link);
        link.click();
        link.remove();
        // Allow the browser to begin downloading before releasing the URL.
        setTimeout(function () {
            URL.revokeObjectURL(url);
        }, 1000);
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

    const input = collectSimulationInput();
    const text = JSON.stringify(input);

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
    simulationError.textContent = "";

    try {
        if (await startSimulation()) {
            showResults(await sendRequest("step"));
        }
    } catch (error) {
        simulationError.textContent = error.message;
    }

    busy = false;
}


// Run until finished or paused.
async function runSimulation() {
    if (running || busy) return;

    busy = true;
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
    simulationError.textContent = "";

    try {
        if (started) {
            showResults(await sendRequest("reset"));
        } else {
            simulationStatus.textContent = "Simulation not started.";
        }
    } catch (error) {
        simulationError.textContent = error.message;
    } finally {
        busy = false;
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
        simulationStatus.textContent = "Simulation not started.";
        validateConfiguration();
    });
});

algorithmSelect.addEventListener("change", function () {
    simulationStatus.textContent = "Simulation not started.";
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
            process.response_time
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
