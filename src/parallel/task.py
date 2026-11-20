
from dataclasses import dataclass, field
from src.core.process import ProcessState


@dataclass
class ParallelTask:
    task_id: str
    parent_job_id: str
    arrival_time: int
    burst_time: int
    dependencies: list[str] = field(default_factory=list)
    priority: int = 1
    required_resource: str | None = None

    # Runtime information
    state: ProcessState = field(default=ProcessState.NEW, init=False)
    remaining_time: int = field(init=False)
    assigned_core: int | None = field(default=None, init=False)
    start_time: int | None = field(default=None, init=False)
    completion_time: int | None = field(default=None, init=False)

    def __post_init__(self):
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError("Task ID must be a nonempty string")

        if not isinstance(self.parent_job_id, str) or not self.parent_job_id.strip():
            raise ValueError("Parent job ID must be a nonempty string")

        if type(self.arrival_time) is not int or self.arrival_time < 0:
            raise ValueError("Arrival time must be a nonnegative integer")

        if type(self.burst_time) is not int or self.burst_time <= 0:
            raise ValueError("Burst time must be a positive integer")

        if type(self.priority) is not int or self.priority < 1:
            raise ValueError("Priority must be a positive integer")

        if not isinstance(self.dependencies, list):
            raise ValueError("Dependencies must be a list")

        if any(not isinstance(dep, str) or not dep.strip()
               for dep in self.dependencies):
            raise ValueError("Dependency IDs must be nonempty strings")

        if len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError("Duplicate dependencies are not allowed")

        if self.task_id in self.dependencies:
            raise ValueError("A task cannot depend on itself")

        self.remaining_time = self.burst_time

    def reset(self):
        self.state = ProcessState.NEW
        self.remaining_time = self.burst_time
        self.assigned_core = None
        self.start_time = None
        self.completion_time = None
