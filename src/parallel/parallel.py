
from src.core.process import Process, ProcessState


class ParallelSimulator:
    """Simulate FCFS execution on 1, 2, or 4 CPU cores."""

    def __init__(self, processes, core_count=1):
        if core_count not in (1, 2, 4) or isinstance(core_count, bool):
            raise ValueError("Core count must be 1, 2, or 4")

        if not processes:
            raise ValueError("At least one process is required")

        self.processes = processes
        self.core_count = core_count

    def run(self):
        # Reset process execution states.
        for process in self.processes:
            process.reset()

        # Track when each core becomes available.
        core_available = [0] * self.core_count
        core_busy = [0] * self.core_count
        timeline = []

        # FCFS order: arrival time, then process ID.
        ordered = sorted(
            self.processes,
            key=lambda p: (p.arrival_time, p.pid)
        )

        for process in ordered:
            # Select the earliest available core.
            core_id = min(
                range(self.core_count),
                key=lambda core: (core_available[core], core)
            )

            start = max(
                process.arrival_time,
                core_available[core_id]
            )

            end = start + process.burst_time

            process.state = ProcessState.TERMINATED
            process.start_time = start
            process.completion_time = end
            process.remaining_time = 0
            process.response_time = start - process.arrival_time
            process.waiting_time = start - process.arrival_time
            process.turnaround_time = end - process.arrival_time

            timeline.append({
                "pid": process.pid,
                "core_id": core_id,
                "start": start,
                "end": end
            })

            core_available[core_id] = end
            core_busy[core_id] += process.burst_time

        makespan = max(core_available)

        utilization = [
            100 * busy / makespan if makespan else 0
            for busy in core_busy
        ]

        throughput = (
            len(self.processes) / makespan
            if makespan else 0
        )

        return {
            "core_count": self.core_count,
            "makespan": makespan,
            "throughput": throughput,
            "core_utilization": utilization,
            "timeline": sorted(
                timeline,
                key=lambda event: (
                    event["start"],
                    event["core_id"]
                )
            )
        }


def compare_core_counts(processes):
    """Compare performance using 1, 2, and 4 CPU cores."""

    results = {}

    for count in (1, 2, 4):
        simulation = ParallelSimulator(
            processes,
            core_count=count
        )
        results[count] = simulation.run()

    baseline = results[1]["makespan"]

    for count, result in results.items():
        makespan = result["makespan"]

        speedup = baseline / makespan if makespan else 0
        efficiency = speedup / count * 100

        result["speedup"] = speedup
        result["efficiency"] = efficiency

    return results


class TaskParallelSimulator:
    """Schedule dependent tasks across multiple CPU cores."""

    def __init__(self, tasks, core_count=2, policy="global", resources=None):
        if type(core_count) is not int or core_count not in (1, 2, 4):
            raise ValueError("Core count must be 1, 2, or 4")

        if policy not in ("global", "partitioned"):
            raise ValueError("Invalid scheduling policy")

        if not tasks:
            raise ValueError("At least one task is required")

        self.tasks = list(tasks)
        self.core_count = core_count
        self.policy = policy
        self.resources = dict(resources or {})

        for task in self.tasks:
            if task.required_resource is not None:
                if task.required_resource not in self.resources:
                    raise ValueError(
                        f"Unknown resource: {task.required_resource}"
                    )
        self.validate_dependencies()

    def validate_dependencies(self):
        ids = [task.task_id for task in self.tasks]

        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate task IDs")

        graph = {
            task.task_id: task.dependencies
            for task in self.tasks
        }

        visited = set()
        visiting = set()

        def visit(task_id):
            if task_id in visiting:
                raise ValueError("Circular dependency detected")

            if task_id in visited:
                return

            visiting.add(task_id)

            for dependency in graph[task_id]:
                if dependency not in graph:
                    raise ValueError("Unknown dependency")

                visit(dependency)

            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in graph:
            visit(task_id)

    def run(self):
        for task in self.tasks:
            task.reset()

        ordered = sorted(
            self.tasks,
            key=lambda task: (task.arrival_time, task.task_id)
        )

        # Static partitioning assigns each task a fixed core.
        owners = {
            task.task_id: index % self.core_count
            for index, task in enumerate(ordered)
        }

        completed = set()
        running = {}
        timeline = []
        core_busy = [0] * self.core_count
        sync_events = []
        blocked_tasks = set()
        granted_resources = set()
        now = 0

        while len(completed) < len(self.tasks):

            # Complete tasks finishing at this time.
            for core, (task, end) in list(running.items()):
                if end == now:
                    task.state = ProcessState.TERMINATED
                    task.remaining_time = 0
                    task.completion_time = now
                    resource_name = task.required_resource

                    if resource_name is not None:
                        next_task = self.resources[resource_name].release(
                            task.task_id
                        )

                        sync_events.append({
                            "time": now,
                            "task_id": task.task_id,
                            "action": "release",
                            "resource": resource_name
                        })

                        if next_task is not None:
                            granted_resources.add(
                                (resource_name, next_task)
                            )
                    completed.add(task.task_id)
                    del running[core]

            # Update waiting and ready task states.
            for task in ordered:
                if task.start_time is not None:
                    continue

                if task.arrival_time > now:
                    task.state = ProcessState.NEW
                elif all(
                    dependency in completed
                    for dependency in task.dependencies
                ):
                    task.state = ProcessState.READY
                else:
                    task.state = ProcessState.WAITING

# Assign ready tasks to available cores.
            for core in range(self.core_count):
                if core in running:
                    continue

                ready = [
                    candidate for candidate in ordered
                    if candidate.state == ProcessState.READY
                    and (
                        self.policy == "global"
                        or owners[candidate.task_id] == core
                    )
                ]

                task = None

                for candidate in ready:
                    resource_name = candidate.required_resource

                    if resource_name is not None:
                        grant = (resource_name, candidate.task_id)

                        if grant in granted_resources:
                            granted_resources.remove(grant)
                            acquired = True
                        else:
                            acquired = self.resources[
                                resource_name
                            ].acquire(candidate.task_id)

                        if not acquired:
                            candidate.state = ProcessState.WAITING

                            if candidate.task_id not in blocked_tasks:
                                blocked_tasks.add(candidate.task_id)
                                sync_events.append({
                                    "time": now,
                                    "task_id": candidate.task_id,
                                    "action": "blocked",
                                    "resource": resource_name
                                })

                            continue

                        if candidate.task_id in blocked_tasks:
                            blocked_tasks.remove(candidate.task_id)

                            sync_events.append({
                                "time": now,
                                "task_id": candidate.task_id,
                                "action": "woken",
                                "resource": resource_name
                            })

                        sync_events.append({
                            "time": now,
                            "task_id": candidate.task_id,
                            "action": "acquire",
                            "resource": resource_name
                        })

                    task = candidate
                    break

                if task is None:
                    continue

                end = now + task.burst_time

                task.state = ProcessState.RUNNING
                task.start_time = now
                task.assigned_core = core

                running[core] = (task, end)
                core_busy[core] += task.burst_time

                timeline.append({
                    "pid": task.task_id,
                    "core_id": core,
                    "start": now,
                    "end": end
                })

            if len(completed) == len(self.tasks):
                break

            # Advance to the next simulation event.
            upcoming = [end for _, end in running.values()]

            upcoming.extend(
                task.arrival_time
                for task in ordered
                if task.start_time is None
                and task.arrival_time > now
            )

            if not upcoming:
                raise RuntimeError("No runnable tasks remain")

            now = min(upcoming)

        makespan = now

        return {
            "core_count": self.core_count,
            "policy": self.policy,
            "sync_events": sync_events,
            "makespan": makespan,
            "throughput": len(self.tasks) / makespan if makespan else 0,
            "core_utilization": [
                100 * busy / makespan if makespan else 0
                for busy in core_busy
            ],
            "timeline": sorted(
                timeline,
                key=lambda event: (event["start"], event["core_id"])
            )
        }
