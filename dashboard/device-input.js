// Accepted device inputs are independent of form drafts and simulation snapshots.
const deviceField = id => document.getElementById(id);
let deviceDemoLoading = false;
const deviceInvalidDrafts = new Set();

function deviceFormNumber(id, optional = false) {
    const text = deviceField(id).value.trim();
    if (!text) {
        if (optional) return null;
        throw new Error(deviceField(id).labels[0].textContent + " is required.");
    }
    const value = Number(text);
    deviceNumber(value, deviceField(id).labels[0].textContent);
    return value;
}

function updateDeviceType() {
    const disk = deviceField("device-type").value === "DISK";
    deviceField("device-disk-fields").hidden = !disk;
    deviceField("device-disk-fields").disabled = !disk;
    for (const option of deviceField("device-policy").options)
        option.disabled = !disk && option.value !== "FCFS";
    if (!disk) deviceField("device-policy").value = "FCFS";
}

function deviceConfigDraft() {
    const input = {
        device_id: deviceField("device-id").value,
        device_type: deviceField("device-type").value,
        policy: deviceField("device-policy").value,
        default_service_time: deviceFormNumber("device-service")
    };
    if (input.device_type === "DISK") input.disk = {
        min_cylinder: deviceFormNumber("device-min"),
        max_cylinder: deviceFormNumber("device-max"),
        initial_head: deviceFormNumber("device-head"),
        initial_direction: deviceField("device-direction").value,
        seek_time_per_cylinder: deviceFormNumber("device-seek")
    };
    return validateDevices([...configuredDevices, input]).at(-1);
}

function updateDeviceTarget() {
    const device = configuredDevices.find(d => d.device_id === deviceField("device-request-target").value);
    const select = deviceField("device-request-operation");
    const previous = select.value;
    select.replaceChildren();
    for (const value of deviceOperations[device?.device_type] || []) select.add(new Option(value, value));
    if ([...select.options].some(o => o.value === previous)) select.value = previous;
    const disk = device?.device_type === "DISK";
    deviceField("device-request-cylinder-field").hidden = !disk;
    deviceField("device-request-cylinder").disabled = !disk;
}

function deviceRequestDraft() {
    const process = processes.find(p => p.pid === deviceField("device-request-pid").value);
    if (!process) throw new Error("Add or select an existing process first.");
    const offset = deviceFormNumber("device-request-offset");
    if (Object.hasOwn(process.device_requests || {}, offset)) throw new Error("That offset already has a request. Remove it before replacing it.");
    const request = {
        device_id: deviceField("device-request-target").value,
        operation: deviceField("device-request-operation").value
    };
    const device = configuredDevices.find(d => d.device_id === request.device_id);
    if (device?.disk) request.cylinder = deviceFormNumber("device-request-cylinder");
    const service = deviceFormNumber("device-request-service", true);
    if (service !== null) request.service_time = service;
    validateDeviceRequests({[offset]: request}, process.burst_time);
    return {process, offset, request};
}

function validateDeviceDrafts() {
    let valid = true;
    for (const [id, validate] of [["device-config-error", deviceConfigDraft], ["device-request-error", deviceRequestDraft]]) {
        if (!deviceInvalidDrafts.has(id)) continue;
        try { validate(); deviceField(id).textContent = ""; deviceInvalidDrafts.delete(id); }
        catch (error) { deviceField(id).textContent = error.message; valid = false; }
    }
    return valid;
}

function clearDeviceDrafts() {
    deviceInvalidDrafts.clear();
    deviceField("device-form").reset();
    deviceField("device-request-form").reset();
    for (const id of ["device-config-error", "device-request-error", "device-demo-status"]) deviceField(id).textContent = "";
    updateDeviceType();
}

function deviceAction(cell, label, action) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "memory-secondary";
    button.textContent = "Remove";
    button.setAttribute("aria-label", label);
    button.onclick = () => { if (!busy) action(); };
    cell.append(button);
}

function renderDeviceInputs() {
    const body = deviceField("device-config-body");
    body.replaceChildren();
    for (const device of configuredDevices) {
        const row = body.insertRow();
        const disk = device.disk;
        for (const value of [device.device_id, device.device_type, device.policy, device.default_service_time,
            disk ? `${disk.min_cylinder}–${disk.max_cylinder}; head ${disk.initial_head} ${disk.initial_direction}; ${disk.seek_time_per_cylinder} units/cylinder` : "—"])
            row.insertCell().textContent = value;
        deviceAction(row.insertCell(), "Remove device " + device.device_id, () => {
            if (processes.some(p => Object.values(p.device_requests || {}).some(r => r.device_id === device.device_id))) {
                deviceField("device-config-error").textContent = "Remove requests referencing this device first.";
                return;
            }
            configuredDevices.splice(configuredDevices.indexOf(device), 1);
            deviceField("device-config-error").textContent = "";
            renderDeviceInputs(); inputChanged();
        });
    }
    if (!configuredDevices.length) renderMemoryTable("device-config-body", [], 6, "No devices configured.");
    for (const [id, values] of [["device-request-pid", processes.map(p => p.pid)],
        ["device-request-target", configuredDevices.map(d => d.device_id)]]) {
        const select = deviceField(id), previous = select.value;
        select.replaceChildren();
        for (const value of values) select.add(new Option(value, value));
        if (values.includes(previous)) select.value = previous;
    }
    updateDeviceTarget();
    const requests = deviceField("device-input-body");
    requests.replaceChildren();
    for (const process of processes) for (const [offset, request] of Object.entries(process.device_requests || {}).sort((a,b) => Number(a[0])-Number(b[0]))) {
        const row = requests.insertRow();
        for (const value of [process.pid, offset, request.device_id, request.operation, request.cylinder ?? "—", request.service_time ?? "Device default"])
            row.insertCell().textContent = value;
        deviceAction(row.insertCell(), `Remove device request ${process.pid} offset ${offset}`, () => {
            delete process.device_requests[offset];
            deviceField("device-request-error").textContent = "";
            renderDeviceInputs(); inputChanged();
        });
    }
    if (!requests.children.length) renderMemoryTable("device-input-body", [], 7, "No device requests configured.");
}

deviceField("device-form").onsubmit = event => {
    event.preventDefault();
    if (busy) return;
    try {
        const device = deviceConfigDraft();
        deviceInvalidDrafts.delete("device-config-error");
        configuredDevices.push(device);
        deviceField("device-config-error").textContent = "";
        deviceField("device-id").value = "";
        renderDeviceInputs(); inputChanged();
    } catch (error) {
        deviceInvalidDrafts.add("device-config-error");
        deviceField("device-config-error").textContent = error.message;
    }
};
deviceField("device-request-form").onsubmit = event => {
    event.preventDefault();
    if (busy) return;
    try {
        const {process, offset, request} = deviceRequestDraft();
        deviceInvalidDrafts.delete("device-request-error");
        process.device_requests = {...process.device_requests, [offset]: request};
        deviceField("device-request-error").textContent = "";
        renderDeviceInputs(); inputChanged();
    } catch (error) {
        deviceInvalidDrafts.add("device-request-error");
        deviceField("device-request-error").textContent = error.message;
    }
};
deviceField("device-type").onchange = updateDeviceType;
deviceField("device-request-target").onchange = updateDeviceTarget;
for (const [button, form, error, update] of [
    ["device-clear-draft", "device-form", "device-config-error", updateDeviceType],
    ["device-request-clear-draft", "device-request-form", "device-request-error", updateDeviceTarget]
]) deviceField(button).onclick = () => {
    if (busy) return;
    deviceField(form).reset();
    deviceInvalidDrafts.delete(error);
    deviceField(error).textContent = "";
    update();
};
deviceField("load-device-demo").onclick = async () => {
    if (busy || deviceDemoLoading) return;
    deviceDemoLoading = true;
    const generation = ++workloadImportGeneration;
    deviceField("load-device-demo").disabled = true;
    try {
        const response = await fetch("device-demo.json");
        if (!response.ok) throw new Error("Could not load device demonstration.");
        const data = parseInputJson(await response.text());
        if (busy) throw new Error("Pause the simulation before replacing its workload.");
        await loadWorkload(data, generation);
        deviceField("device-demo-status").textContent = "Loaded 3 devices and 12 requests. Use Run or Step in Process Manager.";
    } catch (error) { deviceField("device-demo-status").textContent = error.message; }
    finally { deviceDemoLoading = false; deviceField("load-device-demo").disabled = busy; }
};
updateDeviceType();
renderDeviceInputs();
clearDeviceResults();
