// Read-only renderers. Backend fields, not fixture expectations, drive every value.
let lastNetworkSnapshot = null;
const networkFormat = value => {
    if (!Number.isFinite(value)) return "—";
    if (value !== 0 && (Math.abs(value) < 0.001 || Math.abs(value) >= 1e9)) return value.toExponential(3);
    return String(Number(value.toFixed(3)));
};
const networkPercent = value => Number.isFinite(value) && Number.isFinite(value * 100)
    ? networkFormat(value * 100) + "%" : "—";
const netElement = id => document.getElementById(id);

function networkPendingWork(result) {
    return (result?.network_metrics?.in_flight || 0) > 0 || (result?.network_metrics?.queued || 0) > 0 ||
        (result?.network_topology?.events || []).some(event => event.timestamp > result.current_time);
}

function clearNetworkResults() {
    lastNetworkSnapshot = null;
    renderNetworkResults(null);
}

function networkTable(id, rows, columns, empty) {
    renderMemoryTable(id, rows, columns, empty);
}

function renderNetworkGraph(nodes, links) {
    const svg = netElement("network-graph"); svg.replaceChildren();
    const entries = Object.entries(nodes).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0);
    svg.toggleAttribute("hidden", !entries.length);
    netElement("network-graph-empty").textContent = entries.length ? "Arrowheads show directed links; labels and tables show availability without relying on color." : "No registered network nodes.";
    if (!entries.length) return;
    const ns = "http://www.w3.org/2000/svg";
    const make = (tag, attrs = {}, text = null) => {
        const element = document.createElementNS(ns, tag);
        for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, value);
        if (text !== null) element.textContent = text;
        return element;
    };
    const defs = make("defs");
    for (const state of ["up", "down"]) {
        const marker = make("marker", {id: "network-arrow-" + state, viewBox: "0 0 10 10",
            refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse"});
        marker.append(make("path", {d: "M 0 0 L 10 5 L 0 10 z", fill: state === "up" ? "#25634b" : "#b42318"}));
        defs.append(marker);
    }
    svg.append(defs);
    const positions = new Map(entries.map(([id], index) => {
        const angle = entries.length === 1 ? 0 : 2 * Math.PI * index / entries.length;
        return [id, {x: 380 + (entries.length === 1 ? 0 : 255 * Math.cos(angle)),
            y: 195 + (entries.length === 1 ? 0 : 80 * Math.sin(angle))}];
    }));
    const lanes = new Map();
    let left = 0, top = 0, right = 760, bottom = 360;
    Object.entries(links).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).forEach(([id, link]) => {
        const source = positions.get(link.config.source), target = positions.get(link.config.destination);
        if (!source || !target) return;
        // Rank by link ID within each directed pair, without cycling lanes.
        // Reversing direction reverses the normal, separating reciprocal links.
        const pair = JSON.stringify([link.config.source, link.config.destination]);
        const lane = lanes.get(pair) || 0;
        lanes.set(pair, lane + 1);
        const up = link.state === "UP" && nodes[link.config.source]?.state === "UP" && nodes[link.config.destination]?.state === "UP";
        const state = up ? "up" : "down";
        const dx = target.x - source.x, dy = target.y - source.y, length = Math.hypot(dx, dy) || 1;
        const x1 = source.x + dx / length * 24, y1 = source.y + dy / length * 24;
        const x2 = target.x - dx / length * 24, y2 = target.y - dy / length * 24;
        const mx = (source.x + target.x) / 2 - dy / length * (25 + lane * 34);
        const my = (source.y + target.y) / 2 + dx / length * (25 + lane * 34);
        const self = link.config.source === link.config.destination;
        const loopWidth = 95 + lane * 40, loopHeight = 100 + lane * 40;
        const path = self ? `M ${source.x-12} ${source.y-18} C ${source.x-loopWidth} ${source.y-loopHeight}, ${source.x+loopWidth} ${source.y-loopHeight}, ${source.x+12} ${source.y-18}`
            : `M ${x1} ${y1} Q ${mx} ${my} ${x2} ${y2}`;
        // Include control points and labels in the view box for large groups.
        left = Math.min(left, (self ? source.x-loopWidth : mx) - 30);
        right = Math.max(right, (self ? source.x+loopWidth : mx) + 30);
        top = Math.min(top, (self ? source.y-loopHeight : my) - 30);
        bottom = Math.max(bottom, (self ? source.y : my) + 30);
        const edge = make("path", {d: path, fill: "none", stroke: up ? "#25634b" : "#b42318",
            "data-link-id": id,
            "stroke-width": 2, "stroke-dasharray": up ? "" : "6 4", "marker-end": `url(#network-arrow-${state})`});
        edge.append(make("title", {}, `${id}: ${link.config.source} → ${link.config.destination}, ${up ? "UP" : "UNAVAILABLE"}`));
        svg.append(edge);
        svg.append(make("text", {x: self ? source.x : (source.x + target.x) / 4 + mx / 2,
            y: self ? source.y-4.5-loopHeight*0.75-5 : (source.y + target.y) / 4 + my / 2-5,
            "data-link-id": id,
            "text-anchor": "middle", class: "network-svg-label"}, id.length > 18 ? id.slice(0, 18) + "…" : id));
    });
    svg.setAttribute("viewBox", `${left} ${top} ${right-left} ${bottom-top}`);
    for (const [id, node] of entries) {
        const {x, y} = positions.get(id), group = make("g");
        group.append(make("title", {}, `${id}: ${node.state}`));
        group.append(make("circle", {cx: x, cy: y, r: 21, fill: node.state === "UP" ? "#dcfce7" : "#fee2e2", stroke: "#374151"}));
        group.append(make("text", {x, y: y+5, "text-anchor": "middle"}, node.state));
        group.append(make("text", {x, y: y+39, "text-anchor": "middle"}, id.length > 22 ? id.slice(0, 22) + "…" : id));
        svg.append(group);
    }
}

function renderNetworkResults(result) {
    lastNetworkSnapshot = result;
    const topology = result?.network_topology, metrics = result?.network_metrics;
    const nodes = topology?.nodes || {}, links = topology?.links || {};
    const done = result && result.processes.every(p => p.state === "TERMINATED") && !networkPendingWork(result);
    const status = !result ? (configuredNetwork?.enabled
        ? "Network inputs accepted. Start a simulation to view results." : "Networking disabled. Enable it to configure packet execution.")
        : !metrics ? "Networking disabled for this simulation."
        : done ? `Completed network snapshot at time ${networkFormat(result.current_time)}.`
        : metrics.submitted === 0 ? "Pending: no packets submitted yet."
        : `Partial snapshot: ${metrics.in_flight} in flight; ${metrics.queued} waiting.`;
    netElement("network-result-status").textContent = status;
    const summary = netElement("network-summary"); summary.replaceChildren();
    const entries = [
        ["Submitted packets", metrics?.submitted], ["Delivered packets", metrics?.delivered],
        ["Dropped packets", metrics?.dropped], ["Failed packets", metrics?.failed],
        ["Cancelled packets", metrics?.cancelled], ["Mean delivery latency (units)", metrics?.average_delivery_latency],
        ["Mean packet queue wait (units)", metrics?.average_queue_wait],
        ["Throughput (delivered packets / elapsed unit)", metrics?.throughput],
        ["Goodput (payload bytes / elapsed unit)", metrics?.goodput],
        ["Delivered payload (bytes)", metrics?.delivered_payload_bytes]
    ];
    for (const [label, value] of entries) {
        const card = document.createElement("div"); card.className = "metric";
        const heading = document.createElement("h3"), text = document.createElement("p");
        heading.textContent = label; text.textContent = networkFormat(value);
        card.append(heading, text); summary.append(card);
    }
    renderNetworkGraph(nodes, links);
    networkTable("network-node-status-body", Object.entries(nodes).map(([id, node]) =>
        [id, node.config.address, node.state]), 3, "No registered nodes.");
    networkTable("network-link-status-body", Object.entries(links).map(([id, link]) => {
        const available = link.state === "UP" && nodes[link.config.source]?.state === "UP" && nodes[link.config.destination]?.state === "UP";
        return [id, `${link.config.source} → ${link.config.destination}`,
            !available ? "UNAVAILABLE" : link.transmitting_packet_id ? "TRANSMITTING" : "UP",
            link.transmitting_packet_id, link.queue.length, link.propagating_packet_ids.length];
    }), 6, "No registered links.");
    networkTable("network-metrics-body", Object.entries(links).map(([id]) => {
        const m = metrics?.links?.[id];
        return [id, networkFormat(m?.busy_time), networkFormat(m?.idle_time), networkFormat(m?.unavailable_time),
            networkPercent(m?.utilization), `${networkFormat(m?.current_queue_length)} / ${networkFormat(m?.maximum_queue_length)} / ${networkFormat(m?.average_queue_length)}`,
            networkFormat(m?.queue_length_integral), networkFormat(m?.wire_bytes), networkFormat(m?.serialized_bytes)];
    }), 9, "No link performance data.");
    renderNetworkHistory();
    renderNetworkTimeline(result);
    networkTable("network-outcomes-body", Object.values(result?.network_outcomes || {}).map(outcome =>
        [outcome.pid, outcome.request_offset, outcome.packet_id, outcome.timestamp, outcome.outcome, outcome.reason]),
        6, "No process network outcomes.");
    networkTable("network-events-body", (result?.network_events || []).map(event =>
        [event.timestamp, event.entity_id, `${event.packet_id ?? "—"} / ${event.pid ?? "—"}`,
            event.event_type, event.outcome, event.message]), 6, "No network events recorded.");
}

function renderNetworkHistory() {
    const result = lastNetworkSnapshot;
    const filter = netElement("network-history-filter").value.toLowerCase();
    const packets = Object.entries(result?.network_packets || {}).filter(([id, packet]) =>
        `${id} ${packet.pid} ${packet.spec.source} ${packet.spec.destination} ${packet.state} ${packet.reason ?? ""}`.toLowerCase().includes(filter));
    networkTable("network-history-body", packets.map(([id, packet]) => {
        const m = result.network_metrics?.packets?.[id];
        return [id, packet.pid, `${packet.spec.source} → ${packet.spec.destination}`,
            `${packet.spec.size_bytes} / ${packet.spec.priority}`, `${packet.state} / ${packet.reason ?? "—"}`,
            `${networkFormat(packet.submitted_at)} / ${networkFormat(packet.finished_at)}`,
            networkFormat(m?.queue_wait_time), `${networkFormat(m?.transmission_time)} / ${networkFormat(m?.propagation_time)}`,
            networkFormat(m?.delivery_latency), packet.path.length ? packet.path.join(" → ") : "No committed hops"];
    }), 10, filter ? "No matching packets." : "No packets submitted yet.");
    const hops = packets.flatMap(([id, packet]) => packet.hops.map(hop =>
        [id, hop.link_id, `${hop.source} → ${hop.destination}`,
            `${networkFormat(hop.transmission_start)} / ${networkFormat(hop.transmission_end)}`,
            `${networkFormat(hop.propagation_start)} / ${networkFormat(hop.propagation_end)}`, hop.outcome]));
    networkTable("network-hops-body", hops, 6, "No matching hop history.");
}

function renderNetworkTimeline(result) {
    const container = netElement("network-timeline"); container.replaceChildren();
    const intervals = (result?.network_timeline || []).map(interval => ({...interval,
        label: `${interval.packet_id} ${interval.link_id} ${interval.kind}${interval.active ? " (active)" : ""}`,
        kind: interval.kind.toLowerCase()}));
    const waiting = new Map();
    const closeWait = (id, end) => {
        if (!waiting.has(id)) return;
        const begin = waiting.get(id); waiting.delete(id);
        intervals.push({start: begin.timestamp, end, kind: "wait", label: `${id} ${begin.link_id} Queue waiting`});
    };
    for (const event of result?.network_events || []) {
        if (event.event_type === "PACKET_QUEUE") waiting.set(event.packet_id, event);
        else if (["PACKET_SEND", "PACKET_REROUTE", "PACKET_DELIVERED", "PACKET_DROPPED", "PACKET_FAILED", "PACKET_CANCELLED"].includes(event.event_type))
            closeWait(event.packet_id, event.timestamp);
        if (["PACKET_DROPPED", "PACKET_FAILED", "PACKET_CANCELLED", "LINK_DOWN", "NODE_DOWN"].includes(event.event_type))
            intervals.push({start: event.timestamp, end: event.timestamp, kind: "failure",
                label: `${event.packet_id ?? event.entity_id} ${event.event_type} ${event.outcome ?? ""}`});
    }
    for (const id of waiting.keys()) closeWait(id, result.current_time);
    intervals.sort((a, b) => a.start - b.start);
    const scale = result?.current_time > 0 ? result.current_time : 1;
    for (const interval of intervals) {
        if (!Number.isFinite(interval.start) || !Number.isFinite(interval.end)) continue;
        const row = document.createElement("div"); row.className = "network-timeline-row"; row.setAttribute("role", "listitem");
        const label = document.createElement("div");
        label.textContent = `${interval.label} | ${networkFormat(interval.start)}–${networkFormat(interval.end)}`;
        const track = document.createElement("div"); track.className = "network-track";
        const bar = document.createElement("div");
        bar.className = "network-bar network-" + interval.kind;
        bar.style.left = Math.min(100, Math.max(0, interval.start / scale * 100)) + "%";
        bar.style.width = Math.min(100, Math.max(0, (interval.end - interval.start) / scale * 100)) + "%";
        if (interval.start === interval.end) bar.classList.add("network-marker");
        bar.title = label.textContent;
        track.append(bar); row.append(label, track); container.append(row);
    }
    if (!intervals.length) container.textContent = "No network operations recorded.";
}
netElement("network-history-filter").oninput = renderNetworkHistory;
