
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

class SimulatedMutex:
    """A simulated lock with FIFO waiting."""

    def __init__(self, name):
        self.name = name
        self.owner = None
        self.waiting = []

    def acquire(self, task_id):
        if self.owner == task_id:
            return True

        if self.owner is None and (
            not self.waiting or self.waiting[0] == task_id
        ):
            if self.waiting:
                self.waiting.pop(0)
            self.owner = task_id
            return True

        if task_id not in self.waiting:
            self.waiting.append(task_id)

        return False

    def release(self, task_id):
        if self.owner != task_id:
            raise ValueError("Only the lock owner can release it")

        self.owner = self.waiting.pop(0) if self.waiting else None
        return self.owner


class SimulatedSemaphore:
    """Counting semaphore with FIFO waiting."""

    def __init__(self, name, permits=1):
        if type(permits) is not int or permits < 1:
            raise ValueError("Permits must be a positive integer")

        self.name = name
        self.available = permits
        self.holders = {}
        self.waiting = []

    def acquire(self, task_id):
        if self.available > 0 and (
            not self.waiting or self.waiting[0] == task_id
        ):
            if self.waiting:
                self.waiting.pop(0)

            self.available -= 1
            self.holders[task_id] = self.holders.get(task_id, 0) + 1
            return True

        if task_id not in self.waiting:
            self.waiting.append(task_id)

        return False

    def release(self, task_id):
        if self.holders.get(task_id, 0) == 0:
            raise ValueError("Task does not hold a semaphore permit")

        self.holders[task_id] -= 1

        if self.holders[task_id] == 0:
            del self.holders[task_id]

        self.available += 1

        if self.waiting:
            next_task = self.waiting.pop(0)
            self.available -= 1
            self.holders[next_task] = (
                self.holders.get(next_task, 0) + 1
            )
            return next_task

        return None


def detect_deadlock(mutexes):
    """Detect a circular wait among simulated mutex owners."""

    graph = {}

    for mutex in mutexes:
        if mutex.owner is not None:
            for waiting_task in mutex.waiting:
                graph.setdefault(waiting_task, set()).add(mutex.owner)

    visited = set()
    active = []
    on_path = set()

    def visit(task_id):
        if task_id in on_path:
            start = active.index(task_id)
            return active[start:] + [task_id]

        if task_id in visited:
            return []

        active.append(task_id)
        on_path.add(task_id)

        for next_task in sorted(graph.get(task_id, ())):
            cycle = visit(next_task)
            if cycle:
                return cycle

        active.pop()
        on_path.remove(task_id)
        visited.add(task_id)
        return []

    for task_id in sorted(graph):
        cycle = visit(task_id)
        if cycle:
            return cycle

    return []
