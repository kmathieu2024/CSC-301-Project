from dataclasses import dataclass


@dataclass(frozen=True)
class Translation:
    pid: str
    address: int
    page: int
    offset: int
    frame: int
    physical_address: int
    fault: bool
    evicted: tuple | None = None
    latency: float = 0


class MemoryManager:
    """Global demand-paged memory with FIFO or LRU replacement."""

    def __init__(self, frame_count, page_size=1, algorithm="fifo", fault_latency=0):
        for name, value in (("frame_count", frame_count), ("page_size", page_size)):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if not isinstance(algorithm, str) or algorithm.lower() not in ("fifo", "lru"):
            raise ValueError("algorithm must be fifo or lru")
        if isinstance(fault_latency, bool) or not isinstance(fault_latency, (int, float)) or not 0 <= fault_latency < float('inf'):
            raise ValueError("fault_latency must be a finite nonnegative number")
        self.frame_count, self.page_size = frame_count, page_size
        self.algorithm, self.fault_latency = algorithm.lower(), fault_latency
        self.reset()

    def reset(self):
        self.frames = [None] * self.frame_count
        self.page_tables = {}
        self._loaded = {}
        self._used = {}
        self._clock = 0
        self.hits = self.faults = self.evictions = 0

    def reference(self, process, address):
        if isinstance(address, bool) or not isinstance(address, int) or address < 0:
            raise ValueError("address must be a nonnegative integer")
        pid = process.pid
        page, offset = divmod(address, self.page_size)
        key = (pid, page)
        table = self.page_tables.setdefault(pid, {})
        self._clock += 1
        evicted = None
        fault = page not in table
        if fault:
            self.faults += 1
            if None in self.frames:
                frame = self.frames.index(None)
            else:
                order = self._loaded if self.algorithm == "fifo" else self._used
                evicted = min(order, key=order.get)
                frame = self.page_tables[evicted[0]].pop(evicted[1])
                del self._loaded[evicted]
                del self._used[evicted]
                self.evictions += 1
            self.frames[frame] = key
            table[page] = frame
            self._loaded[key] = self._clock
        else:
            self.hits += 1
            frame = table[page]
        self._used[key] = self._clock
        return Translation(pid, address, page, offset, frame, frame * self.page_size + offset,
                           fault, evicted, self.fault_latency if fault else 0)

    def release(self, process):
        for page, frame in self.page_tables.pop(process.pid, {}).items():
            key = (process.pid, page)
            self.frames[frame] = None
            del self._loaded[key]
            del self._used[key]

    def metrics(self):
        references = self.hits + self.faults
        return {"references": references, "hits": self.hits, "faults": self.faults,
                "evictions": self.evictions, "fault_rate": self.faults / references if references else 0,
                "fault_latency": self.faults * self.fault_latency,
                "used_frames": sum(frame is not None for frame in self.frames)}
