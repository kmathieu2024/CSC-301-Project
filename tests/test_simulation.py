from src.core.process import Process
from src.core.simulation import SimulationEngine

# Scheduling algorithms to test
algorithms = ["fcfs", "sjf", "srtf", "rr", "priority"]

for algorithm in algorithms:

    # Create three sample processes
    p1 = Process("P1", 0, 5, priority=3)
    p2 = Process("P2", 0, 3, priority=1)
    p3 = Process("P3", 0, 2, priority=2)

    # Create the simulation engine
    engine = SimulationEngine([p1, p2, p3], algorithm=algorithm)

    # Run the simulation
    results = engine.run()

    # Display the results
    print("\nTesting:", algorithm.upper())
    print("------------------")

    for process in results["processes"]:
        print(process)

    print("\nCPU Utilization:", results["cpu_utilization"], "%")
    print("Average Waiting Time:", results["average_waiting_time"])
