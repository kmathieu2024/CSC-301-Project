
const parallelButton = document.getElementById("parallel-compare-button");
const parallelStatus = document.getElementById("parallel-status");
const parallelResultsBody = document.getElementById("parallel-results-body");

parallelButton.addEventListener("click", async () => {
    parallelButton.disabled = true;
    parallelStatus.textContent = "Running parallel comparison...";
    parallelResultsBody.replaceChildren();

    try {
        const workload = collectSimulationInput();

        const response = await fetch("/api/parallel/compare", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(workload)
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || "Parallel comparison failed.");
        }

        for (const cores of [1, 2, 4]) {
            const result = data.comparisons[String(cores)];
            const row = document.createElement("tr");

            const values = [
                cores,
                result.makespan,
                result.speedup.toFixed(2) + "x",
                result.efficiency.toFixed(2) + "%",
                result.throughput.toFixed(3)
            ];

            for (const value of values) {
                const cell = document.createElement("td");
                cell.textContent = value;
                row.appendChild(cell);
            }

            parallelResultsBody.appendChild(row);
        }

        parallelStatus.textContent = "Parallel comparison completed successfully.";

    } catch (error) {
        parallelStatus.textContent = "Error: " + error.message;
    } finally {
        parallelButton.disabled = false;
    }
});
