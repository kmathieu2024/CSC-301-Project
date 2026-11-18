# Network failure and rerouting scenario

Input: `test_data/milestone4/network_failure.json`.
Submit it to the existing `/api/start` endpoint, followed by `/api/run`.
Step and reset/replay use the same workload. In the dashboard, open Network
Manager and select **Load network failure demonstration**, then use Process
Manager **Run** or **Step**. The same JSON can be loaded through the existing
workload import control. Network results show the paths, timelines, and metrics
below; Reset followed by Run reproduces them.

The fixture has no Security policy and leaves the Security editor unchanged.
The expected results below assume Security is disabled or permits both sends.
For combined workloads with a differing enabled Security policy, apply that
policy through the Security editor before importing; automatic policy import is
not supported. Rejected combined imports preserve accepted inputs and results.

Two processes submit 100-byte packets at CPU offset zero. Each has one CPU time
unit. Links are directed, carry 100 bytes per simulated time unit, and have zero
propagation delay. AB costs 1; alternate AC and CB each cost 2. Thus both packets
initially choose AB. Link AB fails at 0.5 and recovers at 3. Recovery does not retry
failed packets.

| Packet/process | Result | Physical activity | Blocked time | CPU completion |
|---|---|---|---:|---:|
| packet-0 / A | FAILED, LINK_FAILURE at 0.5 | AB transmits 0–0.5, then aborts | 0.5 | 1.5 |
| packet-1 / B | DELIVERED at 2.5 | Waits 0–0.5; AC transmits 0.5–1.5; CB transmits 1.5–2.5 | 2.5 | 3.5 |

The CPU initially idles 0–0.5, then A runs 0.5–1.5. It idles again
1.5–2.5, then B runs 2.5–3.5.
The recovery at time 3 occurs during B's CPU execution and consumes no CPU time.
Transmission and propagation completion boundaries remain separate even when
propagation duration is zero. Invalidated AB callbacks cannot complete A again.

Expected final simulation time: **3.5**. CPU busy time: **2**.

| Metric | Expected |
|---|---:|
| Submitted / delivered / failed | 2 / 1 / 1 |
| Dropped / cancelled | 0 / 0 |
| Terminal notifications | 2 |
| Delivered payload | 100 bytes |
| Delivered throughput | 1 / 3.5 packets per time unit |
| Payload goodput | 100 / 3.5 bytes per time unit |
| B queue wait / transmission / propagation | 0.5 / 2 / 0 time units |
| B committed path | AC, CB |

| Link | Busy | Idle | Unavailable | Wire bytes | Fully serialized bytes |
|---|---:|---:|---:|---:|---:|
| ab | 0.5 | 0.5 | 2.5 | 50 | 0 |
| ac | 1 | 2.5 | 0 | 100 | 100 |
| cb | 1 | 2.5 | 0 | 100 | 100 |

Each link's time totals sum to 3.5. Utilization is busy / 3.5, expressed as a
fraction. AB's queue integral is 1 waiting packet × 0.5 time units = 0.5;
its average queue length is 0.5 / 3.5. Alternate queues never accumulate waiters.
Wire traffic includes aborted serialization and counts each hop; delivered
payload counts B once, so 250 wire bytes do not imply 250 delivered bytes.

All configured controls are processed by a full Run, including controls after
process completion. This fixture's last control is before its final CPU completion.
At identical timestamps the engine compares owner PID or topology target ID,
then insertion ID; there is no universal failure-before-completion priority.

Regression evidence: `tests/test_network_api.py`,
`test_deterministic_failure_demo_reroutes_and_matches_hand_calculation`.
