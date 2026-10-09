# scheduling algorithsm: fcfs, sjf, srtf, rr, priority 

from dataclasses import dataclass
from src.core.process import Process
from collections import deque

@dataclass
class ScheduleResult:
    # results of a completed simulation

    timeline: list[tuple[str, int, int]]
    metrics: dict[str, dict[str, int]]

def calculate_metrics(
    process: Process,
    start_time: int,
    completion_time: int,
) -> dict[str, int]:
    # Calculate scheduling metrics for one process

    turnaround_time = completion_time - process.arrival_time
    waiting_time = turnaround_time - process.burst_time
    response_time = start_time - process.arrival_time

    return {
        "start_time": start_time,
        "completion_time": completion_time,
        "turnaround_time": turnaround_time,
        "waiting_time": waiting_time,
        "response_time": response_time,
    }


def validate_processes(processes: list[Process]) -> None:
    # make sure processes are valid

    pids = [process.pid for process in processes]

    if len(pids) != len(set(pids)):
        raise ValueError("Process IDs must be unique.")

    for process in processes:
        if process.arrival_time < 0:
            raise ValueError("Arrival time cannot be negative.")

        if process.burst_time <= 0:
            raise ValueError("Burst time must be positive.")

def fcfs(processes: list[Process]) -> ScheduleResult:
    # schedule with fcfs algorithm

    validate_processes(processes)

    current_time = 0
    timeline = []
    metrics = {}

    # put processes in running order by earliest first, 
    # if they arrive at the same time sort by input order.
    ordered = sorted(
        processes,
        key=lambda p: (p.arrival_time),
    )

    for process in ordered:
        # if idle, skip to the next event 
        if current_time < process.arrival_time:
            timeline.append(
                ("IDLE", current_time, process.arrival_time)
            )
            current_time = process.arrival_time

        # run the process
        start_time = current_time
        completion_time = start_time + process.burst_time

        timeline.append(
            (process.pid, start_time, completion_time)
        )

        metrics[process.pid] = calculate_metrics(
            process, start_time, completion_time
        )

        current_time = completion_time

    return ScheduleResult(timeline, metrics)


def sjf(processes: list[Process]) -> ScheduleResult:
    # scheduling using non-preemptive Shortest Job First

    validate_processes(processes)

    current_time = 0
    timeline = []
    metrics = {}

    remaining = list(processes)

    while remaining:
        # Find processes that have already arrived
        available = [
            process
            for process in remaining
            if process.arrival_time <= current_time
        ]

        if not available:
            # No process is ready, skip to the next arrival.
            next_arrival = min(
                process.arrival_time for process in remaining
            )

            timeline.append(("IDLE", current_time, next_arrival))
            current_time = next_arrival
            continue

        # Shortest burst first, arrival time breaks ties.
        process = min(
            available,
            key=lambda p: (p.burst_time, p.arrival_time),
        )

        start_time = current_time
        completion_time = start_time + process.burst_time

        timeline.append(
            (process.pid, start_time, completion_time)
        )

        metrics[process.pid] = calculate_metrics(
            process, start_time, completion_time
        )

        current_time = completion_time
        remaining.remove(process)

    return ScheduleResult(timeline, metrics)

def srtf(processes: list[Process]) -> ScheduleResult:
    validate_processes(processes)

    current_time = 0
    remaining = {p.pid: p.burst_time for p in processes}
    start_times = {}
    completion_times = {}
    timeline = []

    # while theres still more processes to run 
    while len(completion_times) < len(processes):

        # a process is ready when it has already arrived to the queue and has remaining burst time
        available = [
            p for p in processes
            if p.arrival_time <= current_time
            and remaining[p.pid] > 0
        ]

        # jump to the next process if none are ready 
        if not available:
            future = [
                p.arrival_time for p in processes
                if remaining[p.pid] > 0
            ]
            next_arrival = min(future)

            timeline.append(("IDLE", current_time, next_arrival))
            current_time = next_arrival
            continue

        # processes run based on shortest remaining time. 
        # if there's a tie, by arrival time, and if there's another tie, in alphabetical order
        process = min(
            available,
            key=lambda p: (
                remaining[p.pid],
                p.arrival_time,
                p.pid
            )
        )

        # add to start times log if not already started 
        if process.pid not in start_times:
            start_times[process.pid] = current_time

        # if the process being ran was the one being ran before 
        if timeline and timeline[-1][0] == process.pid:
            pid, start, _ = timeline[-1]
            timeline[-1] = (pid, start, current_time + 1)
            # extend the time running on timeline
        else:
            # add to timeline if not already running 
            timeline.append(
                (process.pid, current_time, current_time + 1)
            )

        # move one time unit at a time 
        remaining[process.pid] -= 1
        current_time += 1

        # if the process finishes update completion time logs
        if remaining[process.pid] == 0:
            completion_times[process.pid] = current_time

    metrics = {
        p.pid: calculate_metrics(
            p,
            start_times[p.pid],
            completion_times[p.pid]
        )
        for p in processes
    }

    return ScheduleResult(timeline, metrics)

def rr(processes: list[Process], quantum: int) -> ScheduleResult:

    validate_processes(processes)

    if quantum <= 0:
        raise ValueError("Quantum must be positive.")

    # sort the processes by arrival time 
    ordered = sorted(
        processes,
        key=lambda p: p.arrival_time
    )

    remaining = {p.pid: p.burst_time for p in processes}
    start_times = {}
    completion_times = {}
    timeline = []

    ready_queue = deque()
    current_time = 0
    next_index = 0

    while len(completion_times) < len(processes):
        # add all ready processes to the ready queue
        while (next_index < len(ordered) and ordered[next_index].arrival_time <= current_time):
            ready_queue.append(ordered[next_index])
            next_index += 1

        # skip to the next event if no process is ready
        if not ready_queue:
            next_arrival = ordered[next_index].arrival_time

            timeline.append(("IDLE", current_time, next_arrival))
            current_time = next_arrival
            continue

        process = ready_queue.popleft()

        if process.pid not in start_times:
            start_times[process.pid] = current_time

        # run until the time quantum, or if the process is shorter than the quantum until it ends
        run_time = min(quantum, remaining[process.pid])

        timeline.append(
            (process.pid, current_time, current_time + run_time)
        )

        current_time += run_time
        remaining[process.pid] -= run_time

        # Add processes that arrived during this time slice
        while (
            next_index < len(ordered)
            and ordered[next_index].arrival_time <= current_time
        ):
            ready_queue.append(ordered[next_index])
            next_index += 1

        if remaining[process.pid] > 0:
            ready_queue.append(process)
        else:
            completion_times[process.pid] = current_time

    metrics = {
        p.pid: calculate_metrics(
            p,
            start_times[p.pid],
            completion_times[p.pid]
        )
        for p in processes
    }

    return ScheduleResult(timeline, metrics)

# ages the priority of the process every 4 time units
# nonpreemptive priority scheduling
def priority(processes: list[Process], aging_interval: int = 4) -> ScheduleResult:

    validate_processes(processes)

    if aging_interval <= 0:
        raise ValueError("Aging interval must be positive.")

    current_time = 0
    remaining = list(processes)
    timeline = []
    metrics = {}

    while remaining:

        # Find processes that have arrived
        available = [
            p for p in remaining
            if p.arrival_time <= current_time
        ]

        # If no process is available, skip to next arrival
        if not available:
            next_arrival = min(
                p.arrival_time for p in remaining
            )

            timeline.append(
                ("IDLE", current_time, next_arrival)
            )

            current_time = next_arrival
            continue

        # Calculate effective priority using aging
        def effective_priority(p):
            waiting_time = current_time - p.arrival_time

            aging_steps = waiting_time // aging_interval

            return max(0, p.priority - aging_steps)

        # Choose the process with the highest effective priority
        process = min(
            available,
            key=lambda p: (
                effective_priority(p),
                p.arrival_time,
                p.pid
            )
        )

        # Non-preemptive: run until completion
        start_time = current_time
        completion_time = start_time + process.burst_time

        timeline.append(
            (process.pid, start_time, completion_time)
        )

        metrics[process.pid] = calculate_metrics(
            process,
            start_time,
            completion_time
        )

        current_time = completion_time

        # Remove completed process
        remaining.remove(process)

    return ScheduleResult(timeline, metrics)

def calculate_aggregate_metrics(
    result: ScheduleResult
) -> dict[str, float]:

    metrics = result.metrics
    process_count = len(metrics)

    if process_count == 0:
        return {
            "avg_turnaround_time": 0.0,
            "avg_waiting_time": 0.0,
            "avg_response_time": 0.0,
            "cpu_utilization": 0.0,
            "throughput": 0.0,
        }

    avg_turnaround = sum(
        m["turnaround_time"] for m in metrics.values()
    ) / process_count

    avg_waiting = sum(
        m["waiting_time"] for m in metrics.values()
    ) / process_count

    avg_response = sum(
        m["response_time"] for m in metrics.values()
    ) / process_count

    total_time = max(
        m["completion_time"] for m in metrics.values()
    )

    busy_time = sum(
        end - start
        for pid, start, end in result.timeline
        if pid not in ("IDLE", "CS")
    )

    # For CPU utilization, we're measuring useful process execution time divided by 
    # total elapsed simulation time. Context-switch overhead isn't counted as useful execution.
    cpu_utilization = (
        busy_time / total_time * 100
        if total_time > 0 else 0.0
    )

    throughput = (
        process_count / total_time
        if total_time > 0 else 0.0
    )

    return {
        "avg_turnaround_time": avg_turnaround,
        "avg_waiting_time": avg_waiting,
        "avg_response_time": avg_response,
        "cpu_utilization": cpu_utilization,
        "throughput": throughput,
    }