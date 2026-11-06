from flask import Flask, request, jsonify, send_from_directory

from src.core.process import Process
from src.core.simulation import SimulationEngine


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
                item.get("priority", 1)
            )

            processes.append(process)

        # Get the scheduling settings.
        config = data["configuration"]

        engine = SimulationEngine(
            processes,
            algorithm=data["algorithm"],
            quantum=config.get("time_quantum", 4),
            aging_interval=config.get("aging_interval", 4),
            context_switch=config.get("context_switch_cost", 0)
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
