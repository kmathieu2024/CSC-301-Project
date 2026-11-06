# CSC-301-Project

## Project Overview
This project is a Python-based operating system simulator
developed by a six-member team.

The simulator will model several operating system components,
including process management, memory management, file systems,
security, device management, and networking.

## Team Member Names and Roles
**Kyla Jones-Atkinson** - Project Lead and Integration <br>
**Kimberly Mathieu** - Memory and Process Engineer <br>
**Quintin Ampofo** - File and Security Engineer <br>
**Danae Ludy** - Device and Network Engineer <br>
**Jonali Gaylor** - Dashboard and Visualization Engineer <br>
**Eden Hudson** - Quality and Documentation Engineer <br>

## Project Structure
- src/: Simulator source code
- dashboard/: User interface
- tests/: Automated tests
- test_data/: Sample inputs and expected outputs
- docs/: Project documentation
- designs/: Component design documents

## Requirements
- Python 3.11 or newer
- pip
- pytest

## Installation

1. Clone the repository.
2. Open the project in VS Code.
3. Install dependencies:

   python -m pip install -r requirements.txt

## Running Tests

Run:

   python -m pytest tests/ -v

## Development Workflow
1. Create a branch for your assigned task.
2. Make and test your changes.
3. Commit and push your branch.
4. Open a pull request.
5. Request a teammate's review before merging.

See docs/git_workflow.md for details.

## Current Status
Milestone 1: Repository setup, architecture design,
baseline test cases, and project documentation.

### Running the Simulation Dashboard

1. Install the dependencies:
   python -m pip install -r requirements.txt
2. Start the application:
   python app.py
3. Open http://127.0.0.1:5000/ in your browser.
4. Use Run, Pause, Step, or Reset to control the simulation.
5. In Process Manager, select **Download results (JSON)** to save the displayed
   simulation snapshot as `simulation-results.json`. The download includes the
   algorithm, simulation time, seed, process metrics, averages, CPU utilization,
   timeline, and event history returned by the simulator. Partial runs and reset
   snapshots can also be exported; averages are final only after completion.
   The button becomes available once simulation results have been displayed.

### Memory simulation

Pass a `MemoryManager` to `SimulationEngine` and provide each process with a
`memory_references` dictionary mapping elapsed CPU time to virtual addresses:

```python
from src.core.process import Process
from src.core.simulation import SimulationEngine
from src.managers.memory import MemoryManager

process = Process("P1", 0, 5, memory_references={0: 0, 1: 8, 3: 0})
memory = MemoryManager(frame_count=3, page_size=4, algorithm="lru", fault_latency=2)
results = SimulationEngine([process], memory_manager=memory).run()
print(results["memory_metrics"])
```

Addresses are split into pages and offsets using `page_size`. Frames are shared
across processes; FIFO and LRU select victims globally. A fault with latency
blocks the requesting process while other ready processes can run.
Memory is released when a process terminates. Memory events include the
translation and any evicted `(pid, page)` pair. `memory.frames`,
`memory.page_tables`, and `memory.metrics()` expose current state; reset clears
memory and counters. Simulation reset also resets its memory manager.
### Simulating a blocked process

`blocking_requests` maps elapsed CPU time to a positive blocking duration. For
example, `Process("P1", 0, 3, blocking_requests={1: 3})` runs for one CPU
time unit, waits for three simulated time units, then becomes ready again.
Other ready processes can run during that wait. Blocked time is reported
separately from time waiting in the ready queue. Reset clears the results so
the same requests can be simulated again. Page faults with nonzero latency use
the same waiting and ready transitions.

Simulation results include a `dispatch_count` and `preemption_count` for each
process. Dispatches count each time a process starts or resumes execution;
preemptions count times it is returned to the ready queue before completion,
such as an expired Round Robin quantum. Blocking is tracked separately.
`context_switch_overhead` is the total time spent in context switches.

A page loaded for a fault with nonzero latency is temporarily protected from
FIFO or LRU eviction until the requesting process resumes and uses it. If all
frames are protected, another process requesting a missing page waits for a
frame instead of replacing one. When protection ends, a waiting process is
made ready to retry. Time spent waiting for a frame counts as blocked time;
the page fault is counted when a frame can be assigned. This prevents the
simulator from repeatedly replacing pages before their owners can use them.
This is a simplified simulation of memory pressure, not a complete model of
an operating system's page reclaim or disk I/O.


### File system simulation

The file system is implemented by `FileManager` in
`src/managers/file/__init__.py`. It provides a hierarchical, in-memory file
system for creating and managing files and directories. Its contents are held
in memory and are reset when a new `FileManager` instance is created.

#### Features

- Create files with optional initial text content.
- Read, replace, append to, and delete files.
- Create and remove directories; directories must be empty before removal.
- List directory contents and change the current working directory.
- Retrieve file and directory metadata, including creation, modification, and
  access timestamps, owner, size, and permissions.
- Maintain a separate current working directory for each process while sharing
  the same file and directory tree. Pass a `process_id` to use a process-specific
  working directory. Omitting it uses the default context for compatibility.
- Delegate authorization checks to an injected authorizer. The default
  `AllowAllAuthorizer` permits all operations.

#### Python usage

```python
from src.managers.file import FileManager

file_system = FileManager()

# Create a directory and a file.
file_system.make_directory("/home")
file_system.make_directory("/home/P1")
file_system.create_file("/home/P1/notes.txt", "Initial notes")

# Give process P1 its own current working directory.
file_system.change_directory("/home/P1", process_id="P1")

# Relative paths are resolved from P1's working directory.
print(file_system.present_working_directory(process_id="P1"))
print(file_system.read_file("notes.txt", process_id="P1"))

# Append content and inspect the directory.
file_system.write_file(
    "notes.txt", "\\nMore notes", append=True, process_id="P1"
)
print(file_system.list_directory(".", process_id="P1"))
```

#### File system methods

| Method | Description |
| --- | --- |
| `create_file(path, content="")` | Create a file, optionally with initial content. |
| `read_file(path)` | Return a file's text content. |
| `write_file(path, content, append=False)` | Replace content or append when `append=True`. |
| `delete_file(path)` | Delete a file. |
| `make_directory(path)` | Create one directory; its parent must already exist. |
| `remove_directory(path)` | Remove an empty directory. The root directory and directories used as a process's current directory (or their ancestors) are protected. |
| `list_directory(path=".")` | Return sorted entry names in a directory. |
| `change_directory(path)` | Change the selected process's current working directory. |
| `present_working_directory()` | Return the selected process's absolute working directory. |
| `get_metadata(path)` | Return a `FileMetadata` snapshot for a file or directory. |
| `execute_command(command)` | Execute a supported command string and return its result or a user-friendly error message. |

The methods that access paths accept an optional keyword-only `process_id`.
Use the same ID for successive operations by the same simulated process so
relative paths and `CD`/`PWD` behave consistently. Absolute paths beginning
with `/` are resolved from the file system root.

#### Command interface

`execute_command()` supports these commands:

| Command | Example | Purpose |
| --- | --- | --- |
| `CREATE` | `CREATE notes.txt` | Create an empty file. |
| `WRITE` | `WRITE notes.txt "Hello world"` | Replace a file's content. |
| `READ` | `READ notes.txt` | Display file content. |
| `DELETE` | `DELETE notes.txt` | Delete a file. |
| `MKDIR` | `MKDIR documents` | Create a directory. |
| `RMDIR` | `RMDIR documents` | Remove an empty directory. |
| `LS` | `LS` or `LS /home` | List directory entries. |
| `CD` | `CD /home/P1` | Change the working directory. |
| `PWD` | `PWD` | Display the working directory. |
| `STAT` | `STAT notes.txt` | Return file or directory metadata. |

Pass the process ID separately when executing a command:

```python
file_system.execute_command("CD /home/P1", process_id="P1")
print(file_system.execute_command("PWD", process_id="P1"))
```

Direct method calls raise file-system exceptions derived from `FileSystemError`,
including `FileNotFoundError`, `FileAlreadyExistsError`, `NotADirectoryError`,
`IsADirectoryError`, `DirectoryNotEmptyError`, and `AuthorizationError`.
`execute_command()` catches supported operation and input errors and returns
user-friendly error strings.

#### Authorization

A custom authorizer can be supplied when creating a `FileManager`. It must
implement `authorize(user, action, path)` and return `True` to allow the
operation or `False` to reject it. Operations accept an optional `user`
argument, which is passed to the authorizer.

```python
from src.managers.file import FileManager

class ReadOnlyAuthorizer:
    def authorize(self, user, action, path):
        return action in {"read", "list", "metadata"}

file_system = FileManager(authorizer=ReadOnlyAuthorizer())
```

The built-in `AllowAllAuthorizer` is intended as the default policy for
standalone use; it does not enforce access restrictions.


### System integration

The `SimulationEngine` in `src/core/simulation.py` connects the
Scheduler, Memory Manager, and File Manager. It coordinates process
execution and keeps track of simulation time.

The Scheduler determines which process uses the CPU. The Memory
Manager handles memory references and page replacement, while the
File Manager handles file and directory operations.

These components work through the Simulation Engine so their
activities can be recorded together.

#### Updated architecture

The Simulation Engine is the central component of the simulator.
It connects the different managers and coordinates their operations
during process execution.

The main components are:

- `app.py`: Receives simulation input and creates the engine.
- `src/core/process.py`: Stores process information and requests.
- `src/core/simulation.py`: Coordinates execution, timing, and events.
- `src/managers/process/`: Handles CPU scheduling.
- `src/managers/memory/`: Handles memory references and page replacement.
- `src/managers/file/`: Handles file and directory operations.
- `dashboard/`: Provides the interface for viewing simulation results.

When a simulation runs, the Scheduler selects a process for
execution. If the process has a memory reference or file request,
the Simulation Engine sends the request to the appropriate manager.

The managers handle their operations, and the Simulation Engine
records the activity in a shared event history. These results
can then be used by the dashboard.

#### Process requests

Processes can include memory references and file requests that
occur at specific points during CPU execution.

For example:

```python
from src.core.process import Process
from src.core.simulation import SimulationEngine
from src.managers.memory import MemoryManager

process = Process(
    "P1",
    0,
    5,
    memory_references={0: 0, 2: 8},
    file_requests={
        0: "CREATE notes.txt",
        3: "READ notes.txt"
    }
)

memory = MemoryManager(
    frame_count=3,
    page_size=4,
    algorithm="fifo",
    fault_latency=2
)

engine = SimulationEngine([process], memory_manager=memory)
results = engine.run()

print(results["events"])
```

The Memory Manager handles page references and records page faults.
A page fault with nonzero latency causes the process to enter the
waiting state until it can resume execution.

The File Manager executes commands using the requesting process ID.
This allows each process to maintain its own working directory.

#### Unified event logging

The Simulation Engine records process, memory, and file-system
events using the same simulation timeline.

File-system events include:

- Timestamp
- Manager and event type
- Process ID
- Command
- Previous and new process states
- Success or error outcome
- Duration and message
- Scenario ID

Memory events also include page information, frame assignments,
address translation, and eviction details when applicable.

File operations currently have zero simulated duration.

#### Reset behavior

Reset clears the simulation timeline, event history, completed
requests, and process execution states.

The Memory Manager resets its memory state and counters. The
Simulation Engine creates a new File Manager using the existing
authorizer so file operations can be repeated in a new simulation.

The File Manager does not currently provide a separate reset
method. Custom File Manager implementations may not be preserved
when the simulation resets.

#### Integration testing

Automated integration tests are located in
`tests/test_system_integration.py`.

The tests verify:

- Successful file operations
- Invalid file commands and request offsets
- Multiple file requests
- Memory page faults
- Process blocking and recovery
- Combined memory and file-system execution
- Unified event logging
- Page-fault latency
- Reset reproducibility

Run the integration tests:

```bash
python -m pytest -q tests/test_system_integration.py
```

All 10 integration tests passed.

The backend test suite also passed 74 tests when excluding the
dashboard and visualization browser tests. Those tests require
Playwright and have not yet been verified in this environment.

### Parallel computing

The parallel computing module is located in `src/parallel/`.
It provides a separate simulation for comparing process execution
using 1, 2, and 4 CPU cores.

The `ParallelSimulator` class uses FCFS scheduling and assigns
processes to the earliest available core. Each core can execute
a different process at the same time.

#### Performance metrics

The simulator calculates:

- Makespan: Total time required to complete all processes.
- Speedup: Single-core makespan divided by multicore makespan.
- Efficiency: Speedup divided by the number of cores, as a percentage.
- Throughput: Number of completed processes per unit of time.
- Core utilization: Percentage of simulation time each core is busy.

The `compare_core_counts()` function runs the same workload
using 1, 2, and 4 cores and compares the results.

#### Synchronization and deadlocks

The `src/parallel/sync.py` module provides demonstrations of
synchronization, race conditions, and deadlocks.

The race-condition demonstration shows how two processes updating
the same shared counter without synchronization can lose an update.
It compares the incorrect result with the correct result produced
when updates are performed in a synchronized order.

The deadlock demonstration shows two processes holding resources
while waiting for resources owned by each other. It detects the
circular wait and demonstrates resolution by releasing a resource.

These are deterministic demonstrations rather than actual
multithreaded execution or a general-purpose deadlock detector.

#### Parallel computing tests

Automated tests are located in `test_parallel.py`.

The tests verify core counts, makespan, speedup, efficiency,
throughput, utilization, overlapping execution, input validation,
race conditions, synchronization, deadlock detection, and
repeatability.

Run the parallel computing tests:

```bash
python -m pytest -q test_parallel.py
```

The backend test suite passed 92 tests, excluding the dashboard
and visualization browser tests that require Playwright.

### Port 5000 Troubleshooting

On some macOS systems, another service may occupy port 5000, resulting in HTTP 403 errors.

To launch the dashboard on port 5001 instead:

```bash
flask --app app run --debug --port 5001
```

Then open `http://127.0.0.1:5001` in your browser.

### Browser Testing Dependencies

Install the Playwright testing dependencies using:

```bash
python -m pip install pytest-playwright
```

Run the automated tests using:

```bash
python -m pytest -q
```

Browser tests that use a hardcoded local server URL require the server to be available at that address.
