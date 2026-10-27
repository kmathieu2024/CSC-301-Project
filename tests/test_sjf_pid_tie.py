# Scheduling tie and boundary test
# All times are simulated milliseconds; one CPU, no I/O, cs = 0.
# Expected results are reference calculations, not recorded execution results.

import pytest

from src.core.process import Process
from src.core.simulation import SimulationEngine


def test_sjf_equal_bursts_use_pid():
    # Create processes; enter P2 first to distinguish PID from input order
    p2 = Process("P2", 0, 2, priority=0)
    p1 = Process("P1", 0, 2, priority=0)

    # Create simulation
    engine = SimulationEngine(
        [p2, p1],
        algorithm="sjf",
        context_switch=0
    )

    # Run simulation when pytest executes this test
    results = engine.run()

    # Check final states
    assert len(results["processes"]) == 2
    assert p1.state.value == "TERMINATED"
    assert p2.state.value == "TERMINATED"

    # Check start time
    assert p1.start_time == 0
    assert p2.start_time == 2

    # Check completion time
    assert p1.completion_time == 2
    assert p2.completion_time == 4

    # Check turnaround time
    assert p1.turnaround_time == 2
    assert p2.turnaround_time == 4

    # Check waiting time
    assert p1.waiting_time == 0
    assert p2.waiting_time == 2

    # Check response time
    assert p1.response_time == 0
    assert p2.response_time == 2

    # Check averages
    averageCompletion = (p1.completion_time + p2.completion_time) / 2
    assert averageCompletion == pytest.approx(3)
    assert results["average_turnaround_time"] == pytest.approx(3)
    assert results["average_waiting_time"] == pytest.approx(1)
    assert results["average_response_time"] == pytest.approx(1)

    assert results["current_time"] == 4
    assert results["cpu_utilization"] == pytest.approx(100)

    # Check dispatch order as well as the Gantt intervals
    dispatchOrder = []
    for event in results["events"]:
        if event["event_type"] == "dispatch":
            dispatchOrder.append(event["entity_id"])

    assert dispatchOrder == ["P1", "P2"]

    expectedTimeline = [
        ("P1", 0, 2),
        ("P2", 2, 4),
    ]
    check_timeline(results["timeline"], expectedTimeline)



def check_timeline(timeline, expectedTimeline):
    # Join adjacent records for the same continuous CPU execution
    actualTimeline = []

    for segment in timeline:
        pid = segment["pid"]
        start = segment["start"]
        end = segment["end"]

        assert end > start

        if len(actualTimeline) > 0:
            previous = actualTimeline[-1]

            if previous[0] == pid and previous[2] == start:
                actualTimeline[-1] = (pid, previous[1], end)
            else:
                actualTimeline.append((pid, start, end))
        else:
            actualTimeline.append((pid, start, end))

    assert actualTimeline == expectedTimeline

