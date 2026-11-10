let fileRequestEdit = null;
let fileRequestValidationActive = false;

function clearFileRequestErrors() {
    document.getElementById("file-request-error").textContent = "";
    for (const field of ["offset", "command"]) {
        const input = document.getElementById("file-request-" + field);
        document.getElementById(input.id + "-error").textContent = "";
        input.removeAttribute("aria-invalid");
    }
}

function validateFileRequestFields() {
    clearFileRequestErrors();
    const process = processes.find(p => p.pid === document.getElementById("file-request-pid").value);
    let valid = Boolean(process);
    if (!process) document.getElementById("file-request-error").textContent = "Select an existing process first.";
    for (const field of ["offset", "command"]) {
        const input = document.getElementById("file-request-" + field);
        try {
            if (field === "offset") {
                const offset = readWholeNumber(input.id, "CPU-execution offset", 0);
                if (process && offset >= process.burst_time)
                    throw new Error("CPU-execution offset must be less than burst time (" + process.burst_time + ").");
                if (process && Object.hasOwn(process.file_requests ?? {}, offset) &&
                    (!fileRequestEdit || fileRequestEdit.offset !== String(offset)))
                    throw new Error("That offset already exists. Use Edit to change its request.");
            } else if (!input.value.trim()) {
                throw new Error("File command must be nonempty text.");
            }
        } catch (problem) {
            document.getElementById(input.id + "-error").textContent = problem.message;
            input.setAttribute("aria-invalid", "true");
            valid = false;
        }
    }
    return valid;
}

function cancelFileRequestEdit() {
    fileRequestEdit = null;
    fileRequestValidationActive = false;
    document.getElementById("file-request-form").reset();
    document.getElementById("file-request-pid").disabled = false;
    document.getElementById("file-request-save").textContent = "Add Request";
    document.getElementById("file-request-cancel").hidden = true;
    clearFileRequestErrors();
}

function renderFileRequestEditor() {
    const select = document.getElementById("file-request-pid");
    const selected = select.value;
    // Object identity also detects imports that replace a process with the same PID.
    if (fileRequestEdit && !processes.includes(fileRequestEdit.process)) cancelFileRequestEdit();
    select.replaceChildren();
    for (const process of processes) {
        const option = document.createElement("option");
        option.value = option.textContent = process.pid;
        select.appendChild(option);
    }
    if (processes.some(p => p.pid === selected)) select.value = selected;
    const body = document.getElementById("file-request-table-body");
    body.replaceChildren();
    for (const process of processes) {
        const requests = Object.entries(process.file_requests ?? {}).sort((a, b) => Number(a[0]) - Number(b[0]));
        for (const [offset, command] of requests) {
            const row = body.insertRow();
            for (const value of [process.pid, offset, command]) row.insertCell().textContent = value;
            const actions = row.insertCell();
            for (const action of ["Edit", "Remove"]) {
                const button = document.createElement("button");
                button.type = "button";
                button.className = "memory-secondary";
                button.textContent = action;
                button.setAttribute("aria-label", action + " file request " + process.pid + " offset " + offset);
                button.onclick = function () {
                    if (busy) return;
                    cancelFileRequestEdit();
                    if (action === "Remove") {
                        delete process.file_requests[offset];
                        renderFileRequestEditor();
                        inputChanged();
                    } else {
                        fileRequestEdit = {process, offset};
                        select.value = process.pid;
                        select.disabled = true;
                        document.getElementById("file-request-offset").value = offset;
                        document.getElementById("file-request-command").value = command;
                        document.getElementById("file-request-save").textContent = "Save Request";
                        document.getElementById("file-request-cancel").hidden = false;
                    }
                };
                actions.appendChild(button);
            }
        }
    }
    if (!body.children.length) {
        const cell = body.insertRow().insertCell();
        cell.colSpan = 4;
        cell.textContent = "No file requests configured.";
    }
    if (fileRequestValidationActive) validateFileRequestFields();
}

document.getElementById("file-request-form").addEventListener("submit", function (event) {
    event.preventDefault();
    if (busy) return;
    fileRequestValidationActive = true;
    if (!validateFileRequestFields()) return;
    const process = processes.find(p => p.pid === document.getElementById("file-request-pid").value);
    const offset = Number(document.getElementById("file-request-offset").value);
    const command = document.getElementById("file-request-command").value;
    const requests = process.file_requests ?? {};
    // Commit only after all fields pass; never trim or rewrite command text.
    if (fileRequestEdit) delete requests[fileRequestEdit.offset];
    requests[offset] = command;
    process.file_requests = requests;
    cancelFileRequestEdit();
    renderFileRequestEditor();
    inputChanged();
});

for (const field of ["pid", "offset", "command"]) {
    document.getElementById("file-request-" + field).addEventListener("input", function () {
        if (!busy && fileRequestValidationActive) validateFileRequestFields();
    });
}
document.getElementById("file-request-cancel").onclick = function () {
    if (!busy) cancelFileRequestEdit();
};
renderFileRequestEditor();
