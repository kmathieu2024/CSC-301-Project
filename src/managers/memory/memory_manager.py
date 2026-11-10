from dataclasses import dataclass


class NoFrameAvailable(Exception):
    """All frames are temporarily protected."""


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

    @staticmethod
    def validate_configuration(frame_count, page_size=1, algorithm="fifo", fault_latency=0):
        """Check settings without allocating frames or changing manager state."""
        for name, value in (("frame_count", frame_count), ("page_size", page_size)):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if not isinstance(algorithm, str) or algorithm.lower() not in ("fifo", "lru"):
            raise ValueError("algorithm must be fifo or lru")
        if isinstance(fault_latency, bool) or not isinstance(fault_latency, (int, float)) or not 0 <= fault_latency < float('inf'):
            raise ValueError("fault_latency must be a finite nonnegative number")

    def __init__(self, frame_count, page_size=1, algorithm="fifo", fault_latency=0):
        self.validate_configuration(frame_count, page_size, algorithm, fault_latency)
        self.frame_count, self.page_size = frame_count, page_size
        self.algorithm, self.fault_latency = algorithm.lower(), fault_latency
        self.reset()

    def reset(self):
        self.frames = [None] * self.frame_count
        self.page_tables = {}
        self._loaded = {}
        self._used = {}
        self._protected = set()
        self._clock = 0
        self.hits = self.faults = self.evictions = 0

    def reference(self, process, address):
        if isinstance(address, bool) or not isinstance(address, int) or address < 0:
            raise ValueError("address must be a nonnegative integer")
        pid = process.pid
        page, offset = divmod(address, self.page_size)
        key = (pid, page)
        table = self.page_tables.setdefault(pid, {})
        if page not in table and None not in self.frames and all(
                frame in self._protected for frame in self.frames):
            raise NoFrameAvailable()
        self._clock += 1
        evicted = None
        fault = page not in table
        if fault:
            self.faults += 1
            if None in self.frames:
                frame = self.frames.index(None)
            else:
                order = self._loaded if self.algorithm == "fifo" else self._used
                evicted = min((key for key in order if key not in self._protected), key=order.get)
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

    def protect(self, process, page):
        key = (process.pid, page)
        if self.page_tables.get(process.pid, {}).get(page) is None:
            raise ValueError("Cannot protect a page that is not resident")
        self._protected.add(key)

    def unprotect(self, process, page):
        self._protected.remove((process.pid, page))

    def release(self, process):
        for page, frame in self.page_tables.pop(process.pid, {}).items():
            key = (process.pid, page)
            self._protected.discard(key)
            self.frames[frame] = None
            del self._loaded[key]
            del self._used[key]

    def metrics(self):
        references = self.hits + self.faults
        return {"references": references, "hits": self.hits, "faults": self.faults,
                "evictions": self.evictions, "fault_rate": self.faults / references if references else 0,
                "fault_latency": self.faults * self.fault_latency,
                "used_frames": sum(frame is not None for frame in self.frames)}
