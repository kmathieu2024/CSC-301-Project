# Memory trace expected results

Use `test_data/trace.txt` for both policies. Each line is a virtual address
referenced by the same process, in order. Start with empty memory, three frames,
and a page size of one address unit, so each address is also its page number.

Trace: `1, 2, 3, 1, 4`

| Reference | FIFO frames | FIFO result | LRU frames | LRU result |
|---|---|---|---|---|
| 1 | [1, -, -] | Fault | [1, -, -] | Fault |
| 2 | [1, 2, -] | Fault | [1, 2, -] | Fault |
| 3 | [1, 2, 3] | Fault | [1, 2, 3] | Fault |
| 1 | [1, 2, 3] | Hit | [1, 2, 3] | Hit |
| 4 | [4, 2, 3] | Fault; evict 1 | [1, 4, 3] | Fault; evict 2 |

Both policies produce five references, four faults, one hit, one eviction,
and a fault rate of 80%. FIFO evicts page 1 because it was loaded first;
LRU evicts page 2 because it was used least recently. The table shows frames
immediately after each reference, before the process terminates and releases
its frames.

For a configured fault latency of 2 time units, total fault latency is 8 time
units for either policy. For zero latency, it is 0.
