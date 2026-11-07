import json
import re

from flask import Flask, request, jsonify, send_from_directory

from src.core.process import Process
from src.core.simulation import SimulationEngine
from src.managers.memory.memory_manager import MemoryManager


app = Flask(__name__)

# Store the current simulation.
engine = None


@app.route("/")
def dashboard():
    return send_from_directory("dashboard", "index.html")


@app.route("/<path:filename>")
def dashboard_files(filename):
    return send_from_directory("dashboard", filename)


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def validate_memory_references(references, burst):
    if not isinstance(references, dict):
        raise ValueError("memory_references must be an offset-to-address object")
    validated = {}
    for key, address in references.items():
        if not re.fullmatch(r"0|[1-9][0-9]*", key):
            raise ValueError("Memory offset must be a canonical nonnegative integer")
        offset = int(key)
        if (offset > 9007199254740991 or not 0 <= offset < burst
                or isinstance(address, bool) or not isinstance(address, int)
                or not 0 <= address <= 9007199254740991):
            raise ValueError("Invalid memory offset or virtual address")
        validated[offset] = address
    return validated


@app.route("/api/start", methods=["POST"])
def start():
    global engine

    try:
        data = json.loads(request.get_data(as_text=True), object_pairs_hook=reject_duplicate_keys)
        if not isinstance(data, dict):
            raise ValueError("Simulation input must be an object")
        if "memory_references" in data:
            raise ValueError("Memory references must belong to an existing process")

        # Get the processes from the dashboard.
        processes = []

        for item in data["processes"]:
            process = Process(
                item["pid"],
                item["arrival_time"],
                item["burst_time"],
                item.get("priority", 1),
                memory_references=validate_memory_references(
                    item.get("memory_references", {}), item["burst_time"]
                ),
                file_requests={
                    int(offset): command
                    for offset, command in item.get("file_requests", {}).items()
                }
            )

            processes.append(process)

        # Get the scheduling settings.
        config = data["configuration"]
        memory_config = data.get("memory", {})
        if not isinstance(memory_config, dict):
            raise ValueError("Memory settings must be an object")
        if not isinstance(memory_config.get("enabled", True), bool):
            raise ValueError("Memory enabled must be boolean")

        memory_manager = None

        # Validate settings even when disabled; only attach the manager if enabled.
        for key in ("frame_count", "page_size"):
            value = memory_config.get(key, 3 if key == "frame_count" else 4)
            if isinstance(value, int) and value > 9007199254740991:
                raise ValueError(key + " exceeds the supported integer range")
        configured_memory = MemoryManager(
            frame_count=memory_config.get("frame_count", 3),
            page_size=memory_config.get("page_size", 4),
            algorithm=memory_config.get("algorithm", "fifo"),
            fault_latency=memory_config.get("fault_latency", 0)
        )
        if memory_config.get("algorithm", "fifo") not in ("fifo", "lru"):
            raise ValueError("Memory policy must be fifo or lru")
        if memory_config.get("enabled", True):
            memory_manager = configured_memory
        engine = SimulationEngine(
            processes,
            algorithm=data["algorithm"],
            quantum=config.get("time_quantum", 4),
            aging_interval=config.get("aging_interval", 4),
            context_switch=config.get("context_switch_cost", 0),
            memory_manager=memory_manager
        )

        return jsonify(engine.results())

    except (ValueError, TypeError, KeyError, AttributeError) as error:
        return jsonify({"error": str(error)}), 400


@app.route("/api/step", methods=["POST"])
def step():
    if engine is None:
        return jsonify({"error": "Start the simulation first"}), 400

    return jsonify(engine.step())


@app.route("/api/run", methods=["POST"])
def run():
    if engine is None:
        return jsonify({"error": "Start the simulation first"}), 400

    return jsonify(engine.run())


@app.route("/api/reset", methods=["POST"])
def reset():
    if engine is None:
        return jsonify({"error": "Start the simulation first"}), 400

    engine.reset()

    return jsonify(engine.results())


if __name__ == "__main__":
    app.run(debug=True)
