from dataclasses import dataclass
from enum import Enum


class ProcessState(Enum):
    """Possible states of a process during the simulation."""

    NEW = "NEW"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    TERMINATED = "TERMINATED"


@dataclass
class Process:
    """Represents a process in the OS simulator."""

    # Input values
    pid: str
    arrival_time: int
    burst_time: int
    priority: int = 0
    state: ProcessState = ProcessState.NEW

    # change states 
    def change_state(self, new_state: ProcessState):
        self.state = new_state

    # Simulation state
    remaining_time: int = 0
    state: ProcessState = ProcessState.NEW

    # Scheduling results
    start_time: int | None = None
    completion_time: int | None = None
    waiting_time: int = 0
    turnaround_time: int = 0
    response_time: int | None = None

    def __post_init__(self):
        """Initialize values that depend on process input."""
        self.remaining_time = self.burst_time

    def reset(self):
        """Reset the process so the simulation can run again."""
        self.remaining_time = self.burst_time
        self.state = ProcessState.NEW

        self.start_time = None
        self.completion_time = None

        self.waiting_time = 0
        self.turnaround_time = 0
        self.response_time = None