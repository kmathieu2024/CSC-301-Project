# Four Process FCFS Baseline Test
# Based on the hand-worked expected calculations.
# All times are simulated milliseconds.
# One CPU, no I/O, and no context-switch overhead.

import pytest

from src.core.process import Process
from src.core.simulation import SimulationEngine


def test_fcfs_baseline():
    # Create processes
    p1 = Process("P1", 0, 8, priority=3)
    p2 = Process("P2", 1, 4, priority=1)
    p3 = Process("P3", 2, 9, priority=4)
    p4 = Process("P4", 3, 5, priority=2)

    # Create simulation
    engine = SimulationEngine(
        [p1, p2, p3, p4],
        algorithm="fcfs",
        context_switch=0
    )

    # Run simulation when pytest executes this test
    results = engine.run()

    # Check that every process is included and finishes
    assert len(results["processes"]) == 4
    assert p1.state.value == "TERMINATED"
    assert p2.state.value == "TERMINATED"
    assert p3.state.value == "TERMINATED"
    assert p4.state.value == "TERMINATED"

    # Check first start times
    assert p1.start_time == 0, "P1 first start time is incorrect"
    assert p2.start_time == 8, "P2 first start time is incorrect"
    assert p3.start_time == 12, "P3 first start time is incorrect"
    assert p4.start_time == 21, "P4 first start time is incorrect"

    # Check completion times
    assert p1.completion_time == 8, "P1 completion time is incorrect"
    assert p2.completion_time == 12, "P2 completion time is incorrect"
    assert p3.completion_time == 21, "P3 completion time is incorrect"
    assert p4.completion_time == 26, "P4 completion time is incorrect"

    # Check waiting times
    assert p1.waiting_time == 0, "P1 waiting time is incorrect"
    assert p2.waiting_time == 7, "P2 waiting time is incorrect"
    assert p3.waiting_time == 10, "P3 waiting time is incorrect"
    assert p4.waiting_time == 18, "P4 waiting time is incorrect"

    # Check turnaround times
    assert p1.turnaround_time == 8, "P1 turnaround time is incorrect"
    assert p2.turnaround_time == 11, "P2 turnaround time is incorrect"
    assert p3.turnaround_time == 19, "P3 turnaround time is incorrect"
    assert p4.turnaround_time == 23, "P4 turnaround time is incorrect"

    # Check response times
    assert p1.response_time == 0, "P1 response time is incorrect"
    assert p2.response_time == 7, "P2 response time is incorrect"
    assert p3.response_time == 10, "P3 response time is incorrect"
    assert p4.response_time == 18, "P4 response time is incorrect"

    # Check averages; approx allows tiny decimal representation differences
    averageCompletion = (
        p1.completion_time
        + p2.completion_time
        + p3.completion_time
        + p4.completion_time
    ) / 4

    assert averageCompletion == pytest.approx(16.75)
    assert results["average_waiting_time"] == pytest.approx(8.75)
    assert results["average_turnaround_time"] == pytest.approx(15.25)
    assert results["average_response_time"] == pytest.approx(8.75)

    # Check total time and useful CPU utilization
    assert results["current_time"] == 26
    assert results["cpu_utilization"] == pytest.approx(100)

    # Check expected Gantt intervals
    expectedTimeline = [
        ("P1", 0, 8),
        ("P2", 8, 12),
        ("P3", 12, 21),
        ("P4", 21, 26),
    ]

    check_timeline(results["timeline"], expectedTimeline)


def check_timeline(timeline, expectedTimeline):
    # Arrivals can split one continuous run into several records.
    # Join neighboring records only when the same process keeps running.
    actualTimeline = []

    for segment in timeline:
        pid = segment["pid"]
        start = segment["start"]
        end = segment["end"]

        assert end > start, "A timeline segment must have positive duration"

        if len(actualTimeline) > 0:
            previous = actualTimeline[-1]

            if previous[0] == pid and previous[2] == start:
                actualTimeline[-1] = (pid, previous[1], end)
            else:
                actualTimeline.append((pid, start, end))
        else:
            actualTimeline.append((pid, start, end))

    assert actualTimeline == expectedTimeline

