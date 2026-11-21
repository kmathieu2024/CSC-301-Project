# Final-month experiment plan (#51)

These experiments are planned. All times are simulated. For each case,
record the commit SHA, workload ID and input JSON or trace, policy, configured
parameters, seed if applicable, metrics with units, and the raw exported
results. Keep one CSV row per case and policy plus the corresponding event or
timeline export. Run each deterministic configuration twice from reset and
compare results before aggregating.

| Question | Workload and policy comparison | Variables held constant | Metrics and recording |
| --- | --- | --- | --- |
| How does disk policy change head movement and request waits? | Start with a head at 50 on cylinders 0–99, direction UP, and simultaneous requests at 60, 20 and 70. Add a second case with staggered arrivals and clusters on both sides of the head. Compare FCFS, SSTF and SCAN. | Keep cylinder limits, head, direction, arrival schedule, service duration, seek cost, and request order fixed per case. | Record service order, total movement in cylinders, mean and maximum queue wait, busy time, and completion time. Compare timeline seek and endpoint intervals with reported metrics. |
| How does queue policy trade high-priority latency against low-priority delay and drops? | On one link, start a packet, then queue interleaved high- and low-priority packets behind it. Repeat at two fixed-load levels, including arrivals above waiting capacity. Compare FIFO and nonpreemptive Priority within each load level. | Hold topology, link bandwidth, propagation delay, buffer capacity, packet sizes, arrivals, and priorities fixed per case. | Record completion order, latency and max wait by priority, delivered and overflow counts, throughput and goodput. Reconcile terminal packets with counts and confirm the active packet completes without preemption. |
| How does reference locality affect replacement? | Run the checked-in locality and stress traces under FIFO and LRU at 1, 3 and 5 frames. Compare within each trace/frame pair. | Use the same address order, page size, initial empty state, and fault latency within each pair. | Record hits, faults, fault rate, evictions and cumulative modeled fault latency. Compare fault counts and rate by trace and frame count. |

Use the checked-in expected values for the starting disk fixture and memory
locality trace to sanity-check the collection process. The expected values
are available in [device results](../test_data/expected_results/device_expected.md)
and [memory results](../test_data/expected_results/memory_expected.md).
