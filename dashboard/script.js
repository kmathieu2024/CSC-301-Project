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

    if (!Number.isSafeInteger(priority)) {
        processError.textContent = "Priority must be a whole number.";
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

            if (!Number.isSafeInteger(priority)) {
                throw new Error(rowLabel + "priority must be a whole number.");
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

function checkSimulationInput() {
    simulationError.textContent = "";
    simulationStatus.textContent = "Simulation not started.";

    if (!validateConfiguration()) {
        document.querySelector('input[aria-invalid="true"]').focus();
        return;
    }

    try {
        const input = collectSimulationInput();

        simulationStatus.textContent =
            input.algorithm + " inputs are valid. " +
            "Backend connection is required to simulate.";
    } catch (error) {
        simulationError.textContent = error.message;
    }
}

document.getElementById("run-button").addEventListener(
    "click", checkSimulationInput
);

document.getElementById("step-button").addEventListener(
    "click", checkSimulationInput
);

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
