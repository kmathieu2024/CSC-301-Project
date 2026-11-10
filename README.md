# CSC-301-Project

## Project Overview
This project is a Python-based operating system simulator
developed by a six-member team.

The simulator models several operating system components,
including process management, memory management, file systems,
and parallel computing. Security, device management, and
networking are planned for subsequent development milestones.

The project uses a Python simulation backend, Flask API,
and an interactive HTML, CSS, and JavaScript dashboard to
demonstrate operating system concepts, resource management,
and system performance.

The simulator supports deterministic process scheduling,
memory page replacement, file system operations, and
event-based integration between managers.

## Team Member Names and Roles
**Kyla Jones-Atkinson** - Project Lead and Integration <br>
**Kimberly Mathieu** - Memory and Process Engineer <br>
**Quintin Ampofo** - File and Security Engineer <br>
**Danae Ludy** - Device and Network Engineer <br>
**Jonali Gaylor** - Dashboard and Visualization Engineer <br>
**Eden Hudson** - Quality and Documentation Engineer <br>

## Project Structure
- src/: Simulator source code
  - core/: Process models and discrete-event simulation engine
  - managers/: Operating system manager implementations
    - process/: CPU scheduling algorithms
    - memory/: Memory management and page replacement
    - file/: File system operations
    - security/: Security Manager (planned)
    - device/: Device Manager (planned)
    - network/: Network Manager (planned)
  - parallel/: Multicore simulation and synchronization demonstrations
- dashboard/: HTML, CSS, and JavaScript user interface
- app.py: Flask application and simulation API
- tests/: Automated unit, integration, and dashboard tests
- test_parallel.py: Parallel computing tests
- test_data/: Sample inputs, traces, and expected results
- docs/: Project documentation and development guidelines
- .github/workflows/: Automated testing workflow

## Requirements
- Python 3.11 or newer
- pip
- Flask
- pytest
- pytest-playwright (for browser-based tests)
- Chromium (for Playwright browser tests)

## Installation

1. Clone the repository.
2. Open the project in VS Code.
3. Install dependencies:

   ```bash
   python -m pip install -r requirements.txt
   ```
   
4. To install development and browser-testing dependencies:

   ```bash
   python -m pip install -r requirements-dev.txt
   python -m playwright install chromium
   ```

## Running Tests

Run the main test suite:

```bash
python -m pytest tests/ -v
```

Run the parallel computing tests:

```bash
python -m pytest test_parallel.py -v
```

Run all tests:

```bash
python -m pytest -q
```

GitHub Actions automatically runs the main test suite
on pushes and pull requests using Python 3.11.

As of November 9, 2026, the latest verified main-branch
GitHub Actions run reported:

- 163 passed
- 3 expected failures
- 0 unexpected failures

The three expected failures concern unsupported file
command aliases: `mkfile`, `cat`, and `rm`.

These results describe the CI-configured `tests/` suite.
The separate `test_parallel.py` module is not included
in that workflow's test command.

## Development Workflow
1. Create a branch for your assigned task.
2. Make and test your changes.
3. Commit and push your branch.
4. Open a pull request.
5. Request a teammate's review before merging.

See docs/git_workflow.md for details.

## Current Status

**Milestone 1 - Completed**
- Established the repository structure and development workflow.
- Developed the system architecture and manager interfaces.
- Created the requirements traceability matrix and risk register.
- Implemented the initial interactive dashboard prototype.
- Established baseline scenarios and testing documentation.

**Milestone 2 - Completed**
- Implemented the discrete-event simulation engine.
- Implemented FCFS, SJF, SRTF, Round Robin, and Priority
  scheduling with aging.
- Added configurable quantum and context-switch overhead.
- Implemented deterministic process arrival and scheduling behavior.
- Added process state transitions, including blocking and unblocking.
- Implemented the CPU Gantt chart and process metrics table.
- Added simulation controls, event logging, and JSON results export.
- Expanded automated scheduling and simulation tests.

**Milestone 3 - In Progress**
- Implemented FIFO and LRU memory page replacement.
- Added configurable memory frames, page size, and fault latency.
- Integrated page faults with process blocking and unblocking.
- Implemented the in-memory File Manager and file operations.
- Added file metadata, ownership, permissions, and storage tracking.
- Integrated Process, Memory, and File Manager events.
- Added memory statistics, frame visualization, and file tree
  displays to the dashboard.
- Implemented an initial 1-, 2-, and 4-core parallel simulator.
- Added race-condition and deadlock demonstrations.
- Expanded automated manager and integration tests.
- Continuing dashboard input controls, manager comparisons,
  and visualization improvements.

**Milestone 4 - Planned**
- Implement the Security, Device, and Network Managers.
- Integrate all six managers through the simulation engine.
- Complete the remaining dashboard workspaces.
- Expand independent manager and system integration testing.
- Finalize performance experiments, documentation,
  and project demonstration.

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

#### Memory Manager Status

The Memory Manager currently supports FIFO and LRU page
replacement, configurable page sizes and frame counts,
virtual-to-physical address translation, and page-fault
latency.

Memory references are integrated with process execution.
Page faults with nonzero latency cause a process to enter
the WAITING state until its unblock event occurs.

The manager tracks page references, hits, faults,
evictions, fault rates, and occupied frames.

The dashboard displays memory statistics and physical
frame allocation. Additional memory input controls and
visualizations are being developed in the Milestone 3
dashboard branch.

Optimal page replacement is not implemented on the
current main branch. A separate implementation was
proposed in PR #65 but was closed without merging.

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
  access timestamps, owner, size (UTF-8 bytes), and permissions.
- Open and close files using integer handles and explicit read/write/append modes.
- Report aggregate file-content storage with `storage_used()` or `STORAGE`.
- Enforce the stored three-character `rwx` mask; the mask is a simple shared
  permission class applied to requests, while `owner` records the creator identity.
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
| `APPEND` | `APPEND notes.txt " more"` | Append text to a file. |
| `OPEN` | `OPEN notes.txt r` | Open a file and return a handle. Modes: `r`, `w`, `a`, `r+`, `w+`, `a+`. |
| `CLOSE` | `CLOSE 1` | Close an open handle. |
| `HANDLE_READ` | `HANDLE_READ 1` | Read through an open handle. |
| `HANDLE_WRITE` | `HANDLE_WRITE 1 "more"` | Write through an open handle. |
| `CHMOD` | `CHMOD notes.txt rw-` | Set the three-character permission mask. |
| `STORAGE` | `STORAGE` | Return aggregate UTF-8 bytes used by all files. |

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

#### File Manager Updates

The File Manager backend has been expanded to support
additional operations and validation requirements.

Recent improvements include:

- APPEND support for existing files.
- OPEN and CLOSE operations with file handles.
- HANDLE_READ and HANDLE_WRITE operations.
- Read, write, append, and read/write file modes.
- Process-specific working directories.
- File ownership associated with requesting processes.
- Three-character permission masks (rwx).
- CHMOD operations for updating permissions.
- File metadata and storage usage tracking.
- UTF-8 byte-based file size calculations.
- Improved command error handling and event logging.

The File Manager is integrated with the Simulation Engine,
allowing file requests to be executed at specified CPU-time
offsets and recorded in the shared event history.

File operations currently have zero simulated duration.

The following command aliases remain unsupported:
- mkfile (use CREATE)
- cat (use READ)
- rm (use DELETE)

These three missing aliases are tracked by expected-failure
tests and do not prevent the supported file operations
from functioning.

See `docs/file_manager_issue63.md` for additional details.

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

Reset clears the simulation clock, event queue, event
history, execution timeline, completed requests, and
process execution states.

The scheduler is reinitialized using the current
configuration. The Memory Manager resets its frame
allocation, page tables, and memory statistics.

The File Manager is preserved during simulation reset.
Existing files, directories, and file metadata remain
available after resetting the simulation.

This allows process execution to restart without
automatically deleting the simulated file system.

A new SimulationEngine instance creates a new default
File Manager unless an existing instance is supplied.

#### Integration testing

Automated integration tests are located in
`tests/test_system_integration.py`.

The integration tests cover process scheduling, memory
references, file operations, blocking and unblocking,
event logging, and reset behavior.

Additional tests verify manager interactions, invalid
inputs, deterministic behavior, and expected results.

The latest verified main-branch GitHub Actions run
(November 9, 2026) completed with 163 passed tests
and 3 expected failures.

Browser-based tests use Playwright and Chromium.
The GitHub Actions workflow installs these dependencies
before running the main test suite.

Run the integration tests:

```bash
python -m pytest -q tests/test_system_integration.py
```

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

#### Parallel Computing Status

The current parallel simulator supports FCFS execution
across 1, 2, and 4 simulated CPU cores.

The implementation provides:
- Core assignment and execution timelines.
- Makespan calculations.
- Speedup and efficiency comparisons.
- Throughput measurements.
- Individual core utilization.
- Deterministic race-condition demonstrations.
- Synchronization and deadlock examples.

The parallel simulator is currently a separate backend
module and is not integrated into the main dashboard.

The synchronization examples demonstrate expected
behavior using deterministic operations rather than
actual multithreaded execution.

Future development will focus on expanding parallel
execution capabilities and integrating performance
comparisons into the dashboard.

### Dashboard Development Status

The dashboard currently includes functional process
scheduling controls, process tables, a CPU Gantt chart,
simulation metrics, memory statistics, physical memory
frames, file system visualization, and file activity logs.

Additional dashboard functionality is under development
in the `dashboard-milestone-3` branch (PR #62).

This development includes:
- Editable memory configuration.
- Per-process memory reference inputs.
- Improved JSON import validation.
- Memory input validation and error handling.
- Additional manager visualizations and exports.
- Expanded browser-based testing.

PR #62 remains an open draft as of November 9, 2026.
These changes are not yet considered merged main-branch
functionality.

The Security, Device, Network, Parallel Computing,
Scenario Builder, Testing Center, and Results pages
still require further implementation.

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

Browser tests that use a hardcoded local server URL 
require the server to be available at that address.

### Known Limitations and Future Work

The project remains under active development.

Current limitations include:

- Optimal page replacement is not yet available on main.
- File command aliases `mkfile`, `cat`, and `rm`
  are not yet supported.
- File operations currently have zero simulated duration.
- The frontend Round Robin default is 2 time units,
  while the backend default is 4.
- The parallel simulator is separate from the main
  simulation engine and dashboard.
- Security, Device, and Network Managers are not yet
  implemented.
- Several dashboard workspaces remain placeholders.
- Full six-manager integration is still pending.

Future milestones will focus on completing these
components, expanding integration testing, improving
visualizations, and validating system-wide behavior.
