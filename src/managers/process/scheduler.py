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

        #move one time unit at a time 
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
