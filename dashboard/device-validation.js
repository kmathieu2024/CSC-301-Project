// JSON validation mirrors the Python boundary; domain execution stays in Python.
const configuredDevices = [];
const deviceOperations = {DISK: ["READ", "WRITE"], PRINTER: ["PRINT"], KEYBOARD: ["READ"]};

function deviceObject(value, fields, name) {
    if (!isObject(value)) throw new Error(name + " must be an object.");
    for (const key of Object.keys(value))
        if (!fields.includes(key)) throw new Error(name + ": unsupported field " + key);
}

function deviceNumber(value, name, integer = false) {
    if (typeof value !== "number" || !Number.isFinite(value) || value < 0 ||
        (integer && !Number.isSafeInteger(value)))
        throw new Error(name + " must be a nonnegative " + (integer ? "safe integer." : "finite number."));
}

function deviceEnum(value, allowed, name) {
    if (typeof value !== "string" || !allowed.includes(value))
        throw new Error(name + " must be one of " + allowed.join(", ") + ".");
}

function validateDevices(value = []) {
    if (!Array.isArray(value)) throw new Error("devices must be an array.");
    const ids = new Set();
    return value.map(input => {
        deviceObject(input, ["device_id", "device_type", "policy", "default_service_time", "disk"], "Device");
        if (typeof input.device_id !== "string" || !input.device_id || input.device_id.trim() !== input.device_id)
            throw new Error("Device ID must be nonempty text without outer whitespace.");
        if (ids.has(input.device_id)) throw new Error("Duplicate device ID: " + input.device_id);
        ids.add(input.device_id);
        deviceEnum(input.device_type, Object.keys(deviceOperations), "Device type");
        const config = {policy: "FCFS", default_service_time: 1, ...input};
        deviceEnum(config.policy, ["FCFS", "SSTF", "SCAN"], "Device policy");
        deviceNumber(config.default_service_time, "Service time");
        if (config.device_type !== "DISK") {
            if (config.policy !== "FCFS" || Object.hasOwn(config, "disk"))
                throw new Error("Printers/keyboards require FCFS and prohibit disk geometry.");
        } else {
            deviceObject(config.disk, ["min_cylinder", "max_cylinder", "initial_head", "initial_direction", "seek_time_per_cylinder"], "disk");
            const disk = {initial_direction: "UP", seek_time_per_cylinder: 1, ...config.disk};
            for (const key of ["min_cylinder", "max_cylinder", "initial_head"]) deviceNumber(disk[key], key, true);
            if (disk.min_cylinder >= disk.max_cylinder || disk.initial_head < disk.min_cylinder || disk.initial_head > disk.max_cylinder)
                throw new Error("Disk requires min < max and an initial head within inclusive bounds.");
            deviceEnum(disk.initial_direction, ["UP", "DOWN"], "Direction");
            deviceNumber(disk.seek_time_per_cylinder, "Seek time");
            deviceNumber((disk.max_cylinder - disk.min_cylinder) * disk.seek_time_per_cylinder, "Maximum seek duration");
            config.disk = disk;
        }
        return config;
    });
}

function validateDeviceRequests(requests = {}, burst, devices = configuredDevices) {
    if (!isObject(requests)) throw new Error("device_requests must be an offset-to-spec object.");
    for (const [offset, request] of Object.entries(requests)) {
        if (!/^(0|[1-9][0-9]*)$/.test(offset) || !Number.isSafeInteger(Number(offset)) || Number(offset) >= burst)
            throw new Error("Device offset must be a canonical nonnegative integer less than burst time.");
        deviceObject(request, ["device_id", "operation", "cylinder", "service_time"], "Device request");
        const device = devices.find(d => d.device_id === request.device_id);
        if (!device) throw new Error("Unknown device ID: " + request.device_id);
        deviceEnum(request.operation, deviceOperations[device.device_type], "Device operation for " + device.device_type);
        if (device.disk) {
            deviceNumber(request.cylinder, "Request cylinder", true);
            if (request.cylinder < device.disk.min_cylinder || request.cylinder > device.disk.max_cylinder)
                throw new Error("Request cylinder must be within disk bounds.");
        } else if (request.cylinder != null) throw new Error("Printers/keyboards prohibit a cylinder.");
        if (request.service_time != null) deviceNumber(request.service_time, "Request service time");
    }
    return requests;
}
