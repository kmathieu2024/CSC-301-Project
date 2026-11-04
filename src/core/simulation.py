
import random
import math

from src.core.process import ProcessState
from src.managers.memory.memory_manager import NoFrameAvailable
from src.managers.process.scheduler import Scheduler, validate_processes


class SimulationEngine:
    # Controls simulation time, process arrivals, and event history.
    # Scheduler decides which process uses the CPU.

    def __init__(self, processes, algorithm="fcfs", quantum=4,
                 aging_interval=4, context_switch=0,
                 scenario_id="default", seed=0, memory_manager=None):

        self.processes = list(processes)
        validate_processes(self.processes)

        if isinstance(context_switch, bool) or not isinstance(context_switch, (int, float)):
            raise ValueError("Context-switch time must be a number")

        if context_switch < 0:
            raise ValueError("Context-switch time cannot be negative")

        for process in self.processes:
            for offset, duration in process.blocking_requests.items():
                if (isinstance(offset, bool) or not isinstance(offset, (int, float))
                        or not math.isfinite(offset) or not 0 <= offset < process.burst_time
                        or isinstance(duration, bool) or not isinstance(duration, (int, float))
                        or not math.isfinite(duration) or duration <= 0):
                    raise ValueError("Invalid blocking request for process " + process.pid)

        self.algorithm = algorithm.lower()
        self.quantum = quantum
        self.aging_interval = aging_interval
        self.context_switch = context_switch
        self.scenario_id = scenario_id
        self.seed = seed
        self.memory_manager = memory_manager
        for process in self.processes:
            for offset, address in process.memory_references.items():
                if (isinstance(offset, bool) or not isinstance(offset, int) or
                        offset < 0 or offset >= process.burst_time or
                        isinstance(address, bool) or not isinstance(address, int) or address < 0):
                    raise ValueError("Invalid memory reference for process " + process.pid)
        if memory_manager is None and any(p.memory_references for p in self.processes):
            raise ValueError("Memory references require a memory manager")

        self.reset()

    def reset(self):
        # Reset everything to start a new simulation.

        for process in self.processes:
            process.reset()

        self.scheduler = Scheduler(
            self.algorithm,
            quantum=self.quantum,
            aging_interval=self.aging_interval
        )

        if self.memory_manager is not None:
            self.memory_manager.reset()
        self._memory_done = set()
        self._memory_pending = set()
        self._frame_waiters = []
        self.random = random.Random(self.seed)

        self.current_time = 0
        self.events = []
        self.timeline = []
        self.event_queue = []
        self.completed = 0
        self.last_pid = None
        self.next_event_id = 0
        self._runner = None
        self._blocks_done = set()

        # Add aarrivals to the event queue.

        for process in self.processes:
            self.schedule_event(
                process.arrival_time,
                "arrival",
                process
            )

    def schedule_event(self, time, event_type, process):
        # Store upcoming events in chronological order.
        # Arrival and unblock events share the same queue.

        if time < self.current_time:
            raise ValueError("Cannot schedule an event in the past")

        if event_type not in ("arrival", "unblock"):
            raise ValueError("Unsupported event type: " + event_type)

        # Events are sorted by time, PID, and insertion order.

        entry = (
            time,
            process.pid,
            self.next_event_id,
            event_type,
            process
        )

        self.event_queue.append(entry)
        self.event_queue.sort()

        self.next_event_id += 1

    def log(self, process, event_type, action, old_state, duration=0):
        # Record an event.

        event = {
            "timestamp": self.current_time,
            "manager": "process",
            "event_type": event_type,
            "entity_id": process.pid,
            "action": action,
            "previous_state": old_state.value,
            "new_state": process.state.value,
            "outcome": "success",
            "duration": duration,
            "message": "",
            "scenario_id": self.scenario_id
        }

        self.events.append(event)

    def check_arrivals(self):
        # Check if any processes arrived.

        while self.event_queue and self.event_queue[0][0] <= self.current_time:

            time, pid, number, event_type, process = self.event_queue.pop(0)

            expected = ProcessState.NEW if event_type == "arrival" else ProcessState.WAITING
            if process.state != expected:
                raise ValueError("Unexpected state for " + event_type + ": " + process.pid)

            old_state = process.state

            process.state = ProcessState.READY

            self.scheduler.on_ready(process, time)

            self.log(
                process,
                event_type,
                "admit" if event_type == "arrival" else "unblock",
                old_state
            )

    def _block_process(self, process, duration):
        process.blocked_time += duration
        old_state = process.state
        process.state = ProcessState.WAITING
        self.schedule_event(self.current_time + duration, "unblock", process)
        self.log(process, "block", "wait", old_state, duration)

    def _wait_for_frame(self, process):
        old_state = process.state
        process.state = ProcessState.WAITING
        self._frame_waiters.append((process, self.current_time))
        self.log(process, "block", "wait_for_frame", old_state)

    def _wake_frame_waiter(self):
        if not self._frame_waiters:
            return
        process, started = self._frame_waiters.pop(0)
        process.blocked_time += self.current_time - started
        old_state = process.state
        process.state = ProcessState.READY
        self.scheduler.on_ready(process, self.current_time)
        self.log(process, "unblock", "frame_available", old_state)

    def ready_processes(self):
        # Find all processes that are ready.

        ready = []

        for process in self.processes:

            if process.state == ProcessState.READY:
                ready.append(process)

        return ready

    def advance_time(self, duration):
        # Move the simulation clock forward.

        if duration < 0:
            raise ValueError("Time cannot move backward")

        end_time = self.current_time + duration

        # Process any arrivals that happenned 

        while self.event_queue and self.event_queue[0][0] <= end_time:

            self.current_time = self.event_queue[0][0]

            self.check_arrivals()

        self.current_time = end_time

    def _execute(self):
        # Run the simulation until all processess finish.

        while self.completed < len(self.processes):

            self.check_arrivals()

            ready = self.ready_processes()

            # Move forward if no processes are ready.

            if not ready:

                if not self.event_queue:
                    raise RuntimeError("No ready processes or upcoming arrivals")

                next_time = self.event_queue[0][0]

                if next_time > self.current_time:

                    self.timeline.append({
                        "pid": "IDLE",
                        "start": self.current_time,
                        "end": next_time
                    })

                self.advance_time(
                    next_time - self.current_time
                )

                self.last_pid = None

                ready = self.ready_processes()

            # Ask the scheduler which process should run next.

            decision = self.scheduler.select_next(
                ready,
                self.current_time
            )

            process = decision.process

            if process is None:
                raise RuntimeError("Scheduler did not select a process")

            # Apply context-switch if/when changing processes.

            if self.last_pid is not None and process.pid != self.last_pid:

                if self.context_switch > 0:

                    start = self.current_time

                    self.advance_time(self.context_switch)

                    self.timeline.append({
                        "pid": "CS",
                        "start": start,
                        "end": self.current_time
                    })

                    # Round Robin .
                    # Other algorithms can select again after new arrivals.

                    if self.algorithm != "rr":

                        ready = self.ready_processes()

                        decision = self.scheduler.select_next(
                            ready,
                            self.current_time
                        )

                        process = decision.process

                        if process is None:
                            raise RuntimeError("No process ready after switching")

            # Change the process state to RUNNING.

            old_state = process.state

            process.state = ProcessState.RUNNING

            self.scheduler.on_dispatch(
                process,
                self.current_time
            )

            # Record the first time the process runs.

            if process.start_time is None:

                process.start_time = self.current_time

                process.response_time = (
                    self.current_time - process.arrival_time
                )

            self.log(
                process,
                "dispatch",
                "start",
                old_state
            )

            # Return the RUNNING state before executing the process.
            yield self.results()

            time_left = decision.max_run_time

            # run the selected process.

            while process.remaining_time > 0:

                executed = process.burst_time - process.remaining_time
                key = (process.pid, executed)
                if executed in process.blocking_requests and key not in self._blocks_done:
                    duration = process.blocking_requests[executed]
                    self._blocks_done.add(key)
                    self._block_process(process, duration)
                    break

                if (self.memory_manager is not None and
                        executed in process.memory_references and key not in self._memory_done):
                    address = process.memory_references[executed]
                    page = address // self.memory_manager.page_size
                    if key in self._memory_pending and page in self.memory_manager.page_tables.get(process.pid, {}):
                        self._memory_pending.remove(key)
                        self.memory_manager.unprotect(process, page)
                        self._memory_done.add(key)
                        self._wake_frame_waiter()
                    else:
                        try:
                            result = self.memory_manager.reference(process, address)
                        except NoFrameAvailable:
                            self._wait_for_frame(process)
                            break
                        self.events.append({
                            "timestamp": self.current_time, "manager": "memory",
                            "event_type": "page_fault" if result.fault else "page_hit",
                            "entity_id": process.pid, "action": "reference",
                            "previous_state": process.state.value, "new_state": process.state.value,
                            "outcome": "success", "duration": result.latency,
                            "message": "", "scenario_id": self.scenario_id,
                            "address": result.address, "page": result.page, "frame": result.frame,
                            "physical_address": result.physical_address, "evicted": result.evicted,
                        })
                        if result.latency:
                            self.memory_manager.protect(process, page)
                            self._memory_pending.add(key)
                            self._block_process(process, result.latency)
                            break
                        self._memory_pending.discard(key)
                        self._memory_done.add(key)

                duration = process.remaining_time
                future_offsets = [offset for offset in process.blocking_requests if offset > executed]
                future_offsets += [offset for offset in process.memory_references if offset > executed]
                if future_offsets:
                    duration = min(duration, min(future_offsets) - executed)


                if time_left is not None:
                    duration = min(duration, time_left)

                # Stop at the next scheduled arrival (if necessary).

                if self.event_queue:

                    next_time = self.event_queue[0][0]

                    duration = min(
                        duration,
                        next_time - self.current_time
                    )

                if duration <= 0:
                    self.check_arrivals()
                    continue

                start = self.current_time

                # Update simulated and remaining CPU time.

                self.advance_time(duration)

                process.remaining_time -= duration

                if time_left is not None:
                    time_left -= duration

                self.scheduler.on_cpu_progress(
                    process,
                    duration
                )

                # Record CPU execution.

                self.timeline.append({
                    "pid": process.pid,
                    "start": start,
                    "end": self.current_time
                })

                self.log(
                    process,
                    "execution",
                    "run",
                    process.state,
                    duration
                )

                # Do not requeue completed processes.

                if process.remaining_time == 0:
                    break

                # Check if RR has expired.

                if self.scheduler.quantum_expired():
                    break

                # Check if a different process should run.

                if self.scheduler.should_preempt(
                    process,
                    self.ready_processes(),
                    self.current_time
                ):
                    break

            self.scheduler.on_cpu_stop(process)

            old_state = process.state

            # Check if process has completed.

            if process.state == ProcessState.WAITING:
                pass
            elif process.remaining_time == 0:

                process.state = ProcessState.TERMINATED

                process.completion_time = self.current_time

                process.turnaround_time = (
                    self.current_time - process.arrival_time
                )

                process.waiting_time = (
                    process.turnaround_time - process.burst_time - process.blocked_time
                )

                if self.memory_manager is not None:
                    self.memory_manager.release(process)
                self.completed += 1

                self.log(
                    process,
                    "completion",
                    "terminate",
                    old_state
                )

            else:
                # Return unfinished processes to the ready queue.
                # New arrivals have already been added first.

                process.state = ProcessState.READY

                self.scheduler.on_ready(
                    process,
                    self.current_time
                )

                self.log(
                    process,
                    "preemption",
                    "requeue",
                    old_state
                )

            self.last_pid = process.pid

            yield self.results()

    def step(self):
        if self.completed == len(self.processes):
            return self.results()

        if self._runner is None:
            self._runner = self._execute()

        try:
            return next(self._runner)
        except StopIteration:
            return self.results()

    def run(self):
        while self.completed < len(self.processes):
            self.step()

        return self.results()
    def results(self):
        # Calculate the final simulation results.

        busy_time = 0

        # Count time spent executing.

        for item in self.timeline:

            if item["pid"] != "IDLE" and item["pid"] != "CS":

                busy_time += (
                    item["end"] - item["start"]
                )

        context_switch_overhead = sum(
            item["end"] - item["start"]
            for item in self.timeline if item["pid"] == "CS"
        )
        dispatch_counts = {process.pid: 0 for process in self.processes}
        preemption_counts = {process.pid: 0 for process in self.processes}
        for event in self.events:
            if event["manager"] != "process":
                continue
            if event["event_type"] == "dispatch":
                dispatch_counts[event["entity_id"]] += 1
            elif event["event_type"] == "preemption":
                preemption_counts[event["entity_id"]] += 1

        process_results = []

        total_wait = 0
        total_turnaround = 0
        total_response = 0

        # results for every process.

        for process in self.processes:

            process_results.append({
                "pid": process.pid,
                "state": process.state.value,
                "arrival_time": process.arrival_time,
                "burst_time": process.burst_time,
                "priority": process.priority,
                "start_time": process.start_time,
                "completion_time": process.completion_time,
                "waiting_time": process.waiting_time,
                "blocked_time": process.blocked_time,
                "dispatch_count": dispatch_counts[process.pid],
                "preemption_count": preemption_counts[process.pid],
                "turnaround_time": process.turnaround_time,
                "response_time": process.response_time
            })

            total_wait += process.waiting_time

            total_turnaround += process.turnaround_time

            if process.response_time is not None:
                total_response += process.response_time

        count = len(self.processes)

        average_wait = 0
        average_turnaround = 0
        average_response = 0

        # Calculate the average process times.

        if count > 0 and self.completed == count:

            average_wait = total_wait / count

            average_turnaround = total_turnaround / count

            average_response = total_response / count

        # Calculate CPU utilization.

        utilization = 0

        if self.current_time > 0:

            utilization = (
                100 * busy_time / self.current_time
            )

        # Copy event history into a list.

        event_list = []

        for event in self.events:
            event_list.append(event.copy())

        throughput = (
            self.completed / self.current_time
            if self.current_time > 0
            else 0
        )

        # Return the simulation results.

        return {
            "memory_metrics": self.memory_manager.metrics() if self.memory_manager is not None else None,
            "algorithm": self.algorithm,
            "current_time": self.current_time,
            "seed": self.seed,
            "processes": process_results,
            "average_waiting_time": average_wait,
            "average_turnaround_time": average_turnaround,
            "average_response_time": average_response,
            "cpu_utilization": utilization,
            "context_switch_overhead": context_switch_overhead,
            "timeline": self.timeline,
            "events": event_list,
            "throughput": throughput
        }