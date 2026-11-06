
"""Synchronization, race-condition, and deadlock demonstrations."""


def race_condition_demo():
    """Compare shared-counter updates with and without synchronization."""

    initial_value = 0

    # Two processes read the same value before either writes.
    process_a_read = initial_value
    process_b_read = initial_value

    # Both write their calculated value, losing one update.
    unsafe_counter = process_a_read + 1
    unsafe_counter = process_b_read + 1

    # A lock makes each read-update-write operation atomic.
    safe_counter = initial_value

    for process in ("P1", "P2"):
        safe_counter += 1

    return {
        "expected": 2,
        "without_lock": unsafe_counter,
        "with_lock": safe_counter,
        "race_detected": unsafe_counter != 2,
        "synchronization_successful": safe_counter == 2
    }


def deadlock_demo():
    """Demonstrate circular waiting between two processes."""

    resources = {
        "R1": "P1",
        "R2": "P2"
    }

    waiting = {
        "P1": "R2",
        "P2": "R1"
    }

    # P1 holds R1 and waits for R2.
    # P2 holds R2 and waits for R1.
    deadlock_detected = (
        resources[waiting["P1"]] == "P2"
        and resources[waiting["P2"]] == "P1"
    )

    # Resolve the deadlock by releasing P2's resource.
    if deadlock_detected:
        resources["R2"] = None
        waiting["P1"] = None

    return {
        "deadlock_detected": deadlock_detected,
        "initial_cycle": ["P1", "P2", "P1"],
        "resolved": resources["R2"] is None
    }
