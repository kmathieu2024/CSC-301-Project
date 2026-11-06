from src.core.process import Process
from src.core.simulation import SimulationEngine
from src.managers.memory.memory_manager import MemoryManager


# TEST 1: File system integration
def test_file_request_creates_successful_event():
    process = Process(
        "P1", 0, 3,
        file_requests={0: "CREATE test.txt"}
    )

    engine = SimulationEngine([process])
    results = engine.run()

    file_events = [
        event for event in results["events"]
        if event["manager"] == "file"
    ]

    assert results["processes"][0]["state"] == "TERMINATED"
    assert len(file_events) == 1
    assert file_events[0]["action"] == "CREATE test.txt"
    assert file_events[0]["outcome"] == "success"


# TEST 2: Memory page fault and recovery
def test_memory_page_fault_blocks_and_resumes():
    process = Process(
        "P1", 0, 3,
        memory_references={0: 0}
    )

    memory = MemoryManager(
        frame_count=3,
        page_size=4,
        algorithm="fifo",
        fault_latency=2
    )

    engine = SimulationEngine(
        [process],
        memory_manager=memory
    )

    results = engine.run()
    events = results["events"]

    assert results["processes"][0]["state"] == "TERMINATED"

    assert any(
        e["manager"] == "memory"
        and e["event_type"] == "page_fault"
        for e in events
    )

    assert any(
        e["event_type"] == "block"
        and e["new_state"] == "WAITING"
        for e in events
    )

    assert any(
        e["event_type"] == "unblock"
        and e["new_state"] == "READY"
        for e in events
    )


# TEST 3: Memory and file system work together
def test_memory_and_file_integration():
    process = Process(
        "P1", 0, 3,
        memory_references={0: 0},
        file_requests={0: "CREATE test.txt"}
    )

    memory = MemoryManager(
        frame_count=3,
        page_size=4,
        algorithm="fifo",
        fault_latency=2
    )

    engine = SimulationEngine(
        [process],
        memory_manager=memory
    )

    results = engine.run()
    events = results["events"]

    assert results["processes"][0]["state"] == "TERMINATED"

    assert any(
        e["manager"] == "memory"
        for e in events
    )

    assert any(
        e["manager"] == "file"
        and e["outcome"] == "success"
        for e in events
    )


# TEST 4: File system reset is reproducible
def test_file_reset_reproducibility():
    process = Process(
        "P1", 0, 3,
        file_requests={0: "CREATE test.txt"}
    )

    engine = SimulationEngine([process])

    first = engine.run()

    engine.reset()

    second = engine.run()

    first_events = [
        e for e in first["events"]
        if e["manager"] == "file"
    ]

    second_events = [
        e for e in second["events"]
        if e["manager"] == "file"
    ]

    assert first_events == second_events


# TEST 5: Invalid file command is recorded as an error
def test_invalid_file_command():
    process = Process(
        "P1", 0, 3,
        file_requests={0: "touch test.txt"}
    )

    engine = SimulationEngine([process])
    results = engine.run()

    file_events = [
        e for e in results["events"]
        if e["manager"] == "file"
    ]

    assert len(file_events) == 1
    assert file_events[0]["outcome"] == "error"
    assert results["processes"][0]["state"] == "TERMINATED"


# TEST 6: File requests execute only once
def test_file_request_executes_once():
    process = Process(
        "P1", 0, 3,
        file_requests={0: "CREATE test.txt"}
    )

    engine = SimulationEngine([process])
    results = engine.run()

    file_events = [
        e for e in results["events"]
        if e["manager"] == "file"
    ]

    assert len(file_events) == 1


# TEST 7: File requests at different CPU offsets
def test_multiple_file_requests():
    process = Process(
        "P1", 0, 4,
        file_requests={
            0: "CREATE test.txt",
            2: "CREATE second.txt"
        }
    )

    engine = SimulationEngine([process])
    results = engine.run()

    file_events = [
        e for e in results["events"]
        if e["manager"] == "file"
    ]

    assert len(file_events) == 2
    assert all(e["outcome"] == "success" for e in file_events)


# TEST 8: Invalid file request offset is rejected
def test_invalid_file_request_offset():
    import pytest

    process = Process(
        "P1", 0, 3,
        file_requests={5: "CREATE test.txt"}
    )

    with pytest.raises(ValueError):
        SimulationEngine([process])


# TEST 9: Unified events contain required fields
def test_unified_event_fields():
    process = Process(
        "P1", 0, 3,
        file_requests={0: "CREATE test.txt"}
    )

    engine = SimulationEngine([process])
    results = engine.run()

    file_event = next(
        e for e in results["events"]
        if e["manager"] == "file"
    )

    required_fields = [
        "timestamp",
        "manager",
        "event_type",
        "entity_id",
        "action",
        "previous_state",
        "new_state",
        "outcome",
        "duration",
        "message",
        "scenario_id"
    ]

    for field in required_fields:
        assert field in file_event


# TEST 10: Memory fault latency affects simulation timing
def test_memory_fault_latency():
    process = Process(
        "P1", 0, 3,
        memory_references={0: 0}
    )

    memory = MemoryManager(
        frame_count=3,
        page_size=4,
        algorithm="fifo",
        fault_latency=2
    )

    engine = SimulationEngine(
        [process],
        memory_manager=memory
    )

    results = engine.run()
    events = results["events"]

    block_event = next(
        e for e in events
        if e["event_type"] == "block"
    )

    unblock_event = next(
        e for e in events
        if e["event_type"] == "unblock"
    )

    assert unblock_event["timestamp"] - block_event["timestamp"] == 2