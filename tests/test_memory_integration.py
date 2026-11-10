from pathlib import Path

import pytest

from src.core.process import Process
from src.core.simulation import SimulationEngine
from src.managers.memory import MemoryManager
from src.managers.memory.memory_manager import NoFrameAvailable


def test_page_fault_blocks_only_requesting_process_and_resets():
    # P1 faults straight away. P2 should still be able to use the CPU.
    first = Process("P1", 0, 2, memory_references={0: 4})
    second = Process("P2", 0, 2)
    memory = MemoryManager(2, fault_latency=3)
    engine = SimulationEngine(
        [first, second], memory_manager=memory, scenario_id="m3-fault"
    )

    result = engine.run()

    # The fault wait is separate from time waiting in the ready queue.
    assert second.completion_time == 2
    assert first.completion_time == 5
    assert first.blocked_time == 3
    assert first.waiting_time == 0
    assert result["memory_metrics"]["references"] == 1
    assert result["memory_metrics"]["faults"] == 1
    assert result["memory_metrics"]["fault_latency"] == 3

    blocks = [e for e in result["events"] if e["event_type"] == "block"]
    unblocks = [e for e in result["events"] if e["event_type"] == "unblock"]
    assert len(blocks) == len(unblocks) == 1
    assert blocks[0]["timestamp"] == 0
    assert blocks[0]["previous_state"] == "RUNNING"
    assert blocks[0]["new_state"] == "WAITING"
    assert unblocks[0]["timestamp"] == 3
    assert unblocks[0]["new_state"] == "READY"
    assert all(e["scenario_id"] == "m3-fault" for e in result["events"])

    # Finished processes should not leave their pages behind.
    assert memory.frames == [None, None]
    assert memory.page_tables == {}

    # With no file changes in this case, the whole run should repeat exactly.
    engine.reset()
    assert memory.metrics()["references"] == 0
    assert engine.run() == result


def test_fifo_and_lru_same_trace():
    process = Process("P1", 0, 5)
    for policy, expected in [("fifo", [("P1", 4), ("P1", 2), ("P1", 3)]),
                             ("lru", [("P1", 1), ("P1", 4), ("P1", 3)])]:
        memory = MemoryManager(3, algorithm=policy)
        for address in (1, 2, 3, 1, 4):
            memory.reference(process, address)
        assert memory.frames == expected
        assert (memory.hits, memory.faults, memory.evictions) == (1, 4, 1)

    # Also check a longer trace, including every frame and eviction.
    # Use the same 12 virtual addresses for both policies.
    addresses = read_trace("trace_locality.txt")

    # Create the FIFO process and initially empty memory.
    process = Process("P1", 0, 12)
    memory = MemoryManager(
        frame_count=3, page_size=4096,
        algorithm="fifo", fault_latency=10
    )

    # Each row contains frame contents, fault status, and evicted page.
    # None means an empty frame or no eviction.
    expectedResults = [
        ([0, None, None], True, None),
        ([0, 1, None], True, None),
        ([0, 1, None], False, None),
        ([0, 1, 2], True, None),
        ([0, 1, 2], False, None),
        ([3, 1, 2], True, 0),
        ([3, 0, 2], True, 1),
        ([3, 0, 1], True, 2),
        ([3, 0, 1], False, None),
        ([2, 0, 1], True, 3),
        ([2, 0, 1], False, None),
        ([2, 3, 1], True, 0),
    ]

    # Execute references and check every frame, fault, and eviction.
    check_memory_trace(memory, process, addresses, expectedResults)

    # Check FIFO totals and accumulated fault delay.
    results = memory.metrics()
    assert results["references"] == 12, "FIFO reference count is incorrect"
    assert results["hits"] == 4, "FIFO hit count is incorrect"
    assert results["faults"] == 8, "FIFO fault count is incorrect"
    assert results["evictions"] == 5, "FIFO eviction count is incorrect"
    assert results["fault_rate"] == pytest.approx(8 / 12)
    assert results["fault_latency"] == 80

    # Create fresh objects so LRU starts from the same empty state.
    comparisonProcess = Process("P1", 0, 12)
    comparisonMemory = MemoryManager(
        frame_count=3, page_size=4096,
        algorithm="lru", fault_latency=10
    )

    # LRU keeps the frequently reused page 0 in memory.
    comparisonResults = [
        ([0, None, None], True, None),
        ([0, 1, None], True, None),
        ([0, 1, None], False, None),
        ([0, 1, 2], True, None),
        ([0, 1, 2], False, None),
        ([0, 3, 2], True, 1),
        ([0, 3, 2], False, None),
        ([0, 3, 1], True, 2),
        ([0, 3, 1], False, None),
        ([0, 2, 1], True, 3),
        ([0, 2, 1], False, None),
        ([0, 2, 3], True, 1),
    ]

    check_memory_trace(
        comparisonMemory, comparisonProcess, addresses, comparisonResults
    )

    # Check LRU totals using the same address trace.
    results = comparisonMemory.metrics()
    assert results["references"] == 12, "LRU reference count is incorrect"
    assert results["hits"] == 5, "LRU hit count is incorrect"
    assert results["faults"] == 7, "LRU fault count is incorrect"
    assert results["evictions"] == 4, "LRU eviction count is incorrect"
    assert results["fault_rate"] == pytest.approx(7 / 12)
    assert results["fault_latency"] == 70


def test_one_frame_protects_faulted_page_until_process_can_use_it():
    processes = [Process("P1", 0, 1, memory_references={0: 0}),
                 Process("P2", 0, 1, memory_references={0: 1})]
    engine = SimulationEngine(processes, algorithm="rr", quantum=1,
                              memory_manager=MemoryManager(1, fault_latency=2))
    for _ in range(30):
        if engine.completed == len(processes):
            break
        engine.step()
    assert engine.completed == 2
    assert engine.memory_manager.metrics()["faults"] == 2
    assert engine.memory_manager.metrics()["evictions"] <= 1
    assert any(e["action"] == "wait_for_frame" for e in engine.events)
    assert all(p.blocked_time >= 2 for p in processes)
    firstResults = engine.results()
    engine.reset()
    assert engine._frame_waiters == []
    assert not engine.memory_manager._protected

    # Waiting for a frame should not leave a stale request after reset.
    assert engine.run() == firstResults


def test_address_translation_at_page_boundaries():
    # Address 4095 ends page 0; address 4096 starts page 1.
    expectedTranslations = [
        (0, 0, 0), (4095, 0, 4095),
        (4096, 1, 0), (8191, 1, 4095),
    ]

    for policy in ["fifo", "lru"]:
        process = Process("P1", 0, 4)
        memory = MemoryManager(2, page_size=4096, algorithm=policy)

        # Check page, offset, and physical address for each boundary.
        for address, expectedPage, expectedOffset in expectedTranslations:
            result = memory.reference(process, address)
            assert result.page == expectedPage, "Page number is incorrect"
            assert result.offset == expectedOffset, "Page offset is incorrect"
            assert result.physical_address == result.frame * 4096 + expectedOffset


def test_one_frame_and_repeated_page_boundaries():
    for policy in ["fifo", "lru"]:
        process = Process("P1", 0, 10)
        memory = MemoryManager(1, algorithm=policy, fault_latency=2)

        # The first reference faults; the next nine references hit.
        for _ in range(10):
            memory.reference(process, 0)

        assert memory.hits == 9
        assert memory.faults == 1
        assert memory.evictions == 0
        assert memory.metrics()["fault_latency"] == 2

        # Reset and request alternating pages with only one frame.
        memory.reset()
        first = memory.reference(process, 0)
        second = memory.reference(process, 1)
        third = memory.reference(process, 0)

        assert first.evicted is None
        assert second.evicted == ("P1", 0)
        assert third.evicted == ("P1", 1)
        assert memory.faults == 3
        assert memory.evictions == 2


def test_memory_reset_is_reproducible():
    for policy in ["fifo", "lru"]:
        process = Process("P1", 0, 5)
        memory = MemoryManager(2, algorithm=policy, fault_latency=2)
        firstResults = []

        # Run and save the first set of translations and totals.
        for address in [0, 1, 0, 2, 1]:
            firstResults.append(memory.reference(process, address))
        firstMetrics = memory.metrics()

        # Reset should remove all pages and counters.
        memory.reset()
        assert memory.frames == [None, None]
        assert memory.page_tables == {}
        assert memory.metrics()["references"] == 0
        assert memory.metrics()["fault_rate"] == 0

        # Repeat identical input and compare exact results.
        secondResults = []
        for address in [0, 1, 0, 2, 1]:
            secondResults.append(memory.reference(process, address))
        assert secondResults == firstResults
        assert memory.metrics() == firstMetrics


def test_invalid_memory_inputs_do_not_change_valid_state():
    # Reject invalid frame counts and page sizes.
    for value in [0, -1, True, 1.5]:
        with pytest.raises(ValueError):
            MemoryManager(frame_count=value)
        with pytest.raises(ValueError):
            MemoryManager(frame_count=2, page_size=value)

    # Reject unsupported policies and invalid fault delays.
    for policy in ["clock", None]:
        with pytest.raises(ValueError):
            MemoryManager(2, algorithm=policy)
    for delay in [-1, True, float("inf"), float("nan")]:
        with pytest.raises(ValueError):
            MemoryManager(2, fault_latency=delay)

    process = Process("P1", 0, 1)
    memory = MemoryManager(2)
    memory.reference(process, 1)
    expectedFrames = list(memory.frames)
    expectedTable = dict(memory.page_tables["P1"])
    expectedMetrics = memory.metrics()

    # The manager accepts integers, not unparsed address strings.
    for address in [-1, 1.5, True, None, "0x0040", "invalid"]:
        with pytest.raises(ValueError):
            memory.reference(process, address)
        assert memory.frames == expectedFrames
        assert memory.page_tables["P1"] == expectedTable
        assert memory.metrics() == expectedMetrics


def test_process_memory_is_separate_and_released():
    p1 = Process("P1", 0, 1)
    p2 = Process("P2", 0, 1)
    memory = MemoryManager(2)

    # Equal page numbers from different processes are different pages.
    memory.reference(p1, 0)
    memory.reference(p2, 0)
    assert memory.frames == [("P1", 0), ("P2", 0)]

    # Releasing P1 must not remove P2's page.
    memory.release(p1)
    assert memory.frames == [None, ("P2", 0)]
    assert "P1" not in memory.page_tables
    assert memory.reference(p2, 0).fault is False


def test_protected_frame_wait_does_not_count_a_fault():
    p1 = Process("P1", 0, 1)
    p2 = Process("P2", 0, 1)
    memory = MemoryManager(1)
    memory.reference(p1, 0)
    memory.protect(p1, 0)
    expectedMetrics = memory.metrics()

    # P2 cannot replace a page protected for P1's pending reference.
    with pytest.raises(NoFrameAvailable):
        memory.reference(p2, 1)
    assert memory.frames == [("P1", 0)]
    assert memory.metrics() == expectedMetrics

    # Once protection ends, the replacement can proceed.
    memory.unprotect(p1, 0)
    assert memory.reference(p2, 1).evicted == ("P1", 0)


def test_cyclic_memory_stress_is_reproducible():
    # Cycle through 16 pages using only 3 frames, for 200 references.
    addresses = read_trace("trace_stress.txt")
    assert len(addresses) == 200

    for policy in ["fifo", "lru"]:
        process = Process("P1", 0, 200)
        memory = MemoryManager(3, page_size=4096, algorithm=policy)
        replays = []

        for _ in range(2):
            memory.reset()
            results = []
            for index, address in enumerate(addresses):
                result = memory.reference(process, address)
                expectedEviction = (
                    None if index < 3 else ("P1", addresses[index - 3] // 4096)
                )
                assert result.fault is True
                assert result.evicted == expectedEviction
                assert memory.metrics()["used_frames"] == min(index + 1, 3)
                results.append((result, tuple(memory.frames)))

            assert memory.hits == 0
            assert memory.faults == 200
            assert memory.evictions == 197
            replays.append(results)

        assert replays[0] == replays[1]


def read_trace(filename):
    folder = Path(__file__).resolve().parents[1] / "test_data" / "milestone3"
    return [
        int(line.strip(), 0)
        for line in (folder / filename).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def check_memory_trace(memory, process, addresses, expectedResults):
    # Like check_timeline in the reference test, compare every record.
    assert len(addresses) == len(expectedResults)

    for address, expected in zip(addresses, expectedResults):
        result = memory.reference(process, address)
        expectedFrames, expectedFault, expectedEviction = expected
        actualFrames = []

        for frame in memory.frames:
            actualFrames.append(None if frame is None else frame[1])

        assert actualFrames == expectedFrames, "Frame contents are incorrect"
        assert result.fault == expectedFault, "Hit/fault classification is incorrect"
        assert result.page == address // memory.page_size
        assert result.latency == (10 if expectedFault else 0)
        assert result.evicted == (
            None if expectedEviction is None else (process.pid, expectedEviction)
        ), "Evicted page is incorrect"

        # Every resident page must point back to the correct frame.
        for frameNumber, resident in enumerate(memory.frames):
            if resident is not None:
                pid, page = resident
                assert memory.page_tables[pid][page] == frameNumber
