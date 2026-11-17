// Shared validation for imported and interactive simulation inputs.
const memoryDefaults = {enabled: true, frame_count: 3, page_size: 4,
    algorithm: "fifo", fault_latency: 0};

function isObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
}

function validateMemory(value = {}) {
    if (!isObject(value)) throw new Error("Memory settings must be an object.");
    const memory = {...memoryDefaults, ...value};
    if (typeof memory.enabled !== "boolean") throw new Error("Memory enabled must be boolean.");
    if (!["fifo", "lru"].includes(memory.algorithm)) throw new Error("Memory policy must be fifo or lru.");
    for (const key of ["frame_count", "page_size"]) {
        if (!Number.isSafeInteger(memory[key]) || memory[key] < 1)
            throw new Error(key + " must be a positive whole number.");
    }
    if (typeof memory.fault_latency !== "number" || !Number.isFinite(memory.fault_latency) || memory.fault_latency < 0)
        throw new Error("Fault latency must be a finite nonnegative number.");
    return memory;
}

function validateReferences(references, burst) {
    if (!isObject(references)) throw new Error("memory_references must be an offset-to-address object.");
    for (const [offset, address] of Object.entries(references)) {
        // Canonical decimal keys prevent aliases such as 01 and 1 from collapsing.
        if (!/^(0|[1-9][0-9]*)$/.test(offset) || !Number.isSafeInteger(Number(offset)) || Number(offset) >= burst)
            throw new Error("Reference offset must be a nonnegative integer less than burst time (canonical decimal key).");
        if (!Number.isSafeInteger(address) || address < 0)
            throw new Error("Virtual address must be a nonnegative integer.");
    }
    return references;
}

function validateMemoryAssociations(data) {
    if (Object.hasOwn(data, "memory_references"))
        throw new Error("Place memory_references inside an existing process, not at the top level.");
}

// JSON.parse normally discards duplicate keys. Inspect each object's keys first.
function parseInputJson(text) {
    const tokens = text.match(/"(?:\\.|[^"\\])*"|[{}\[\]:,]|[^\s{}\[\]:,]+/g) || [];
    let position = 0;
    function value() {
        const token = tokens[position++];
        if (token === "{") {
            const keys = new Set();
            while (tokens[position] !== "}") {
                const key = JSON.parse(tokens[position++]);
                if (keys.has(key)) throw new Error("Duplicate JSON key: " + key);
                keys.add(key);
                if (tokens[position++] !== ":") throw new SyntaxError();
                value();
                if (tokens[position] !== ",") break;
                position++;
            }
            if (tokens[position++] !== "}") throw new SyntaxError();
        } else if (token === "[") {
            while (tokens[position] !== "]") {
                value();
                if (tokens[position] !== ",") break;
                position++;
            }
            if (tokens[position++] !== "]") throw new SyntaxError();
        } else {
            JSON.parse(token);
        }
    }
    // Validate syntax before scanning so malformed files retain the existing message.
    const parsed = JSON.parse(text);
    value();
    return parsed;
}


function validateFileRequests(requests, burst) {
    if (!isObject(requests)) throw new Error("file_requests must be an offset-to-command object.");
    for (const [offset, command] of Object.entries(requests)) {
        if (!/^(0|[1-9][0-9]*)$/.test(offset) || !Number.isSafeInteger(Number(offset)) || Number(offset) >= burst)
            throw new Error("File-request offset must be a nonnegative integer less than burst time (canonical decimal key).");
        if (typeof command !== "string" || !command.trim())
            throw new Error("File command must be nonempty text.");
    }
    // Keep commands verbatim; the backend alone parses command syntax.
    return requests;
}

function validateBlockingRequests(requests, burst) {
    if (!isObject(requests)) throw new Error("blocking_requests must be an offset-to-duration object.");
    for (const [offset, duration] of Object.entries(requests)) {
        if (!/^(0|[1-9][0-9]*)$/.test(offset) || !Number.isSafeInteger(Number(offset)) || Number(offset) >= burst)
            throw new Error("Blocking offset must be a nonnegative integer less than burst time.");
        if (typeof duration !== "number" || !Number.isFinite(duration) || duration <= 0)
            throw new Error("Blocking duration must be a positive finite number.");
    }
    return requests;
}
