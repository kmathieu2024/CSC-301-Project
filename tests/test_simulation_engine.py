import unittest

from src.core.process import Process
from src.core.simulation import SimulationEngine


class TestSimulationEngine(unittest.TestCase):

    def test_event_order(self):
        # Check that processes arrive in the correct order.

        p1 = Process("P1", 0, 5)
        p2 = Process("P2", 2, 3)
        p3 = Process("P3", 4, 2)

        engine = SimulationEngine([p3, p1, p2])
        results = engine.run()

        arrivals = []

        for event in results["events"]:
            if event["event_type"] == "arrival":
                arrivals.append(event["entity_id"])

        self.assertEqual(arrivals, ["P1", "P2", "P3"])

    def test_simulation_clock(self):
        # Check that simulated time advances correctly.

        p1 = Process("P1", 0, 5)

        engine = SimulationEngine([p1])
        results = engine.run()

        self.assertEqual(results["current_time"], 5)

    def test_same_time_arrivals(self):
        # Check the order of processes arriving together.

        p1 = Process("P2", 0, 2)
        p2 = Process("P1", 0, 3)

        engine = SimulationEngine([p1, p2])
        results = engine.run()

        arrivals = []

        for event in results["events"]:
            if event["event_type"] == "arrival":
                arrivals.append(event["entity_id"])

        self.assertEqual(arrivals, ["P1", "P2"])

    def test_round_robin_order(self):
        # New arrivals should enter before a process is requeued.

        p1 = Process("P1", 0, 6)
        p2 = Process("P2", 2, 2)

        engine = SimulationEngine(
            [p1, p2],
            algorithm="rr",
            quantum=2
        )

        results = engine.run()

        order = []

        for event in results["events"]:
            if event["event_type"] == "dispatch":
                order.append(event["entity_id"])

        self.assertEqual(order, ["P1", "P2", "P1", "P1"])

    def test_completed_process(self):
        # Completed processes should not be requeued.

        p1 = Process("P1", 0, 2)

        engine = SimulationEngine(
            [p1],
            algorithm="rr",
            quantum=2
        )

        results = engine.run()

        for event in results["events"]:
            self.assertNotEqual(event["event_type"], "preemption")

        self.assertEqual(results["processes"][0]["state"], "TERMINATED")

    def test_context_switch(self):
        # Switching between two processes should add overhead.

        p1 = Process("P1", 0, 2)
        p2 = Process("P2", 0, 2)

        engine = SimulationEngine(
            [p1, p2],
            context_switch=1
        )

        results = engine.run()

        self.assertEqual(results["current_time"], 5)

    def test_no_switch_for_same_process(self):
        # Continuing the same process should not add overhead.

        p1 = Process("P1", 0, 5)

        engine = SimulationEngine(
            [p1],
            algorithm="rr",
            quantum=2,
            context_switch=1
        )

        results = engine.run()

        self.assertEqual(results["current_time"], 5)

    def test_no_switch_after_idle(self):
        # A process starting after idle time has no switch cost.

        p1 = Process("P1", 0, 2)
        p2 = Process("P2", 6, 2)

        engine = SimulationEngine(
            [p1, p2],
            context_switch=1
        )

        results = engine.run()

        self.assertEqual(results["current_time"], 8)

    def test_step_advances_simulation(self):
        p1 = Process("P1", 0, 5)
        p2 = Process("P2", 2, 3)

        engine = SimulationEngine([p1, p2])

        result = engine.step()

        self.assertEqual(result["current_time"], 5)
        self.assertEqual(engine.completed, 1)

        result = engine.step()

        self.assertEqual(result["current_time"], 8)
        self.assertEqual(engine.completed, 2)    

    def test_run_after_step(self):
        p1 = Process("P1", 0, 5)
        p2 = Process("P2", 2, 3)

        engine = SimulationEngine([p1, p2])

        engine.step()

        self.assertEqual(engine.completed, 1)

        result = engine.run()

        self.assertEqual(result["current_time"], 8)
        self.assertEqual(engine.completed, 2)
        self.assertEqual(len(result["processes"]), 2)  

    def test_reset(self):
        # Reset should produce the same results again.

        p1 = Process("P1", 0, 3)
        p2 = Process("P2", 1, 2)

        engine = SimulationEngine([p1, p2], seed=10)

        first = engine.run()

        engine.reset()

        second = engine.run()

        self.assertEqual(first, second)
        
    def test_reset_after_step(self):
        p1 = Process("P1", 0, 5)
        p2 = Process("P2", 2, 3)

        engine = SimulationEngine([p1, p2])

        engine.step()

        self.assertEqual(engine.completed, 1)
        self.assertEqual(engine.current_time, 5)

        engine.reset()

        self.assertEqual(engine.completed, 0)
        self.assertEqual(engine.current_time, 0)
        self.assertEqual(len(engine.timeline), 0)
        self.assertEqual(len(engine.events), 0)

        result = engine.run()

        self.assertEqual(result["current_time"], 8)
        self.assertEqual(engine.completed, 2)

    def test_invalid_event(self):
        # Events cannot be scheduled in the past.

        p1 = Process("P1", 0, 2)

        engine = SimulationEngine([p1])

        engine.advance_time(1)

        with self.assertRaises(ValueError):
            engine.schedule_event(
                0,
                "arrival",
                Process("P2", 0, 1)
            )


if __name__ == "__main__":
    unittest.main()
