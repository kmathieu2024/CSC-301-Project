from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.managers.device import DeviceRequestSpec
    from src.managers.network import NetworkRequestSpec


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
    priority: int = 1

    # Mapping from CPU time already executed to a virtual address.
    memory_references: dict[int, int] = field(default_factory=dict)
    # CPU-time offsets mapped to durations spent blocked.
    blocking_requests: dict[int, float] = field(default_factory=dict)
    # CPU-time offsets mapped to file-system commands.
    file_requests: dict[int, str] = field(default_factory=dict)
    # Synchronous device inputs; keyword-only to preserve all positional fields.
    device_requests: dict[int, DeviceRequestSpec] = field(
        default_factory=dict, kw_only=True,
    )
    network_requests: dict[int, NetworkRequestSpec] = field(
        default_factory=dict, kw_only=True,
    )

    # Simulation state
    remaining_time: int = 0
    state: ProcessState = ProcessState.NEW

    # Scheduling results
    start_time: int | None = None
    completion_time: int | None = None
    waiting_time: int = 0
    blocked_time: float = 0
    turnaround_time: int = 0
    response_time: int | None = None

    def __post_init__(self):
        """Initialize values that depend on process input."""
        self.remaining_time = self.burst_time
        if not isinstance(self.device_requests, Mapping):
            raise ValueError("device_requests must be an offset-to-spec mapping")
        self.device_requests = dict(self.device_requests)
        if not isinstance(self.network_requests, Mapping):
            raise ValueError("network_requests must be an offset-to-spec mapping")
        # Topology-dependent validation is performed by SimulationEngine.
        self.network_requests = dict(self.network_requests)

    def reset(self):
        """Reset the process so the simulation can run again."""
        self.remaining_time = self.burst_time
        self.state = ProcessState.NEW

        self.start_time = None
        self.completion_time = None

        self.waiting_time = 0
        self.blocked_time = 0
        self.turnaround_time = 0
        self.response_time = None
