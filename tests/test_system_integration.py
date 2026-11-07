import pytest

from src.managers.file import FileManager

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

    # Make sure the command really created a file, not just a success event.
    assert engine.file_manager.read_file("/test.txt") == ""
    assert file_events[0]["entity_id"] == "P1"
    assert file_events[0]["timestamp"] == 0


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

    assert process.blocked_time == 2
    assert process.completion_time == 5
    assert results["memory_metrics"]["references"] == 1
    assert memory.frames == [None, None, None]


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

    # The file command runs once even though the fault sends P1 back to READY.
    fileEvents = [e for e in events if e["manager"] == "file"]
    memoryEvents = [e for e in events if e["manager"] == "memory"]
    assert len(fileEvents) == len(memoryEvents) == 1
    assert fileEvents[0]["timestamp"] == memoryEvents[0]["timestamp"] == 0
    assert engine.file_manager.read_file("/test.txt") == ""
    assert process.completion_time == 5


# TEST 4: Reset preserves the file system and reruns file requests
def test_file_reset_preserves_state():
    # Reset starts another run. It does not wipe the files in this build.
    manager = FileManager()
    manager.create_file("/kept.txt", "Keep this")
    process = Process("P1", 0, 3, file_requests={0: "CREATE test.txt"})
    engine = SimulationEngine([process], file_manager=manager)
    policy = manager.authorizer

    first = engine.run()
    before = snapshot_file_state(manager._root)
    engine.reset()

    # Keep the same manager, policy, file contents and metadata.
    assert engine.file_manager is manager
    assert manager.authorizer is policy
    assert engine.results()["file_tree"] == first["file_tree"]
    assert snapshot_file_state(manager._root) == before
    assert process.state.value == "NEW"
    assert engine.events == []

    second = engine.run()
    firstEvents = [e for e in first["events"] if e["manager"] == "file"]
    secondEvents = [e for e in second["events"] if e["manager"] == "file"]

    # CREATE runs again, but the file is already there this time.
    assert len(firstEvents) == len(secondEvents) == 1
    assert firstEvents[0]["outcome"] == "success"
    assert secondEvents[0]["outcome"] == "error"
    assert "already exists" in secondEvents[0]["message"].lower()
    assert manager.read_file("/kept.txt") == "Keep this"


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
    process = Process("P1", 0, 3, file_requests={0: "CREATE test.txt"}, memory_references={0: 0})
    other = Process("P2", 0, 2)
    engine = SimulationEngine(
        [process, other], algorithm="rr", quantum=1,
        memory_manager=MemoryManager(2, fault_latency=2)
    )

    results = engine.run()
    fileEvents = [e for e in results["events"] if e["manager"] == "file"]

    # Resuming after a fault and taking more CPU turns must not repeat CREATE.
    assert len(fileEvents) == 1
    assert fileEvents[0]["outcome"] == "success"
    assert engine.file_manager.list_directory("/") == ["test.txt"]
    assert process.state.value == "TERMINATED"


# TEST 7: File requests at different CPU offsets
def test_multiple_file_requests():
    process = Process("P1", 0, 4, file_requests={
        0: "CREATE test.txt", 1: 'WRITE test.txt "Hello"',
        2: "CREATE second.txt", 3: "READ test.txt",
    })
    engine = SimulationEngine([process])

    results = engine.run()
    events = [e for e in results["events"] if e["manager"] == "file"]

    # Each command should happen at its own CPU offset.
    assert len(events) == 4
    assert [e["timestamp"] for e in events] == [0, 1, 2, 3]
    assert all(e["outcome"] == "success" for e in events)
    assert engine.file_manager.read_file("/test.txt") == "Hello"
    assert engine.file_manager.list_directory("/") == ["second.txt", "test.txt"]


# TEST 8: Invalid file request offset is rejected
def test_invalid_file_request_offset():
    # Check the edges and the command itself, not only an offset past the burst.
    for requests in [{-1: "LS"}, {3: "LS"}, {5: "LS"}, {True: "LS"}, {1.5: "LS"}, {0: ""}, {0: None}]:
        process = Process("P1", 0, 3, file_requests=requests)
        with pytest.raises(ValueError):
            SimulationEngine([process])


# TEST 9: Unified events contain required fields
def test_unified_event_fields():
    process = Process("P1", 0, 3, file_requests={0: "CREATE test.txt"}, memory_references={0: 0})
    engine = SimulationEngine(
        [process], memory_manager=MemoryManager(2, fault_latency=2),
        scenario_id="m3-shared-events"
    )
    results = engine.run()

    requiredFields = [
        "timestamp", "manager", "event_type", "entity_id", "action",
        "previous_state", "new_state", "outcome", "duration", "message", "scenario_id",
    ]
    # All three managers should use the same common event fields.
    assert {e["manager"] for e in results["events"]} == {"process", "memory", "file"}
    for event in results["events"]:
        for field in requiredFields:
            assert field in event
        assert event["entity_id"] == "P1"
        assert event["scenario_id"] == "m3-shared-events"
        assert event["duration"] >= 0


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


class WritePolicy:
    def __init__(self, deny_write):
        self.deny_write = deny_write

    def authorize(self, user, action, path):
        return not (self.deny_write and action == "write")


@pytest.mark.xfail(strict=True, reason="M3-F06: successful READ content is misclassified")
def test_reading_error_prefix_text_is_still_a_successful_read():
    # Error: is legitimate file content here, not a failed operation.
    process = Process("P1", 0, 3, file_requests={
        0: "CREATE /notes.txt",
        1: 'WRITE /notes.txt "Error: example text"',
        2: "READ /notes.txt",
    })
    results = SimulationEngine([process]).run()
    events = [e for e in results["events"] if e["manager"] == "file"]
    assert events[-1]["outcome"] == "success"


def test_zero_fault_delay_and_reset_during_wait():
    # Zero fault delay must not block or double-count the first reference.
    process = Process("P1", 0, 2, memory_references={0: 0, 1: 0})
    engine = SimulationEngine([process], memory_manager=MemoryManager(1))
    results = engine.run()
    assert results["memory_metrics"]["faults"] == 1
    assert results["memory_metrics"]["hits"] == 1
    assert process.blocked_time == 0
    assert process.completion_time == 2

    # In a fresh run, reset while a fault completion is still pending.
    process = Process("P1", 0, 2, memory_references={0: 0})
    engine = SimulationEngine([process], memory_manager=MemoryManager(1, fault_latency=3))
    engine.step()
    engine.step()
    assert process.state.value == "WAITING"
    engine.reset()
    assert process.state.value == "NEW"
    assert engine.results()["memory_metrics"]["references"] == 0
    results = engine.run()
    assert process.completion_time == 5
    assert results["memory_metrics"]["faults"] == 1
    assert len([e for e in results["events"] if e["event_type"] == "unblock"]) == 1


def test_allowed_and_denied_process_file_writes():
    for denyWrite in [False, True]:
        manager = FileManager(authorizer=WritePolicy(denyWrite))
        process = Process("P1", 0, 3, file_requests={
            0: "CREATE /notes.txt", 1: 'WRITE /notes.txt "Hello"', 2: "READ /notes.txt",
        })
        engine = SimulationEngine([process], file_manager=manager)

        results = engine.run()
        events = [e for e in results["events"] if e["manager"] == "file"]

        # A denied write should be logged and leave the created file empty.
        assert [e["outcome"] for e in events] == ["success", "error" if denyWrite else "success", "success"]
        assert manager.read_file("/notes.txt") == ("" if denyWrite else "Hello")
        assert process.blocked_time == 0


def snapshot_file_state(node):
    # Check metadata without a getter changing the access time.
    metadata = node.metadata
    return (
        node.name, node.is_directory, node.content,
        metadata.created_at, metadata.modified_at, metadata.accessed_at,
        metadata.owner, metadata.permissions, metadata.size,
        {name: snapshot_file_state(child) for name, child in node.children.items()},
    )
