# Milestone 4 independent manager testing (#50)

Baseline: `95e82faddf535d63d08f15e693d050c257558f56`.
Executed November 20, 2026 on Windows, Python 3.15.2 and pytest 9.2.0.
The venv reused installed Flask and Playwright packages.

## Baseline results

| Suite | Passed | Failed | Skipped | XFAIL | XPASS |
| --- | ---: | ---: | ---: | ---: | ---: |
| Process | 17 | 0 | 0 | 0 | 0 |
| Memory | 10 | 0 | 0 | 0 | 0 |
| File | 41 | 0 | 0 | 3 | 0 |
| Security | 11 | 0 | 0 | 0 | 0 |
| Device | 156 | 0 | 0 | 0 | 0 |
| Network | 378 | 0 | 0 | 0 | 0 |
| Separate integration suites | 59 | 0 | 0 | 0 | 0 |

Device also reported 154 passing subtests. Run the following from the repository root with the environment's Python.

```sh
# Process
python -m pytest -v -rA tests/test_fcfs_baseline.py tests/test_fcfs_pid_tie.py tests/test_sjf_baseline.py tests/test_sjf_pid_tie.py tests/test_srtf_baseline.py tests/test_srtf_ties.py tests/test_rr_baseline.py tests/test_rr_pid_arrivals.py tests/test_priority_baseline.py tests/test_priority_pid_tie.py tests/test_priority_aging.py tests/test_priority_minimum.py tests/test_blocking.py

# Memory
python -m pytest -v -rA tests/test_memory_integration.py

# File
python -m pytest -v -rA tests/test_file_manager.py

# Security
python -m pytest -v -rA tests/test_security_manager.py

# Device
python -m pytest -v -rA tests/test_device_manager.py tests/test_device_models.py tests/test_device_scheduler.py tests/test_device_validation.py

# Network
python -m pytest -v -rA tests/test_network_manager.py tests/test_network_models.py tests/test_network_routing.py tests/test_network_scheduler.py tests/test_network_validation.py

# Integration
python -m pytest -v -rA tests/test_security_integration.py tests/test_system_integration.py tests/test_network_topology.py
```

## Coverage and observed results

A pass means pytest verified the listed assertion. Numeric results below are the values checked by the assertions.

| Manager and case | Test | Setup and expected assertion | Actual and verdict |
| --- | --- | --- | --- |
| Process normal | `test_{fcfs,sjf,srtf,rr,priority}_baseline` | Run the baseline process workload through the engine and compare the scheduling timeline and metrics. | Expected policy-specific results matched. Pass. |
| Process boundary | `test_fcfs_equal_arrivals_use_input_order`, `test_srtf_equal_remaining_time_does_not_preempt`, `test_rr_simultaneous_arrivals_use_pid` | Compare FCFS input ordering, SRTF equal remaining-time behavior, and RR simultaneous arrivals. | All three engine assertions passed. |
| Process error | `test_priority_defaults_to_one_and_rejects_zero` | Validate a process with priority zero; expect a validation error. | Direct validation raised the expected error. Pass. |
| Process block and resume | `test_blocked_process_yields_cpu_and_returns_to_ready`, `test_cpu_idles_until_blocked_process_is_ready` | Block a running process; check that another process can run and that an empty READY queue yields idle time. | Engine timelines matched. Pass. |
| Memory normal | `test_fifo_and_lru_same_trace` | Feed the same locality trace to both policies and check frame state, victim, fault latency and totals. | Direct checks passed. FIFO had 8 faults and 5 evictions; LRU had 7 faults and 4 evictions. |
| Memory boundary | `test_address_translation_at_page_boundaries`, `test_one_frame_and_repeated_page_boundaries` | Check page/offset/frame math and repeated references followed by replacement with one frame. | Direct assertions passed, including 9 hits on the repeated-page portion and 2 evictions in the distinct-page case. |
| Memory error and failure | `test_invalid_memory_inputs_do_not_change_valid_state`, `test_protected_frame_wait_does_not_count_a_fault` | Invalid input leaves mappings and counters intact; an all-protected frame raises `NoFrameAvailable` without charging another fault. | Direct exceptions and state checks passed. |
| Memory block and reset | `test_page_fault_blocks_only_requesting_process_and_resets`, `test_one_frame_protects_faulted_page_until_process_can_use_it` | Use a nonzero fault penalty and a contended single frame; check waiting, resumption and reset replay. | Engine checks passed, including a 3-unit wait in the first case. |
| File normal | `test_basic_file_commands`, `test_append_preserves_existing_file_content` | Create a file, read back content, append and check existing content survives. | Direct content checks passed. |
| File boundary and error | `test_root_directory_cannot_be_removed`, `test_errors_for_existing_missing_and_wrong_type` | Attempt root deletion and operations on duplicate, missing and wrong-type paths. | Direct guards rejected the operations as expected. Pass. |
| File failure | `test_broken_authorizer_does_not_allow_a_write`, `test_other_denied_file_actions_leave_state_alone` | Fail authorization and check protected file state after denied operations. | Direct checks passed. |
| File legacy aliases | `test_required_mkfile_command`, `test_required_cat_command`, `test_required_rm_command` | Exercise the three command aliases against their historical assertions. | All three strict expectations failed as marked. The README lists them as unsupported and names CREATE, READ, DELETE as equivalents. XFAIL. |
| Security normal and boundary | `test_users_roles_and_permissions_are_managed`, `test_path_scoped_permissions_and_default_deny`, `test_disabled_user_is_denied_until_reenabled` | Grant permissions, check scopes and default denials, then disable and reenable a user. | Direct authorization assertions passed. |
| Security error and history | `test_role_and_user_configuration_is_validated`, `test_audit_log_is_append_only_and_returns_defensive_copies` | Invalid configuration raises; later operations append records and caller mutations preserve prior records. | Direct exception and audit assertions passed. |
| Security protected state and alert | `test_denied_file_request_creates_security_alert_and_file_error`, `test_allowed_and_denied_process_file_writes` | Deny a File request and check warning/error; compare File contents after allowed and denied writes. | Integration checks passed. The denied write left an empty file; the allowed write produced `Hello`. |
| Device normal | `test_completed_reference_execution`, `test_peripheral_serialization_and_wait_metrics` | Submit cylinders 60, 20, 70 from head 50; compare service order, timeline and metrics. Queue printer and keyboard workloads. | Disk movement was 100/70/128 for FCFS/SSTF/SCAN, with three completions for each. Peripheral order and wait assertions passed. |
| Device boundary and error | `test_scan_endpoint_collision_and_same_cylinder_order`, `test_invalid_callbacks_are_atomic` | Resolve same-cylinder requests at the SCAN endpoint; reject invalid callbacks without mutating state. | Direct order and atomicity checks passed. |
| Device failure | `test_failure_aborts_work_preserves_head_and_recovers` | Fail operations during seek/service and recover the device. | Direct abort, head and recovery assertions passed. |
| Network normal | `test_hand_calculated_three_packet_two_hop_fixture`, `test_nonpreemptive_queue_contention` | Run a two-hop fixture and queue mixed priorities behind an active packet. | Deliveries matched times 4/5/6 and 300 delivered bytes. FIFO and Priority ordering assertions passed. |
| Network boundary and error | `test_one_hop_exact_boundaries`, `test_waiting_only_capacity_and_drop_tail`, `test_duplicate_ids_unknown_endpoints_and_wrong_models` | Check serialization plus propagation, a full waiting buffer, and invalid topology definitions. | Delay and capacity assertions passed; overflow dropped the arrival, and invalid topology raised as expected. |
| Network failure | `test_link_failure_in_each_phase_and_recovery`, `test_node_failure_preserves_independent_devices_and_recovery`, `test_failure_without_alternate_route_drops_waiters_once` | Fail links/nodes, recover them and exercise absence of an alternate path. | Direct terminal-state and recovery checks passed, including a single drop notification when no alternate route exists. |

API input, output and browser rendering tests cover their own layers. Engine-based assertions above establish timing and interactions with other components.

## Six-manager integration rerun

From the testing checkout, submit the JSON fixture to `POST /api/start` using Flask's test client, then call `POST /api/run` on the same client. Compare the response with [the expected results](../test_data/expected_results/six_manager_integration_expected.md). The observed HTTP status was 200 for both calls, final process completion was 6.0, process blocked time was 5.0, and the shared event set was Process, Memory, File, Security, Device and Network. Security returned allowed for File, Device and Network. The dashboard workflow is documented in the #51 changes.

## Added Scheduler assertions

`python -m pytest -v -rA tests/test_process_scheduler.py` passed all 8 cases. The standalone cases check policy selection at an equal arrival, RR requeueing at a quantum boundary, and invalid algorithm, quantum and aging settings.

Chromium also opened all six workspaces, validated the fixture import, ran
the simulation to completion, and downloaded the JSON results. The downloaded
results matched all of the API checks, including `current_time == 6.0`.

## Deferred Device limitation

The documented DM-A07 behavior reproduced on the baseline. In the failure
setup from `test_failure_and_cancellation_notifications_wake_process`, call
`engine.results()` immediately after failing the device at time 2 instead of
first delivering notifications via `check_arrivals()`. The result read moved
the process from WAITING to READY, increased blocked time from 0 to 2, and
reported a FAILED device request. A caller expecting passive polling can
therefore advance engine state. Device and integration owners should confirm
the disposition, as recorded under #51.

## Security integration assertions

The denial test now checks that a denied CREATE preserves the directory
listing and the contents of a preexisting file. A read/list-only inspector
verifies state independently of the denied process. The reset/replay test
checks that the original audit entries are an exact prefix after replay.
The Security manager and integration suites passed all 30 cases after these
changes. The standalone Process suites, including the new Scheduler tests,
passed all 25 cases.

## File workload offset validation

The regression exposed the known API XFAIL in
`test_equivalent_request_offsets_are_rejected`. A focused rerun confirmed
that equivalent File offsets are accepted and the later request overwrites
the earlier one. The matching Memory offset check passed. This belongs to
workload validation and is recorded in the #51 defect list.

## Full regression and focused dashboard rerun

The final baseline-worktree regression reported 1,508 passed, four expected
failures, and 154 passing subtests. One Chromium test,
`test_memory_log_preserves_same_timestamp_order`, failed while waiting for
the dashboard to show Completed within five seconds. The status was still
at simulation time 3. The earlier full run passed this test.

On November 21, the same Chromium case passed in a focused run alongside
the standalone Scheduler and Security suites (39 passed in 3.68 seconds).
The result is intermittent across the recorded runs. The cause of the
slow completion remains unverified.

```sh
python -m pytest -p no:cacheprovider -q tests/test_manager_exports.py::test_memory_log_preserves_same_timestamp_order tests/test_process_scheduler.py tests/test_security_manager.py tests/test_security_integration.py
```
