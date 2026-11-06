
from src.core.process import Process, ProcessState


class ParallelSimulator:
    """Simulate FCFS execution on 1, 2, or 4 CPU cores."""

    def __init__(self, processes, core_count=1):
        if core_count not in (1, 2, 4) or isinstance(core_count, bool):
            raise ValueError("Core count must be 1, 2, or 4")

        if not processes:
            raise ValueError("At least one process is required")

        self.processes = processes
        self.core_count = core_count

    def run(self):
        # Reset process execution states.
        for process in self.processes:
            process.reset()

        # Track when each core becomes available.
        core_available = [0] * self.core_count
        core_busy = [0] * self.core_count
        timeline = []

        # FCFS order: arrival time, then process ID.
        ordered = sorted(
            self.processes,
            key=lambda p: (p.arrival_time, p.pid)
        )

        for process in ordered:
            # Select the earliest available core.
            core_id = min(
                range(self.core_count),
                key=lambda core: (core_available[core], core)
            )

            start = max(
                process.arrival_time,
                core_available[core_id]
            )

            end = start + process.burst_time

            process.state = ProcessState.TERMINATED
            process.start_time = start
            process.completion_time = end
            process.remaining_time = 0
            process.response_time = start - process.arrival_time
            process.waiting_time = start - process.arrival_time
            process.turnaround_time = end - process.arrival_time

            timeline.append({
                "pid": process.pid,
                "core_id": core_id,
                "start": start,
                "end": end
            })

            core_available[core_id] = end
            core_busy[core_id] += process.burst_time

        makespan = max(core_available)

        utilization = [
            100 * busy / makespan if makespan else 0
            for busy in core_busy
        ]

        throughput = (
            len(self.processes) / makespan
            if makespan else 0
        )

        return {
            "core_count": self.core_count,
            "makespan": makespan,
            "throughput": throughput,
            "core_utilization": utilization,
            "timeline": sorted(
                timeline,
                key=lambda event: (
                    event["start"],
                    event["core_id"]
                )
            )
        }


def compare_core_counts(processes):
    """Compare performance using 1, 2, and 4 CPU cores."""

    results = {}

    for count in (1, 2, 4):
        simulation = ParallelSimulator(
            processes,
            core_count=count
        )
        results[count] = simulation.run()

    baseline = results[1]["makespan"]

    for count, result in results.items():
        makespan = result["makespan"]

        speedup = baseline / makespan if makespan else 0
        efficiency = speedup / count * 100

        result["speedup"] = speedup
        result["efficiency"] = efficiency

    return results
