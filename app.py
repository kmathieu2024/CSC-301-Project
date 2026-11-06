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


@app.route("/api/start", methods=["POST"])
def start():
    global engine

    try:
        data = request.get_json()

        # Get the processes from the dashboard.
        processes = []

        for item in data["processes"]:
            process = Process(
                item["pid"],
                item["arrival_time"],
                item["burst_time"],
                item.get("priority", 1),
                memory_references={
                    int(offset): address
                    for offset, address in item.get("memory_references", {}).items()
                },
                file_requests={
                    int(offset): command
                    for offset, command in item.get("file_requests", {}).items()
                }
            )

            processes.append(process)

        # Get the scheduling settings.
        config = data["configuration"]
        memory_config = data.get("memory")

        memory_manager = None

        if memory_config and memory_config.get("enabled", True):
            memory_manager = MemoryManager(
                frame_count=memory_config.get("frame_count", 3),
                page_size=memory_config.get("page_size", 4),
                algorithm=memory_config.get("algorithm", "fifo"),
                fault_latency=memory_config.get("fault_latency", 0)
            )
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
