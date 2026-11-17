"""Device model contracts; runnable with unittest or pytest."""

import unittest
from dataclasses import FrozenInstanceError, replace

from src.managers.device import (
    DeviceConfig, DeviceOperation, DeviceRequest, DeviceRequestSpec, DeviceState,
    DeviceType, DiskGeometry, OperationKind, RequestState, ScanDirection,
    SchedulingPolicy,
    create_device_runtime, validate_runtime_state,
)


def disk_config(**changes: object) -> DeviceConfig:
    """Build a small deterministic SCAN disk fixture."""
    config = DeviceConfig(
        device_id="disk0", device_type=DeviceType.DISK,
        policy=SchedulingPolicy.SCAN, default_service_time=2,
        disk=DiskGeometry(
            min_cylinder=0, max_cylinder=99, initial_head=50,
            initial_direction=ScanDirection.UP, seek_time_per_cylinder=0.5,
        ),
    )
    return replace(config, **changes)


class TestDeviceModels(unittest.TestCase):
    """Immutable inputs and independent runtime state."""

    def test_explicit_device_types(self) -> None:
        self.assertEqual(
            {kind.value for kind in DeviceType}, {"DISK", "PRINTER", "KEYBOARD"}
        )

    def test_configs_geometry_and_specs_are_frozen(self) -> None:
        config = disk_config()
        spec = DeviceRequestSpec(device_id="disk0", operation="READ", cylinder=60)
        for model, attribute, value in (
            (config, "device_id", "other"),
            (config.disk, "initial_head", 0),
            (spec, "operation", "WRITE"),
        ):
            with self.subTest(model=type(model).__name__):
                with self.assertRaises(FrozenInstanceError):
                    setattr(model, attribute, value)

    def test_models_require_keyword_arguments(self) -> None:
        with self.assertRaises(TypeError):
            DeviceRequestSpec("disk0", "READ")

    def test_committed_operation_is_immutable(self) -> None:
        operation = DeviceOperation(
            token="o0", generation=0, kind=OperationKind.SERVICE,
            request_id="r0", started_at=0, due_at=2,
        )
        with self.assertRaises(FrozenInstanceError):
            operation.due_at = 3

    def test_factory_initializes_disk_and_keeps_config(self) -> None:
        config = disk_config()
        runtime = create_device_runtime(config)
        self.assertIs(runtime.config, config)
        self.assertEqual(runtime.state, DeviceState.IDLE)
        self.assertEqual(runtime.head_position, 50)
        self.assertEqual(runtime.direction, ScanDirection.UP)
        self.assertEqual(runtime.generation, 0)
        self.assertEqual(runtime.queue, [])
        self.assertIsNone(runtime.active_request_id)
        self.assertIsNone(runtime.active_operation)
        self.assertIsNone(runtime.failure_reason)
        validate_runtime_state(runtime, {})

    def test_factory_initializes_non_disk_devices(self) -> None:
        for kind in (DeviceType.PRINTER, DeviceType.KEYBOARD):
            with self.subTest(kind=kind):
                runtime = create_device_runtime(DeviceConfig(
                    device_id=kind.value, device_type=kind,
                    policy=SchedulingPolicy.FCFS, default_service_time=0,
                ))
                self.assertIsNone(runtime.head_position)
                self.assertIsNone(runtime.direction)
                validate_runtime_state(runtime, {})

    def test_factory_does_not_share_mutable_runtime(self) -> None:
        config = disk_config()
        first = create_device_runtime(config)
        second = create_device_runtime(config)
        first.queue.append("request0")
        first.head_position = 70
        self.assertEqual(second.queue, [])
        self.assertEqual(second.head_position, 50)
        self.assertEqual(config.disk.initial_head, 50)

    def test_factory_rejects_invalid_config(self) -> None:
        with self.assertRaises(ValueError):
            create_device_runtime(disk_config(default_service_time=-1))

    def test_request_defaults_do_not_mutate_spec(self) -> None:
        spec = DeviceRequestSpec(device_id="disk0", operation="READ", cylinder=60)
        request = DeviceRequest(
            request_id="r0", generation=0, sequence=0, pid="P1", spec=spec,
            submitted_at=0, resolved_service_time=2,
        )
        self.assertEqual(request.state, RequestState.QUEUED)
        self.assertIsNone(request.start_time)
        self.assertIsNone(request.finish_time)
        request.state = RequestState.ACTIVE
        self.assertIsNone(spec.service_time)

    def test_fresh_factory_restores_initial_geometry(self) -> None:
        config = disk_config()
        previous = create_device_runtime(config)
        previous.head_position = 99
        previous.direction = ScanDirection.DOWN
        previous.state = DeviceState.FAILED
        previous.failure_reason = "Device failure"
        previous.generation = 3
        fresh = create_device_runtime(config)
        self.assertEqual(fresh.head_position, 50)
        self.assertEqual(fresh.direction, ScanDirection.UP)
        self.assertEqual(fresh.state, DeviceState.IDLE)
        self.assertIsNone(fresh.failure_reason)
        self.assertEqual(fresh.generation, 0)
        self.assertEqual(previous.generation, 3)


if __name__ == "__main__":
    unittest.main()
