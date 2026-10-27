import pytest

from src.core.process import Process
from src.managers.process.scheduler import Scheduler, validate_processes


def test_priority_defaults_to_one_and_rejects_zero():
    assert Process('P1', 0, 1).priority == 1
    with pytest.raises(ValueError, match='Invalid priority'):
        validate_processes([Process('P1', 0, 1, priority=0)])


def test_aging_stops_at_one():
    process = Process('P1', 0, 1, priority=2)
    scheduler = Scheduler('priority', aging_interval=4)
    scheduler.on_ready(process, 0)
    assert scheduler.aging_priority(process, 4) == 1
    assert scheduler.aging_priority(process, 100) == 1

