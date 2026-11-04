from src.core.process import Process
from src.core.simulation import SimulationEngine


def test_blocked_process_yields_cpu_and_returns_to_ready():
    first = Process("P1", 0, 3, blocking_requests={1: 3})
    second = Process("P2", 0, 2)
    result = SimulationEngine([first, second], algorithm="rr", quantum=4).run()
    assert second.completion_time == 3
    assert first.completion_time == 6
    assert first.blocked_time == 3
    assert first.waiting_time == 0
    transitions = [(e["event_type"], e["previous_state"], e["new_state"]) for e in result["events"]]
    assert ("block", "RUNNING", "WAITING") in transitions
    assert ("unblock", "WAITING", "READY") in transitions


def test_cpu_idles_until_blocked_process_is_ready():
    process = Process("P1", 0, 2, blocking_requests={0: 2})
    result = SimulationEngine([process]).run()
    assert result["timeline"][0] == {"pid": "IDLE", "start": 0, "end": 2}
    assert process.completion_time == 4


def test_scheduling_counts_and_context_switch_overhead():
    first = Process("P1", 0, 3)
    second = Process("P2", 0, 2)
    engine = SimulationEngine([first, second], algorithm="rr", quantum=1, context_switch=0.5)
    result = engine.run()
    processes = {process["pid"]: process for process in result["processes"]}
    assert (processes["P1"]["dispatch_count"], processes["P1"]["preemption_count"]) == (3, 2)
    assert (processes["P2"]["dispatch_count"], processes["P2"]["preemption_count"]) == (2, 1)
    assert result["context_switch_overhead"] == 2
    engine.reset()
    reset = engine.results()
    assert reset["context_switch_overhead"] == 0
    assert all(p["dispatch_count"] == p["preemption_count"] == 0 for p in reset["processes"])
