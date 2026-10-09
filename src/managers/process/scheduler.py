# scheduling algorithsm: fcfs, sjf, srtf, rr, priority 

from dataclasses import dataclass
from src.core.process import Process

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

        # Shortest burst first; arrival and PID break ties.
        process = min(
            available,
            key=lambda p: (p.burst_time, p.arrival_time, p.pid),
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
