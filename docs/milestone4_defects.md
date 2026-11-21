# Milestone 4 unresolved defects and verification status (#51)

Reviewed baseline: `95e82faddf535d63d08f15e693d050c257558f56`.
Issue #50 exercised Process scheduling, Memory replacement and fault handling,
File operations and permissions, Security authorization and audit history,
Device scheduling and recovery, and Network routing, queuing and recovery.
The six-manager fixture also passed its timing and authorization checks.

| Item | Component and owner | Evidence, impact and status |
| --- | --- | --- |
| M3-F01 | File, Quintin Ampofo | Run `python -m pytest -v -rx tests/test_file_manager.py -k "required_mkfile or required_cat or required_rm"`. All three strict historical expectations returned XFAIL. Current documented commands are CREATE, READ and DELETE. Scripts relying on the aliases require updates. Keep the historical results visible and confirm with the team if a rubric or compatibility requirement still calls for alias support. |
| DM-A07 | Device and Simulation Engine, Danae Ludy and Kyla Jones-Atkinson (owner confirmation pending) | README documents a deferred limitation. A healthy-run results read can deliver queued device notifications, wake a process and schedule work. Read-only polling consumers can observe state changes. The limitation remains documented on the baseline. Reproduced on the reviewed SHA. Disposition and ownership require team confirmation. |
| Equivalent File request offsets | Flask workload validation and File, owner confirmation pending from Kyla Jones-Atkinson and Quintin Ampofo | Reproduced as a strict XFAIL on the baseline and in a focused retest. A request can be silently lost when imported offsets normalize to the same integer. |
| Intermittent Memory export test timeout | Dashboard and Chromium test, owner confirmation pending | One full regression timed out at simulation time 3 while waiting five seconds for Completed in `test_memory_log_preserves_same_timestamp_order`. An earlier full run and a focused rerun passed. The cause remains unverified. The timed-out assertion checked completion before comparing export event order. |

## Reproduce the File request offset defect

Run `python -m pytest -v -rx tests/test_api_inputs.py -k equivalent_request_offsets`.
For File requests, the fixture sends two different commands at offsets
`"0"` and `"00"` to `POST /api/start`. Expected HTTP 400 and no new engine. The API returned HTTP 200
and retained only the second request at offset zero. The matching Memory case rejected equivalent offsets.

## Reproduce DM-A07

Use the failure setup in `tests/test_device_integration.py::test_failure_and_cancellation_notifications_wake_process`.
Create a burst-2 process with its first request targeting a device with a
10-unit service time. Step twice, advance to time 2, and fail the device at
that timestamp. Inspect the process, then call `engine.results()` without
calling `check_arrivals()` first.

A passive read would preserve WAITING and zero charged blocked time until
explicit notification delivery. The observed read changed the process to READY,
charged 2 units of blocked time, and returned a FAILED device outcome at time 2.
This matches the deferred behavior documented in the README.

The defect list draws on #50's baseline and regression evidence. Both
change sets are independently based on the same revision. Read #50 first
for the detailed assertion evidence. The documentation can merge independently.
