const memoryFields = {
    algorithm: "memory-policy", frame_count: "memory-frame-count",
    page_size: "memory-page-size", fault_latency: "memory-fault-latency"
};
let referenceEdit = null;
const referenceError = document.getElementById("reference-error");

function inputChanged() {
    simulationStatus.textContent = "Simulation not started.";
    document.getElementById("step-button").disabled = false;
}

function applyMemoryConfiguration(memory) {
    document.getElementById("memory-enabled").checked = memory.enabled;
    for (const [key, id] of Object.entries(memoryFields)) document.getElementById(id).value = memory[key];
    cancelReferenceEdit();
    validateMemoryControls();
    inputChanged();
}

function collectMemoryConfiguration() {
    const enabled = document.getElementById("memory-enabled").checked;
    // Disabled fields are irrelevant. Preserve their UI values for re-enabling.
    const memory = {...memoryDefaults, enabled};
    if (enabled) {
        for (const [key, id] of Object.entries(memoryFields)) {
            const text = document.getElementById(id).value;
            if (text === "") throw new Error("Memory configuration fields cannot be empty.");
            memory[key] = key === "algorithm" ? text : Number(text);
        }
    }
    validateMemory(memory);
    for (const process of processes) validateReferences(process.memory_references ?? {}, process.burst_time);
    if (!enabled && processes.some(p => Object.keys(p.memory_references ?? {}).length))
        throw new Error("Memory is disabled but references exist. Enable memory simulation or remove the references to start.");
    return memory;
}

function validateMemoryControls() {
    const enabled = document.getElementById("memory-enabled").checked;
    let valid = true;
    for (const [key, id] of Object.entries(memoryFields)) {
        const input = document.getElementById(id);
        const error = document.getElementById(id + "-error");
        input.disabled = !enabled;
        error.textContent = "";
        input.removeAttribute("aria-invalid");
        if (!enabled) continue;
        try {
            const text = input.value;
            if (text === "") throw new Error("Enter a value.");
            validateMemory({[key]: key === "algorithm" ? text : Number(text)});
        } catch (problem) {
            error.textContent = problem.message;
            input.setAttribute("aria-invalid", "true");
            valid = false;
        }
    }
    const error = document.getElementById("memory-error");
    error.textContent = "";
    try { collectMemoryConfiguration(); } catch (problem) {
        error.textContent = problem.message;
        valid = false;
    }
    return valid;
}

function cancelReferenceEdit() {
    referenceEdit = null;
    document.getElementById("reference-form").reset();
    document.getElementById("reference-pid").disabled = false;
    document.getElementById("reference-save").textContent = "Add Reference";
    document.getElementById("reference-cancel").hidden = true;
    referenceError.textContent = "";
}

function renderReferenceEditor() {
    const select = document.getElementById("reference-pid");
    const selected = select.value;
    if (referenceEdit && !processes.includes(referenceEdit.process)) cancelReferenceEdit();
    select.replaceChildren();
    for (const process of processes) {
        const option = document.createElement("option");
        option.value = option.textContent = process.pid;
        select.appendChild(option);
    }
    if (processes.some(p => p.pid === selected)) select.value = selected;
    const body = document.getElementById("reference-table-body");
    body.replaceChildren();
    for (const process of processes) {
        for (const [offset, address] of Object.entries(process.memory_references ?? {}).sort((a, b) => Number(a[0]) - Number(b[0]))) {
            const row = document.createElement("tr");
            const size = Number(document.getElementById("memory-page-size").value);
            for (const value of [process.pid, offset, address, Number.isSafeInteger(size) && size > 0 ? Math.floor(address / size) : "?"]) {
                const cell = document.createElement("td");
                cell.textContent = value;
                row.appendChild(cell);
            }
            const actions = document.createElement("td");
            for (const action of ["Edit", "Remove"]) {
                const button = document.createElement("button");
                button.type = "button";
                button.textContent = action;
                button.setAttribute("aria-label", action + " reference " + process.pid + " offset " + offset);
                button.onclick = function () {
                    cancelReferenceEdit();
                    if (action === "Remove") {
                        delete process.memory_references[offset];
                        renderReferenceEditor();
                        inputChanged();
                    } else {
                        referenceEdit = {process, offset};
                        select.value = process.pid;
                        select.disabled = true;
                        document.getElementById("reference-offset").value = offset;
                        document.getElementById("reference-address").value = address;
                        document.getElementById("reference-save").textContent = "Save Reference";
                        document.getElementById("reference-cancel").hidden = false;
                    }
                };
                actions.appendChild(button);
            }
            row.appendChild(actions);
            body.appendChild(row);
        }
    }
    if (!body.children.length) {
        const row = body.insertRow();
        const cell = row.insertCell();
        cell.colSpan = 5;
        cell.textContent = "No memory references configured.";
    }
    validateMemoryControls();
}

document.getElementById("reference-form").addEventListener("submit", function (event) {
    event.preventDefault();
    referenceError.textContent = "";
    try {
        const process = processes.find(p => p.pid === document.getElementById("reference-pid").value);
        if (!process) throw new Error("Select an existing process first.");
        const offset = readWholeNumber("reference-offset", "CPU-execution offset", 0);
        const address = readWholeNumber("reference-address", "Virtual address", 0);
        validateReferences({[offset]: address}, process.burst_time);
        const references = process.memory_references ?? {};
        if (Object.hasOwn(references, offset) && (!referenceEdit || referenceEdit.offset !== String(offset)))
            throw new Error("That offset already exists. Use Edit to change its reference.");
        if (referenceEdit) delete references[referenceEdit.offset];
        references[offset] = address;
        process.memory_references = references;
        cancelReferenceEdit();
        renderReferenceEditor();
        inputChanged();
    } catch (problem) { referenceError.textContent = problem.message; }
});
document.getElementById("reference-cancel").onclick = cancelReferenceEdit;
for (const id of ["memory-enabled", ...Object.values(memoryFields)]) {
    document.getElementById(id).addEventListener("input", function () {
        renderReferenceEditor();
        inputChanged();
    });
}
renderReferenceEditor();
