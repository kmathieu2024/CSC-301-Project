// Accepted Network configuration is separate from editable drafts and snapshots.
const networkField = id => document.getElementById(id);
const networkEditing = {node: null, link: null, request: null, event: null};
const networkInvalidDrafts = new Set();
let networkDemoLoading = false;
const networkBase = () => networkClone(configuredNetwork || {
    enabled: false, nodes: [], links: [], events: [], hop_limit: 16
});

function networkFormNumber(id, integer = false) {
    const input = networkField(id), text = input.value.trim();
    if (!text) throw new Error(input.labels[0].textContent + " is required.");
    const value = Number(text);
    networkNumber(value, input.labels[0].textContent, integer);
    return value;
}

function networkSelect(id, values) {
    const select = networkField(id), previous = select.value;
    select.replaceChildren();
    for (const value of values) select.add(new Option(value, value));
    if (values.includes(previous)) select.value = previous;
}

function updateNetworkEventTarget() {
    const key = networkField("network-event-action").value.startsWith("LINK_") ? "links" : "nodes";
    networkSelect("network-event-target", (configuredNetwork?.[key] || [])
        .map(item => item[key === "links" ? "link_id" : "node_id"]));
}

function networkDraft(kind) {
    const next = networkBase();
    if (kind === "settings") {
        next.enabled = networkField("network-enabled").checked;
        next.hop_limit = networkFormNumber("network-hop-limit", true);
    } else if (kind === "node") {
        const node = {node_id: networkField("network-node-id").value};
        const address = networkField("network-node-address").value;
        if (address) node.address = address;
        if (networkEditing.node === null) {
            next.nodes.push(node);
        } else next.nodes = next.nodes.map(n => n.node_id === networkEditing.node ? node : n);
    } else if (kind === "link") {
        const link = {
            link_id: networkField("network-link-id").value,
            source: networkField("network-link-source").value,
            destination: networkField("network-link-destination").value,
            bandwidth_bytes_per_time_unit: networkFormNumber("network-bandwidth"),
            propagation_delay: networkFormNumber("network-delay"),
            routing_cost: networkFormNumber("network-cost"),
            queue_policy: networkField("network-policy").value,
            queue_capacity_packets: networkFormNumber("network-capacity", true)
        };
        if (networkEditing.link === null) next.links.push(link);
        else next.links = next.links.map(l => l.link_id === networkEditing.link ? link : l);
    } else if (kind === "event") {
        const action = networkField("network-event-action").value;
        const event = {timestamp: networkFormNumber("network-event-time"), action,
            [action.startsWith("LINK_") ? "link_id" : "node_id"]: networkField("network-event-target").value};
        if (networkEditing.event === null) next.events.push(event);
        else next.events[networkEditing.event] = event;
    } else {
        const process = processes.find(p => p.pid === networkField("network-request-pid").value);
        if (!process) throw new Error("Add or select an existing process first.");
        const offset = networkFormNumber("network-request-offset", true);
        if (networkEditing.request === null && Object.hasOwn(process.network_requests || {}, offset))
            throw new Error("That CPU offset already has a network request. Use Edit or Remove.");
        const request = {
            source: networkField("network-request-source").value,
            destination: networkField("network-request-destination").value,
            size_bytes: networkFormNumber("network-size", true),
            priority: networkFormNumber("network-priority", true)
        };
        const requests = {...process.network_requests, [offset]: request};
        const candidates = processes.map(p => p === process ? {...p, network_requests: requests} : p);
        validateNetworkCandidate(next, candidates);
        return {next, apply: () => { process.network_requests = requests; }};
    }
    validateNetworkCandidate(next);
    return {next, apply: () => {}};
}

function networkCommit(next, candidates = processes, apply = () => {}) {
    const validated = validateNetworkCandidate(next, candidates);
    configuredNetwork = validated;
    apply();
    renderNetworkInputs();
    inputChanged();
}

function clearNetworkDraft(kind) {
    if (busy) return;
    networkField(`network-${kind}-form`).reset();
    networkInvalidDrafts.delete(kind);
    networkField(`network-${kind}-error`).textContent = "";
    if (kind === "settings") return;
    networkEditing[kind] = null;
    networkField(`network-${kind}-edit-status`).textContent = "";
    if (kind === "node" || kind === "link") networkField(`network-${kind}-id`).readOnly = false;
    if (kind === "request") {
        networkField("network-request-pid").disabled = false;
        networkField("network-request-offset").readOnly = false;
    }
    updateNetworkEventTarget();
}

function applyNetworkConfiguration(network) {
    configuredNetwork = network;
    for (const kind of ["node", "link", "request", "event"]) clearNetworkDraft(kind);
    networkInvalidDrafts.clear();
    networkField("network-settings-error").textContent = "";
    networkField("network-enabled").checked = network?.enabled || false;
    networkField("network-hop-limit").value = network?.hop_limit ?? 16;
    networkField("network-demo-status").textContent = "";
    renderNetworkInputs();
}

function validateNetworkDrafts() {
    let valid = true;
    for (const kind of [...networkInvalidDrafts]) {
        try {
            networkDraft(kind);
            networkInvalidDrafts.delete(kind);
            networkField(`network-${kind}-error`).textContent = "";
        } catch (error) {
            networkField(`network-${kind}-error`).textContent = error.message;
            valid = false;
        }
    }
    return valid;
}

function networkAction(cell, label, action, text = "Remove") {
    const button = document.createElement("button");
    button.type = "button"; button.className = "memory-secondary";
    button.textContent = text; button.setAttribute("aria-label", label);
    button.onclick = () => { if (!busy) action(); };
    cell.append(button);
}

function networkEdit(kind, identity, record, pid = null) {
    if (busy) return;
    clearNetworkDraft(kind);
    networkEditing[kind] = identity;
    const fields = {
        node: {node_id: "network-node-id", address: "network-node-address"},
        link: {link_id: "network-link-id", source: "network-link-source", destination: "network-link-destination",
            bandwidth_bytes_per_time_unit: "network-bandwidth", propagation_delay: "network-delay",
            routing_cost: "network-cost", queue_policy: "network-policy", queue_capacity_packets: "network-capacity"},
        request: {source: "network-request-source", destination: "network-request-destination",
            size_bytes: "network-size", priority: "network-priority"},
        event: {timestamp: "network-event-time", action: "network-event-action"}
    };
    for (const [key, id] of Object.entries(fields[kind])) networkField(id).value = record[key] ?? (kind === "request" && key === "priority" ? 1 : "");
    if (kind === "event") {
        updateNetworkEventTarget(); networkField("network-event-target").value = record.link_id ?? record.node_id;
    }
    if (kind === "node" || kind === "link") networkField(`network-${kind}-id`).readOnly = true;
    if (kind === "request") {
        networkField("network-request-pid").value = pid;
        networkField("network-request-pid").disabled = true;
        networkField("network-request-offset").value = identity.offset;
        networkField("network-request-offset").readOnly = true;
    }
    networkField(`network-${kind}-edit-status`).textContent = "Editing accepted " + kind + ". Save to apply; Clear to cancel.";
    networkField(`network-${kind}-form`).querySelector("input, select").focus();
}

function networkRemove(kind, action) {
    try {
        action();
        clearNetworkDraft(kind);
    } catch (error) {
        const notice = networkField(`network-${kind}-error`);
        notice.textContent = error.message; notice.focus();
    }
}

function renderNetworkInputs() {
    if (networkEditing.request) {
        const owner = processes.find(p => p.pid === networkEditing.request.pid);
        if (!owner || !Object.hasOwn(owner.network_requests || {}, networkEditing.request.offset))
            clearNetworkDraft("request");
    }
    const network = networkBase();
    for (const id of ["network-link-source", "network-link-destination", "network-request-source", "network-request-destination"])
        networkSelect(id, network.nodes.map(node => node.node_id));
    networkSelect("network-request-pid", processes.map(process => process.pid));
    updateNetworkEventTarget();
    for (const kind of ["node", "link", "request", "event"]) networkField(`network-${kind}-body`).replaceChildren();
    const row = (kind, values) => {
        const result = networkField(`network-${kind}-body`).insertRow();
        for (const value of values) result.insertCell().textContent = value ?? "—";
        return result.insertCell();
    };
    for (const node of network.nodes) {
        const cell = row("node", [node.node_id, node.address]);
        networkAction(cell, "Edit node " + node.node_id, () => networkEdit("node", node.node_id, node), "Edit");
        networkAction(cell, "Remove node " + node.node_id, () => networkRemove("node", () => {
            const next = networkBase(); next.nodes = next.nodes.filter(n => n.node_id !== node.node_id);
            networkCommit(next);
        }));
    }
    for (const link of network.links) {
        const cell = row("link", [link.link_id, `${link.source} → ${link.destination}`,
            link.bandwidth_bytes_per_time_unit, link.propagation_delay, link.routing_cost,
            link.queue_policy, link.queue_capacity_packets]);
        networkAction(cell, "Edit link " + link.link_id, () => networkEdit("link", link.link_id, link), "Edit");
        networkAction(cell, "Remove link " + link.link_id, () => networkRemove("link", () => {
            const next = networkBase(); next.links = next.links.filter(l => l.link_id !== link.link_id);
            networkCommit(next);
        }));
    }
    for (const process of processes) for (const [offset, request] of Object.entries(process.network_requests || {}).sort((a, b) => Number(a[0]) - Number(b[0]))) {
        const cell = row("request", [process.pid, offset, request.source, request.destination,
            request.size_bytes, request.priority ?? 1]);
        const label = `${process.pid} offset ${offset}`;
        networkAction(cell, "Edit network request " + label,
            () => networkEdit("request", {pid: process.pid, offset}, request, process.pid), "Edit");
        networkAction(cell, "Remove network request " + label, () => networkRemove("request", () => {
            const requests = {...process.network_requests}; delete requests[offset];
            const candidates = processes.map(p => p === process ? {...p, network_requests: requests} : p);
            networkCommit(networkBase(), candidates, () => { process.network_requests = requests; });
        }));
    }
    network.events.forEach((event, index) => {
        const cell = row("event", [event.timestamp, event.action, event.link_id ?? event.node_id]);
        networkAction(cell, "Edit topology event " + (index + 1), () => networkEdit("event", index, event), "Edit");
        networkAction(cell, "Remove topology event " + (index + 1), () => networkRemove("event", () => {
            const next = networkBase(); next.events.splice(index, 1); networkCommit(next);
        }));
    });
    for (const [kind, columns] of [["node", 3], ["link", 8], ["request", 7], ["event", 4]])
        if (!networkField(`network-${kind}-body`).rows.length)
            renderMemoryTable(`network-${kind}-body`, [], columns, `No ${kind === "request" ? "packet requests" : kind + "s"} configured.`);
}

for (const kind of ["settings", "node", "link", "request", "event"]) {
    networkField(`network-${kind}-form`).onsubmit = event => {
        event.preventDefault();
        if (busy) return;
        try {
            const {next, apply} = networkDraft(kind);
            networkCommit(next, processes, apply);
            networkInvalidDrafts.delete(kind);
            networkField(`network-${kind}-error`).textContent = "";
            if (kind !== "settings") clearNetworkDraft(kind);
        } catch (error) {
            networkInvalidDrafts.add(kind);
            networkField(`network-${kind}-error`).textContent = error.message;
            networkField(`network-${kind}-error`).focus();
            if (kind === "settings") networkField("network-enabled").checked = configuredNetwork?.enabled || false;
        }
    };
    if (kind !== "settings") networkField(`network-${kind}-clear`).onclick = () => clearNetworkDraft(kind);
}
networkField("network-enabled").onchange = () => {
    if (!busy) networkField("network-settings-form").requestSubmit();
};
networkField("network-event-action").onchange = updateNetworkEventTarget;
networkField("load-network-demo").onclick = async () => {
    if (busy || networkDemoLoading) return;
    networkDemoLoading = true; networkField("load-network-demo").disabled = true;
    try {
        const response = await fetch("network-demo.json");
        if (!response.ok) throw new Error("Could not load network demonstration.");
        const data = parseInputJson(await response.text());
        if (busy) throw new Error("Pause before replacing the workload.");
        loadWorkload(data);
        networkField("network-demo-status").textContent = "Loaded the network failure scenario. Use Process Manager Run or Step. Current Security settings are unchanged.";
    } catch (error) { networkField("network-demo-status").textContent = error.message; }
    finally { networkDemoLoading = false; networkField("load-network-demo").disabled = busy; }
};
renderNetworkInputs();
clearNetworkResults();
