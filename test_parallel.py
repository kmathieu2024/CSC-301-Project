
import pytest

from src.core.process import Process
from src.parallel.parallel import ParallelSimulator, compare_core_counts
from src.parallel.sync import race_condition_demo, deadlock_demo


def make_processes():
    return [
        Process("P1", 0, 4),
        Process("P2", 0, 4),
        Process("P3", 0, 4),
        Process("P4", 0, 4),
    ]


@pytest.mark.parametrize(
    "cores,expected_makespan",
    [(1, 16), (2, 8), (4, 4)]
)
def test_core_execution(cores, expected_makespan):
    result = ParallelSimulator(
        make_processes(), core_count=cores
    ).run()

    assert result["makespan"] == expected_makespan
    assert len(result["timeline"]) == 4
    assert len(result["core_utilization"]) == cores


def test_parallel_speedup_and_efficiency():
    results = compare_core_counts(make_processes())

    assert results[1]["speedup"] == 1
    assert results[2]["speedup"] == 2
    assert results[4]["speedup"] == 4

    for result in results.values():
        assert result["efficiency"] == 100


def test_parallel_throughput():
    results = compare_core_counts(make_processes())

    assert results[1]["throughput"] == 0.25
    assert results[2]["throughput"] == 0.5
    assert results[4]["throughput"] == 1.0


def test_core_utilization():
    results = compare_core_counts(make_processes())

    for result in results.values():
        assert all(
            utilization == 100
            for utilization in result["core_utilization"]
        )


def test_parallel_execution_overlaps():
    result = ParallelSimulator(
        make_processes(), core_count=2
    ).run()

    first = result["timeline"][0]
    second = result["timeline"][1]

    assert first["start"] == second["start"]
    assert first["core_id"] != second["core_id"]


@pytest.mark.parametrize("cores", [0, 3, 5, -1, True])
def test_invalid_core_count(cores):
    with pytest.raises(ValueError):
        ParallelSimulator(make_processes(), core_count=cores)


def test_empty_process_list():
    with pytest.raises(ValueError):
        ParallelSimulator([], core_count=2)


def test_race_condition():
    result = race_condition_demo()

    assert result["expected"] == 2
    assert result["without_lock"] == 1
    assert result["race_detected"] is True


def test_synchronization():
    result = race_condition_demo()

    assert result["with_lock"] == 2
    assert result["synchronization_successful"] is True


def test_deadlock_detection():
    result = deadlock_demo()

    assert result["deadlock_detected"] is True
    assert result["initial_cycle"] == ["P1", "P2", "P1"]


def test_deadlock_resolution():
    result = deadlock_demo()

    assert result["resolved"] is True


def test_parallel_repeatability():
    processes = make_processes()
    simulator = ParallelSimulator(processes, core_count=2)

    first = simulator.run()
    second = simulator.run()

    assert first == second
