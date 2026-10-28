# Priority Aging Comparison Test
# Based on the hand-worked expected calculations.
# All times are simulated milliseconds.
# One CPU, no I/O, and no context-switch overhead.

import pytest

from src.core.process import Process
from src.core.simulation import SimulationEngine


def test_priority_aging_changes_order():
    # Create processes
    p1 = Process("P1", 0, 12, priority=1)
    p2 = Process("P2", 0, 2, priority=3)
    p3 = Process("P3", 10, 1, priority=1)

    # Create simulation
    # Priority is non-preemptive; smaller numbers mean higher priority.
    # Ready waiting improves priority by 1 every 4 ms, down to 1.
    engine = SimulationEngine(
        [p1, p2, p3],
        algorithm="priority",
        aging_interval=4,
        context_switch=0
    )

    # Run simulation when pytest executes this test
    results = engine.run()

    # Check that every process is included and finishes
    assert len(results["processes"]) == 3
    assert p1.state.value == "TERMINATED"
    assert p2.state.value == "TERMINATED"
    assert p3.state.value == "TERMINATED"

    # Check first start times
    assert p1.start_time == 0, "P1 first start time is incorrect"
    assert p2.start_time == 12, "P2 first start time is incorrect"
    assert p3.start_time == 14, "P3 first start time is incorrect"

    # Check completion times
    assert p1.completion_time == 12, "P1 completion time is incorrect"
    assert p2.completion_time == 14, "P2 completion time is incorrect"
    assert p3.completion_time == 15, "P3 completion time is incorrect"

    # Check waiting times
    assert p1.waiting_time == 0, "P1 waiting time is incorrect"
    assert p2.waiting_time == 12, "P2 waiting time is incorrect"
    assert p3.waiting_time == 4, "P3 waiting time is incorrect"

    # Check turnaround times
    assert p1.turnaround_time == 12, "P1 turnaround time is incorrect"
    assert p2.turnaround_time == 14, "P2 turnaround time is incorrect"
    assert p3.turnaround_time == 5, "P3 turnaround time is incorrect"

    # Check response times
    assert p1.response_time == 0, "P1 response time is incorrect"
    assert p2.response_time == 12, "P2 response time is incorrect"
    assert p3.response_time == 4, "P3 response time is incorrect"

    # Check averages; approx allows tiny decimal representation differences
    averageCompletion = (
        p1.completion_time
        + p2.completion_time
        + p3.completion_time
    ) / 3

    assert averageCompletion == pytest.approx(41 / 3)
    assert results["average_waiting_time"] == pytest.approx(16 / 3)
    assert results["average_turnaround_time"] == pytest.approx(31 / 3)
    assert results["average_response_time"] == pytest.approx(16 / 3)

    # Check total time and useful CPU utilization
    assert results["current_time"] == 15
    assert results["cpu_utilization"] == pytest.approx(100)

    # Check expected Gantt intervals
    expectedTimeline = [
        ("P1", 0, 12),
        ("P2", 12, 14),
        ("P3", 14, 15),
    ]

    check_timeline(results["timeline"], expectedTimeline)

    # At time 12, P2 has waited 12 ms: priority max(1, 3 - 3) = 1.
    # P3 has waited 2 ms: priority 1 - 0 = 1.
    # Their effective priorities tie at 1; P2 wins by its earlier arrival.

    # Create fresh processes for the comparison run
    p1 = Process("P1", 0, 12, priority=1)
    p2 = Process("P2", 0, 2, priority=3)
    p3 = Process("P3", 10, 1, priority=1)

    # No process waits 100 ms, so no aging improvement occurs in this run.
    # This is a long aging interval, not an aging-disabled configuration.
    comparisonEngine = SimulationEngine(
        [p1, p2, p3],
        algorithm="priority",
        aging_interval=100,
        context_switch=0
    )

    comparisonResults = comparisonEngine.run()

    # Without an aging improvement, P3's original priority beats P2's.
    comparisonTimeline = [
        ("P1", 0, 12),
        ("P3", 12, 13),
        ("P2", 13, 15),
    ]

    check_timeline(comparisonResults["timeline"], comparisonTimeline)

    # Check comparison-run metrics
    assert p1.start_time == 0
    assert p2.start_time == 13
    assert p3.start_time == 12

    assert p1.completion_time == 12
    assert p2.completion_time == 15
    assert p3.completion_time == 13

    assert p1.waiting_time == 0
    assert p2.waiting_time == 13
    assert p3.waiting_time == 2

    assert p1.turnaround_time == 12
    assert p2.turnaround_time == 15
    assert p3.turnaround_time == 3

    assert p1.response_time == 0
    assert p2.response_time == 13
    assert p3.response_time == 2

    assert p1.state.value == "TERMINATED"
    assert p2.state.value == "TERMINATED"
    assert p3.state.value == "TERMINATED"

    assert len(comparisonResults["processes"]) == 3
    assert comparisonResults["current_time"] == 15
    assert comparisonResults["cpu_utilization"] == pytest.approx(100)

    averageCompletion = (
        p1.completion_time + p2.completion_time + p3.completion_time
    ) / 3

    assert averageCompletion == pytest.approx(40 / 3)
    assert comparisonResults["average_waiting_time"] == pytest.approx(15 / 3)
    assert comparisonResults["average_turnaround_time"] == pytest.approx(30 / 3)
    assert comparisonResults["average_response_time"] == pytest.approx(15 / 3)


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

