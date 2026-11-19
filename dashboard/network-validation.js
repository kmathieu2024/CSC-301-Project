// Network JSON contract mirrors json_inputs.py; execution and RBAC stay in Python.
let configuredNetwork = null;
const networkActions = ["LINK_DOWN", "LINK_UP", "NODE_DOWN", "NODE_UP"];
const networkClone = value => structuredClone(value);

function networkObject(value, fields, name) {
    if (!isObject(value)) throw new Error(name + " must be an object.");
    for (const key of Object.keys(value))
        if (!fields.includes(key)) throw new Error(name + ": unsupported field " + key);
}

function networkNumber(value, name, integer = false, positive = false) {
    if (typeof value !== "number" || !Number.isFinite(value) || value < 0 ||
        (positive && value === 0) || (integer && !Number.isSafeInteger(value)))
        throw new Error(name + " must be a " + (positive ? "positive " : "nonnegative ") +
            (integer ? "safe integer." : "finite number."));
}

function networkText(value, name) {
    if (typeof value !== "string" || !value || value.trim() !== value)
        throw new Error(name + " must be nonempty text without outer whitespace.");
}

function networkEnum(value, choices, name) {
    if (typeof value !== "string" || !choices.includes(value))
        throw new Error(name + " must be one of " + choices.join(", ") + ".");
}

function networkFiniteJson(value) {
    const pending = [value];
    while (pending.length) {
        const item = pending.pop();
        if (typeof item === "number" && !Number.isFinite(item))
            throw new Error("Network workload JSON numbers must be finite.");
        if (item !== null && typeof item === "object") pending.push(...Object.values(item));
    }
}

function validateNetwork(value) {
    if (value === undefined) return null;
    networkObject(value, ["enabled", "nodes", "links", "hop_limit", "events"], "network");
    const result = {enabled: true, nodes: [], links: [], hop_limit: 16, events: [], ...networkClone(value)};
    if (typeof result.enabled !== "boolean") throw new Error("network.enabled must be boolean.");
    networkNumber(result.hop_limit, "Hop limit", true, true);
    for (const key of ["nodes", "links", "events"])
        if (!Array.isArray(result[key])) throw new Error("network." + key + " must be an array.");
    const nodes = new Set(), links = new Set();
    for (const node of result.nodes) {
        networkObject(node, ["node_id", "address"], "Node");
        networkText(node.node_id, "Node ID");
        if (nodes.has(node.node_id)) throw new Error("Duplicate node ID: " + node.node_id);
        nodes.add(node.node_id);
        if (node.address != null) networkText(node.address, "Node address");
    }
    result.links = result.links.map(input => {
        networkObject(input, ["link_id", "source", "destination", "bandwidth_bytes_per_time_unit",
            "propagation_delay", "routing_cost", "queue_policy", "queue_capacity_packets"], "Link");
        networkText(input.link_id, "Link ID");
        if (links.has(input.link_id)) throw new Error("Duplicate link ID: " + input.link_id);
        links.add(input.link_id);
        networkText(input.source, "Link source"); networkText(input.destination, "Link destination");
        if (!nodes.has(input.source) || !nodes.has(input.destination))
            throw new Error("Link references an unknown node.");
        const link = {propagation_delay: 0, routing_cost: 1, queue_policy: "FIFO",
            queue_capacity_packets: 0, ...input};
        networkNumber(link.bandwidth_bytes_per_time_unit, "Bandwidth", false, true);
        networkNumber(link.propagation_delay, "Propagation delay");
        networkNumber(link.routing_cost, "Routing cost", false, true);
        networkNumber(link.queue_capacity_packets, "Waiting capacity", true);
        networkEnum(link.queue_policy, ["FIFO", "PRIORITY"], "Queue policy");
        return link;
    });
    for (const event of result.events) {
        networkObject(event, ["timestamp", "action", "link_id", "node_id"], "Topology event");
        networkNumber(event.timestamp, "Event timestamp");
        networkEnum(event.action, networkActions, "Topology action");
        const link = event.action.startsWith("LINK_"), target = link ? "link_id" : "node_id";
        if (Object.keys(event).length !== 3 || !Object.hasOwn(event, target))
            throw new Error("Topology event requires timestamp, action, and only " + target + ".");
        networkText(event[target], "Event target");
        if (!(link ? links : nodes).has(event[target])) throw new Error("Unknown topology event target.");
    }
    if (!result.enabled && result.events.length)
        throw new Error("Remove topology events before disabling networking.");
    return result;
}

function validateNetworkRequests(requests = {}, burst, network = configuredNetwork) {
    if (!isObject(requests)) throw new Error("network_requests must be an offset-to-spec object.");
    if (Object.keys(requests).length && !network?.enabled)
        throw new Error("Network requests require networking to be enabled.");
    const nodes = new Set((network?.nodes || []).map(node => node.node_id));
    for (const [offset, request] of Object.entries(requests)) {
        if (!/^(0|[1-9][0-9]*)$/.test(offset) || !Number.isSafeInteger(Number(offset)) || Number(offset) >= burst)
            throw new Error("Network offset must be a canonical nonnegative integer less than burst time.");
        networkObject(request, ["source", "destination", "size_bytes", "priority"], "Packet request");
        for (const key of ["source", "destination"]) {
            networkText(request[key], "Packet " + key);
            if (!nodes.has(request[key])) throw new Error("Unknown packet " + key + ": " + request[key]);
        }
        networkNumber(request.size_bytes, "Packet size", true, true);
        if (Object.hasOwn(request, "priority")) networkNumber(request.priority, "Packet priority", true);
    }
    return networkClone(requests);
}

function validateNetworkCandidate(network, candidates = processes) {
    const validated = validateNetwork(network === null ? undefined : network);
    for (const process of candidates)
        validateNetworkRequests(process.network_requests ?? {}, process.burst_time, validated);
    return validated;
}
