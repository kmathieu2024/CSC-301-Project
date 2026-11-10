let activeManagerTest = null;
let managerTestFinished = false;

const fileTestCommands = ["MKDIR /demo", "CREATE /demo/notes.txt",
    'WRITE /demo/notes.txt "Hello OS"', "READ /demo/notes.txt", "READ /missing.txt"];

function managerTestWorkload(manager) {
    const process = {pid: "P1", arrival_time: 0, burst_time: 5, priority: 1};
    if (manager === "memory") process.memory_references = {0: 0, 1: 4, 2: 0, 3: 8, 4: 4};
    else process.file_requests = Object.fromEntries(fileTestCommands.map((command, offset) => [offset, command]));
    return {algorithm: "FCFS", configuration: {context_switch_cost: 0},
        memory: {...memoryDefaults, enabled: manager === "memory", frame_count: 2},
        processes: [process]};
}

function updateManagerTestButtons() {
    for (const manager of ["memory", "file"])
        document.getElementById("load-" + manager + "-test").disabled = busy || running;
}

function clearManagerTestVerdict() {
    managerTestFinished = false;
    for (const manager of ["memory", "file"]) {
        document.getElementById(manager + "-test-body").replaceChildren();
        document.getElementById(manager + "-test-results").hidden = true;
        document.getElementById(manager + "-test-status").textContent = activeManagerTest === manager
            ? "Scenario loaded. Run or Step to complete the checks." : "";
    }
}

function clearManagerTestMode() {
    activeManagerTest = null;
    clearManagerTestVerdict();
}

function loadManagerTest(manager) {
    if (busy || running) return;
    try {
        // The same validation and atomic replacement path as JSON workload import.
        loadWorkload(managerTestWorkload(manager));
        activeManagerTest = manager;
        processError.textContent = "";
        uploadStatus.textContent = "";
        clearManagerTestVerdict();
    } catch (error) {
        document.getElementById(manager + "-test-status").textContent = "Could not load scenario: " + error.message;
    }
}

function renderManagerTestResult(result, finished) {
    if (!activeManagerTest) return;
    if (!finished) {
        clearManagerTestVerdict();
        return;
    }
    managerTestFinished = true;
    const checks = [];
    const check = (label, expected, actual) => checks.push({label, expected, actual, passed: actual === expected});
    if (activeManagerTest === "memory") {
        for (const [metric, expected] of [["references", 5], ["hits", 2], ["faults", 3], ["evictions", 1]])
            check(metric, expected, result.memory_metrics?.[metric] ?? "Unavailable");
    } else {
        const events = (result.events || []).filter(event => event.manager === "file");
        check("Operation count", 5, events.length);
        fileTestCommands.forEach((command, index) => {
            const event = events[index];
            const expected = "P1 @ " + index + " | " + command + " | " + (index === 4 ? "error" : "success");
            const actual = event
                ? event.entity_id + " @ " + event.timestamp + " | " + event.action + " | " + event.outcome
                : "Unavailable";
            check("Request " + index, expected, actual);
        });
        check("Successful READ output", "Hello OS", events[3]?.message ?? "Unavailable");
        check("Missing-file error reported", true, events[4]?.outcome === "error" &&
            typeof events[4]?.message === "string" && /not found/i.test(events[4].message));
        const demo = (result.file_tree?.children || []).find(node => node.name === "demo" && node.type === "directory");
        check("/demo/notes.txt exists", true, Boolean(demo?.children?.some(node => node.name === "notes.txt" && node.type === "file")));
    }
    const body = document.getElementById(activeManagerTest + "-test-body");
    body.replaceChildren();
    for (const item of checks) {
        const row = body.insertRow();
        for (const value of [item.label, item.expected, item.actual, item.passed ? "Pass" : "Fail"])
            row.insertCell().textContent = String(value);
        row.lastChild.className = item.passed ? "test-pass" : "test-fail";
    }
    document.getElementById(activeManagerTest + "-test-results").hidden = false;
    document.getElementById(activeManagerTest + "-test-status").textContent = checks.every(item => item.passed)
        ? "Pass: all scenario checks passed." : "Fail: one or more scenario checks failed.";
}

for (const manager of ["memory", "file"])
    document.getElementById("load-" + manager + "-test").onclick = () => loadManagerTest(manager);
