// Comparison has its own request state and never changes simulation inputs/results.
const comparisonForm = document.getElementById("memory-comparison-form");
const comparisonResults = document.getElementById("comparison-results");
const comparisonStatus = document.getElementById("comparison-status");
const comparisonError = document.getElementById("comparison-error");
let comparingMemory = false;

function clearMemoryComparison() {
    comparisonResults.hidden = true;
    document.getElementById("comparison-body").replaceChildren();
    comparisonStatus.textContent = "";
    comparisonError.textContent = "";
}

comparisonForm.addEventListener("input", event => {
    clearMemoryComparison();
    event.target.removeAttribute("aria-invalid");
    document.getElementById(event.target.id + "-error").textContent = "";
});

comparisonForm.addEventListener("submit", async event => {
    event.preventDefault();
    if (comparingMemory) return;
    clearMemoryComparison();
    let valid = true;
    const field = (name, message) => {
        const input = document.getElementById("comparison-" + name);
        const value = input.value.trim();
        const error = message(value);
        input.setAttribute("aria-invalid", String(Boolean(error)));
        document.getElementById(input.id + "-error").textContent = error;
        if (error) valid = false;
        return value;
    };
    const trace = field("addresses", value => {
        if (!/^\d+(?:(?:\s*,\s*|\s+)\d+)*$/.test(value) ||
            value.split(/[\s,]+/).some(address => !Number.isSafeInteger(Number(address))))
            return "Enter nonnegative whole byte addresses separated by commas or whitespace.";
        return "";
    });
    const positiveInteger = value => /^\d+$/.test(value) && Number.isSafeInteger(Number(value)) && Number(value) > 0
        ? "" : "Enter a positive whole number.";
    const frames = field("frame-count", positiveInteger);
    const pageSize = field("page-size", positiveInteger);
    if (!valid) return;

    comparingMemory = true;
    const controls = Array.from(comparisonForm.querySelectorAll("input, button"));
    controls.forEach(control => control.disabled = true);
    comparisonStatus.textContent = "Comparing FIFO and LRU…";
    try {
        const result = await sendRequest("memory/compare", {
            addresses: trace.split(/[\s,]+/).map(Number),
            frame_count: Number(frames), page_size: Number(pageSize)
        });
        const body = document.getElementById("comparison-body");
        for (const [key, label] of [["references", "References"], ["hits", "Hits"],
            ["faults", "Faults"], ["evictions", "Evictions"], ["fault_rate", "Fault rate"]]) {
            const row = body.insertRow();
            row.insertCell().textContent = label;
            for (const policy of ["fifo", "lru"])
                row.insertCell().textContent = key === "fault_rate"
                    ? (result[policy][key] * 100).toFixed(1) + "%" : result[policy][key];
        }
        comparisonResults.hidden = false;
        comparisonStatus.textContent = "Comparison complete. Fault rate is shown as a percentage.";
    } catch (error) {
        clearMemoryComparison();
        comparisonError.textContent = "Comparison failed: " + error.message;
    } finally {
        comparingMemory = false;
        controls.forEach(control => control.disabled = false);
    }
});
