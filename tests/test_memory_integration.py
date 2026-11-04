from src.core.process import Process
from src.core.simulation import SimulationEngine
from src.managers.memory import MemoryManager


def test_page_fault_blocks_only_requesting_process_and_resets():
    first = Process("P1", 0, 2, memory_references={0: 4})
    second = Process("P2", 0, 2)
    engine = SimulationEngine([first, second], memory_manager=MemoryManager(2, fault_latency=3))
    result = engine.run()
    assert second.completion_time == 2
    assert first.completion_time == 5
    assert first.blocked_time == 3
    assert first.waiting_time == 0
    assert result["memory_metrics"]["faults"] == 1
    transitions = [(e["event_type"], e["previous_state"], e["new_state"]) for e in result["events"]]
    assert ("block", "RUNNING", "WAITING") in transitions
    assert ("unblock", "WAITING", "READY") in transitions
    engine.reset()
    assert engine.run()["memory_metrics"]["faults"] == 1


def test_fifo_and_lru_same_trace():
    process = Process("P1", 0, 5)
    for policy, expected in [("fifo", [("P1", 4), ("P1", 2), ("P1", 3)]),
                             ("lru", [("P1", 1), ("P1", 4), ("P1", 3)])]:
        memory = MemoryManager(3, algorithm=policy)
        for address in (1, 2, 3, 1, 4):
            memory.reference(process, address)
        assert memory.frames == expected
        assert (memory.hits, memory.faults, memory.evictions) == (1, 4, 1)
