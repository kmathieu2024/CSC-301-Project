"""CPU scheduling policies. The simulation engine is the sole owner of time.

Contract:
- The engine owns arrival events, current_time, process states, remaining_time,
  context-switch overhead, execution timeline, metrics, and event log.
- The engine calls on_ready() once whenever a process enters READY (including
  after a Round Robin quantum expires), and on_dispatch() when CPU execution starts.
- For RR, call on_cpu_stop() after a time slice ends, then update the process
  state/remaining_time and call on_ready() if it is still runnable.
- A non-preemptive process remains selected until it completes or blocks.
- For SRTF, the engine can call should_preempt() after new arrivals.
- The engine MUST re-evaluate selection after context-switch overhead because
  new processes may arrive during that interval.
"""

from __future__ import annotations
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.process import Process


SUPPORTED_ALGORITHMS = frozenset({"fcfs", "sjf", "srtf", "rr", "priority"})


@dataclass(frozen=True)
class SchedulingDecision:
    # pick which process runs next 

    process: Process | None
    max_run_time: int | float | None  # RR quantum cap, None for other policies


def validate_processes(processes: list[Process]) -> None:
    # make sure processes are valid 
    if not processes:
        raise ValueError("At least one process is required.")
    seen: set[str] = set()
    for p in processes:
        if not isinstance(p.pid, str) or not p.pid.strip():
            raise ValueError("PID must be a nonempty string.")
        if p.pid in seen:
            raise ValueError(f"Duplicate PID: {p.pid}")
        seen.add(p.pid)
        if not isinstance(p.arrival_time, (int, float)) or isinstance(p.arrival_time, bool) or p.arrival_time < 0:
            raise ValueError(f"Invalid arrival time for {p.pid}")
        if not isinstance(p.burst_time, (int, float)) or isinstance(p.burst_time, bool) or p.burst_time <= 0:
            raise ValueError(f"Invalid burst time for {p.pid}")
        if not isinstance(p.priority, int) or isinstance(p.priority, bool) or p.priority < 0:
            raise ValueError(f"Invalid priority for {p.pid}")


class Scheduler:
    # creates the scheduling policy 
    # The engine supplies the current time and updated Process objects
    # new Scheduler instance should be created for each simulation run or reset

    # make sure the variables are valid 
    def __init__(self,algorithm: str,*,quantum: int | float = 4,aging_interval: int | float = 4,) -> None:
        self.algorithm = algorithm.lower()
        if self.algorithm not in SUPPORTED_ALGORITHMS:
            raise ValueError(f"Unsupported algorithm: {algorithm}")
        if isinstance(quantum, bool) or not isinstance(quantum, (int, float)) or quantum <= 0:
            raise ValueError("Quantum must be positive")
        if isinstance(aging_interval, bool) or not isinstance(aging_interval, (int, float)) or aging_interval <= 0:
            raise ValueError("Aging interval must be positive")
        self.quantum = quantum
        self.aging_interval = aging_interval
        self.reset()

    def reset(self) -> None:
        # Clear scheduling state
        self._rr_queue: deque[Process] = deque()
        self._rr_queued: set[str] = set()
        self._rr_active: str | None = None
        self._rr_budget: int | float = self.quantum
        # Actual READY entry time (needed for correct aging after preemption).
        self._ready_since: dict[str, int | float] = {}
        # Cumulative READY waiting (not time running or blocked).
        self._ready_wait: dict[str, int | float] = {}

    def on_ready(self, process: Process, current_time: int | float) -> None:
        # records when a process is ready 
        # call when a process is ready after the state update
        if process.pid in self._ready_since:
            raise ValueError(f"Process already ready: {process.pid}")
        self._ready_since[process.pid] = current_time
        self._ready_wait.setdefault(process.pid, 0)
        if self.algorithm == "rr" and process.pid != self._rr_active:
            if process.pid not in self._rr_queued:
                self._rr_queue.append(process)
                self._rr_queued.add(process.pid)

    def on_dispatch(self, process: Process, current_time: int | float) -> None:
        # records when a process is running 
        # call when process actually begins running, after any CS overhead.
        entered = self._ready_since.pop(process.pid, None)
        if entered is None:
            raise ValueError(f"Process was not READY: {process.pid}")
        self._ready_wait[process.pid] += current_time - entered
        if self.algorithm == "rr":
            if self._rr_active != process.pid:
                raise ValueError("RR dispatch must match select_next()")

    def on_cpu_stop(self, process: Process) -> None:
        """ Call when CPU execution stops (completion, blocking, or preemption)
        For RR, engine must call on_ready() afterward if the process remains
        runnable. RR quantum is reset for its next turn. 
        """
        if self.algorithm == "rr":
            if self._rr_active != process.pid:
                raise ValueError("Stopping a process not active in RR")
            self._rr_active = None
            self._rr_budget = self.quantum

    def on_cpu_progress(self, process: Process, duration: int | float) -> None:
        # how much of the time quantum was used in round robin
        # only RR needs to remember quantum consumed between event boundaries
        if duration < 0:
            raise ValueError("Duration cannot be negative")
        if self.algorithm == "rr":
            if self._rr_active != process.pid:
                raise ValueError("RR progress must belong to active process")
            if duration > self._rr_budget:
                raise ValueError("RR execution exceeds remaining quantum")
            self._rr_budget -= duration

    def quantum_expired(self) -> bool:
        return self.algorithm == "rr" and self._rr_active is not None and self._rr_budget <= 0

    def aging_priority(self, p: Process, current_time: int | float) -> int:
        # Lower numeric priority is higher. Only time actually spent READY ages.
        waited = self._ready_wait.get(p.pid, 0)
        if p.pid in self._ready_since:
            waited += current_time - self._ready_since[p.pid]
        return max(0, p.priority - int(waited // self.aging_interval))

    def select_next(self,ready: list[Process],current_time: int | float,) -> SchedulingDecision:
        # makes a decision of the ready processes, which should run next 
        # For RR this dequeues the selected process and reserves it until dispatch.
        # Call on_dispatch() once the engine starts its CPU execution.
        if not ready:
            return SchedulingDecision(None, None)
        ready_by_pid = {p.pid: p for p in ready}
        if len(ready_by_pid) != len(ready):
            raise ValueError("READY list contains duplicate PIDs")
        if self.algorithm == "rr":
            if self._rr_active is not None:
                p = ready_by_pid.get(self._rr_active)
                if p is None:
                    raise ValueError("Reserved RR process missing from READY list")
                return SchedulingDecision(p, self._rr_budget)
            while self._rr_queue:
                queued = self._rr_queue.popleft()
                self._rr_queued.remove(queued.pid)
                p = ready_by_pid.get(queued.pid)
                if p is not None:
                    self._rr_active = p.pid
                    self._rr_budget = self.quantum
                    return SchedulingDecision(p, self._rr_budget)
            raise ValueError("RR READY processes must be registered with on_ready()")

        # breaks ties based on arrival time in queue
        if self.algorithm == "fcfs":
            p = min(ready, key=lambda x: (x.arrival_time))
        # breaks ties based on arrival time in the queue, then PID (alphabetical)
        elif self.algorithm == "sjf":
            p = min(ready, key=lambda x: (x.burst_time, x.arrival_time, x.pid))
        # breaks ties based on arrival time in the queue, then PID (alphabetical)
        elif self.algorithm == "srtf":
            p = min(ready, key=lambda x: (x.remaining_time, x.arrival_time, x.pid))
        else:  # non-preemptive priority with aging
            p = min(ready, key=lambda x: (self.aging_priority(x, current_time), x.arrival_time, x.pid))
        return SchedulingDecision(p, None)

    def should_preempt(self,running: Process,ready: list[Process],current_time: int | float,) -> bool:
        # Only SRTF preempts on a new, strictly shorter remaining burst.
        if self.algorithm != "srtf" or not ready:
            return False
        best = self.select_next(ready, current_time).process
        assert best is not None
        return best.remaining_time < running.remaining_time
