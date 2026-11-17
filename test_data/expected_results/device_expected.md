# Device demonstration expected results

Input: `test_data/milestone4/devices.json`. CPU FCFS, context-switch cost 0,
memory disabled. All three processes arrive at time 0 and execute four CPU units.
There are 12 synchronous requests: 5 disk, 4 printer, and 3 keyboard operations.

Verified through Flask start/run and independently checked from interval arithmetic:

| Device | Completed | Busy | Idle | Head movement | Queue area |
|---|---:|---:|---:|---:|---:|
| disk0 | 5 | 316 | 3 | 306 | 101 |
| printer0 | 4 | 12 | 307 | 0 | 0 |
| keyboard0 | 3 | 3 | 316 | 0 | 0 |

All requests succeed. Final simulation time is 319. Process completion/blocked
times are P1: 319/315, P2: 47/43, P3: 12/8. CPU execution totals 12 units.

Disk path: 50 → 60 → 82 → endpoint 99 → 20 → endpoint 0 → 70 → endpoint 99 → 40.
Distances: 10 + 22 + 17 + 79 + 20 + 70 + 29 + 59 = 306.
Endpoint time is 17 + 20 + 29 = 66; request seek time is 240; service is 5 × 2 = 10.
Busy time is 306 + 10 = 316. Idle intervals are 134–135, 227–228, and 318–319.

The first disk request activates at 0, begins service at 10, and completes at 12.
The second disk request activates at 12 and completes at 36, waiting 12 units.
P1's next request is submitted at 13, activates after endpoint travel at 53,
begins service at 132, and completes at 134: queue wait 40, seek 79, service 2,
turnaround 121. Remaining disk waits are 20 and 29. Queue area is
12 + 40 + 20 + 29 = 101; average queue wait is 101 / 5 = 20.2.

Printer service intervals are 0–3, 8–11, 37–40, and 41–44.
Keyboard service intervals are 4–5, 6–7, and 45–46.
Disk and printer service overlap at the beginning. Printer/keyboard requests
have no queue wait. Total I/O turnaround equals each process's blocked time.

Per-device utilization is busy / 319 × 100 percent; throughput is completed / 319.
Average queue length for disk0 is 101 / 319. Aggregate completed throughput is
12 / 319; mean utilization is (316 + 12 + 3) / (3 × 319) × 100 percent.
