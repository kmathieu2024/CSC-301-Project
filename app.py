import json
import re

from flask import Flask, request, jsonify, send_from_directory

from src.core.process import Process
from src.core.simulation import SimulationEngine
from src.managers.memory.memory_manager import MemoryManager
from src.managers.security import SecurityManager


app = Flask(__name__)

# Store the current simulation.
engine = None


def build_security_manager(config, process_ids):
    """Build optional RBAC policy from the simulation input document.

    Security remains opt-in for backward compatibility. When enabled, every
    process is registered as a user; users omitted from the supplied policy
    receive no roles and are therefore denied protected operations.
    """
    if config is None:
        return None
    if not isinstance(config, dict):
        raise ValueError("Security settings must be an object")
    enabled = config.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("Security enabled must be boolean")
    if not enabled:
        return None

    roles = config.get("roles", [])
    users = config.get("users", [])
    if not isinstance(roles, list):
        raise ValueError("Security roles must be a list")
    if not isinstance(users, list):
        raise ValueError("Security users must be a list")

    manager = SecurityManager()
    for role in roles:
        if not isinstance(role, dict):
            raise ValueError("Each security role must be an object")
        name = role.get("name")
        permissions = role.get("permissions", [])
        if not isinstance(permissions, list):
            raise ValueError("Role permissions must be a list")
        if any(not isinstance(permission, str) for permission in permissions):
            raise ValueError("Role permissions must contain only strings")
        manager.create_role(name, permissions, actor="configuration")

    configured_users = set()
    for user in users:
        if not isinstance(user, dict):
            raise ValueError("Each security user must be an object")
        username = user.get("username")
        assigned_roles = user.get("roles", [])
        if not isinstance(assigned_roles, list):
            raise ValueError("User roles must be a list")
        if any(not isinstance(role_name, str) for role_name in assigned_roles):
            raise ValueError("User roles must contain only strings")
        manager.add_user(username, assigned_roles, actor="configuration")
        configured_users.add(username)

    for process_id in process_ids:
        if process_id not in configured_users:
            manager.add_user(process_id, actor="configuration")

    return manager


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

        # Validate settings without allocating frames when memory is disabled.
        for key in ("frame_count", "page_size"):
            value = memory_config.get(key, 3 if key == "frame_count" else 4)
            if isinstance(value, int) and value > 9007199254740991:
                raise ValueError(key + " exceeds the supported integer range")
        memory_settings = {
            "frame_count": memory_config.get("frame_count", 3),
            "page_size": memory_config.get("page_size", 4),
            "algorithm": memory_config.get("algorithm", "fifo"),
            "fault_latency": memory_config.get("fault_latency", 0),
        }
        MemoryManager.validate_configuration(**memory_settings)
        if memory_config.get("algorithm", "fifo") not in ("fifo", "lru"):
            raise ValueError("Memory policy must be fifo or lru")
        if memory_config.get("enabled", True):
            memory_manager = MemoryManager(**memory_settings)
        security_manager = build_security_manager(
            data.get("security"), [process.pid for process in processes]
        )
        engine = SimulationEngine(
            processes,
            algorithm=data["algorithm"],
            quantum=config.get("time_quantum", 4),
            aging_interval=config.get("aging_interval", 4),
            context_switch=config.get("context_switch_cost", 0),
            memory_manager=memory_manager,
            security_manager=security_manager
        )

        return jsonify(engine.results())

    except (ValueError, TypeError, KeyError, AttributeError) as error:
        return jsonify({"error": str(error)}), 400


@app.route("/api/memory/compare", methods=["POST"])
def compare_memory():
    """Compare fresh FIFO/LRU managers; fault_rate is a fraction from 0 to 1."""
    try:
        data = json.loads(request.get_data(as_text=True), object_pairs_hook=reject_duplicate_keys)
        if not isinstance(data, dict):
            raise ValueError("Memory comparison input must be an object")
        addresses = data.get("addresses")
        if not isinstance(addresses, list) or not addresses:
            raise ValueError("addresses must be a nonempty list of nonnegative integers")
        for address in addresses:
            if isinstance(address, bool) or not isinstance(address, int) or address < 0:
                raise ValueError("Each address must be a nonnegative integer")

        frame_count = data.get("frame_count")
        page_size = data.get("page_size")
        MemoryManager.validate_configuration(frame_count, page_size)

        # Local managers and a synthetic process keep this independent of the engine.
        process = Process("memory-comparison", 0, len(addresses))
        results = {}
        for policy in ("fifo", "lru"):
            manager = MemoryManager(frame_count, page_size, algorithm=policy)
            for address in addresses:
                manager.reference(process, address)
            metrics = manager.metrics()
            results[policy] = {
                key: metrics[key]
                for key in ("references", "hits", "faults", "evictions", "fault_rate")
            }
        return jsonify(results)
    except (ValueError, TypeError) as error:
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
