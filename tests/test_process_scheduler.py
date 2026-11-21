import pytest

from src.core.process import Process
from src.managers.process.scheduler import Scheduler


@pytest.mark.parametrize(
    ("policy", "expected_pid"),
    [("fcfs", "Z"), ("sjf", "A"), ("srtf", "Z"), ("priority", "A")],
)
def test_direct_selection_uses_policy_order_at_equal_arrival(policy, expected_pid):
    earlier_in_ready_list = Process("Z", 0, 5, priority=3)
    shorter_burst = Process("A", 0, 3, priority=1)
    earlier_in_ready_list.remaining_time = 2
    scheduler = Scheduler(policy)

    choice = scheduler.select_next([earlier_in_ready_list, shorter_burst], 0)

    assert choice.process.pid == expected_pid
    assert choice.max_run_time is None


def test_rr_rejoins_tail_after_consuming_quantum():
    first = Process("Z", 0, 5)
    second = Process("A", 2, 3)
    scheduler = Scheduler("rr", quantum=2)
    scheduler.on_ready(first, 0)
    choice = scheduler.select_next([first], 0)
    assert choice.process is first
    assert choice.max_run_time == 2
    scheduler.on_dispatch(first, 0)
    scheduler.on_cpu_progress(first, 2)
    assert scheduler.quantum_expired()

    scheduler.on_cpu_stop(first)
    scheduler.on_ready(second, 2)
    scheduler.on_ready(first, 2)
    choice = scheduler.select_next([first, second], 2)

    assert choice.process is second
    assert choice.max_run_time == 2


@pytest.mark.parametrize(
    ("policy", "options", "message"),
    [
        ("unknown", {}, "Unsupported algorithm"),
        ("rr", {"quantum": 0}, "Quantum must be positive"),
        ("priority", {"aging_interval": 0}, "Aging interval must be positive"),
    ],
)
def test_direct_scheduler_rejects_invalid_configuration(policy, options, message):
    with pytest.raises(ValueError, match=message):
        Scheduler(policy, **options)
