"""Pure input and runtime invariant tests for Phase 1 device contracts."""

import unittest
from dataclasses import replace

from src.managers.device import (
    DeviceConfig, DeviceOperation, DeviceRequest, DeviceRequestSpec, DeviceRuntime,
    DeviceState, DeviceType, DiskGeometry, OperationKind, RequestState,
    ScanDirection, SchedulingPolicy, create_device_runtime, validate_device_config,
    validate_device_configs, validate_request_offsets, validate_request_spec,
    validate_runtime_state,
)


def config_for(kind: DeviceType = DeviceType.DISK) -> DeviceConfig:
    """Return a valid configuration for any supported peripheral."""
    return DeviceConfig(
        device_id="d0", device_type=kind,
        policy=SchedulingPolicy.SCAN if kind == DeviceType.DISK
        else SchedulingPolicy.FCFS,
        default_service_time=2,
        disk=DiskGeometry(
            min_cylinder=0, max_cylinder=99, initial_head=50,
            initial_direction=ScanDirection.UP, seek_time_per_cylinder=0.5,
        ) if kind == DeviceType.DISK else None,
    )


def request_for(**changes: object) -> DeviceRequest:
    """Return a valid queued disk request."""
    return replace(DeviceRequest(
        request_id="r0", generation=0, sequence=0, pid="P1",
        spec=DeviceRequestSpec(device_id="d0", operation="READ", cylinder=60),
        submitted_at=1, resolved_service_time=2,
    ), **changes)


def busy_runtime(
    kind: OperationKind = OperationKind.REQUEST_SEEK,
) -> tuple[DeviceRuntime, dict[str, DeviceRequest]]:
    """Build valid seek/service/endpoint snapshots at committed boundaries."""
    runtime = create_device_runtime(config_for())
    runtime.state = DeviceState.BUSY
    if kind == OperationKind.SCAN_ENDPOINT:
        runtime.active_operation = DeviceOperation(
            token="o0", generation=0, kind=kind, started_at=2, due_at=26.5,
            source_cylinder=50, target_cylinder=99,
        )
        return runtime, {}
    request = request_for(state=RequestState.ACTIVE, start_time=2)
    runtime.active_request_id = request.request_id
    if kind == OperationKind.SERVICE:
        runtime.head_position = 60
        runtime.active_operation = DeviceOperation(
            token="o1", generation=0, kind=kind, started_at=7, due_at=9,
            request_id="r0",
        )
    else:
        runtime.active_operation = DeviceOperation(
            token="o0", generation=0, kind=kind, started_at=2, due_at=7,
            request_id="r0", source_cylinder=50, target_cylinder=60,
        )
    return runtime, {"r0": request}


class TestDeviceInputValidation(unittest.TestCase):
    """Configuration, request operations, timing, and offset contracts."""

    def test_all_disk_policies_and_nonzero_bounds(self) -> None:
        for policy in SchedulingPolicy:
            validate_device_config(replace(config_for(), policy=policy))
        config = config_for()
        validate_device_config(replace(config, disk=replace(
            config.disk, min_cylinder=10, initial_head=10,
        )))

    def test_bad_config_fields(self) -> None:
        changes = [
            {"device_id": value} for value in ("", " ", " d0", "d0 ", None, 7)
        ] + [
            {"device_type": "DISK"}, {"policy": "FCFS"}, {"disk": None},
            {"disk": {}},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_device_config(replace(config_for(), **change))
        with self.assertRaises(ValueError):
            validate_device_config(None)

    def test_timing_rejects_bool_nonfinite_negative_and_wrong_types(self) -> None:
        for value in (True, False, -1, float("nan"), float("inf"),
                      -float("inf"), "2", None, 10 ** 1000):
            with self.subTest(value=str(value)[:20]):
                with self.assertRaises(ValueError):
                    validate_device_config(replace(
                        config_for(), default_service_time=value,
                    ))
                with self.assertRaises(ValueError):
                    validate_device_config(replace(config_for(), disk=replace(
                        config_for().disk, seek_time_per_cylinder=value,
                    )))

    def test_bad_geometry(self) -> None:
        for change in (
            {"min_cylinder": -1}, {"min_cylinder": True},
            {"max_cylinder": 0}, {"max_cylinder": 0.5},
            {"initial_head": -1}, {"initial_head": 100},
            {"initial_head": True}, {"initial_direction": "UP"},
            {"max_cylinder": 10 ** 1000, "seek_time_per_cylinder": 1.0},
            {"seek_time_per_cylinder": 1e308},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_device_config(replace(config_for(), disk=replace(
                    config_for().disk, **change,
                )))

    def test_non_disks_require_fcfs_and_no_geometry(self) -> None:
        for kind in (DeviceType.PRINTER, DeviceType.KEYBOARD):
            config = config_for(kind)
            validate_device_config(config)
            for change in ({"policy": SchedulingPolicy.SSTF},
                           {"policy": SchedulingPolicy.SCAN},
                           {"disk": config_for().disk}):
                with self.subTest(kind=kind, change=change):
                    with self.assertRaises(ValueError):
                        validate_device_config(replace(config, **change))

    def test_registry_unique_ids_and_no_input_mutation(self) -> None:
        configs = [config_for(), replace(config_for(DeviceType.PRINTER),
                                        device_id="printer0")]
        original = configs.copy()
        registry = validate_device_configs(configs)
        self.assertEqual(configs, original)
        self.assertIs(registry["d0"], configs[0])
        self.assertEqual(validate_device_configs([]), {})
        for invalid in ([config_for(), config_for()], "d0", {}, None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_device_configs(invalid)

    def test_device_specific_operations(self) -> None:
        allowed = {
            DeviceType.DISK: {"READ", "WRITE"},
            DeviceType.PRINTER: {"PRINT"}, DeviceType.KEYBOARD: {"READ"},
        }
        for kind in DeviceType:
            for operation in ("READ", "WRITE", "PRINT", "read", " READ", "", None):
                spec = DeviceRequestSpec(
                    device_id="d0", operation=operation,
                    cylinder=60 if kind == DeviceType.DISK else None,
                )
                with self.subTest(kind=kind, operation=operation):
                    if operation in allowed[kind]:
                        validate_request_spec(spec, config_for(kind))
                    else:
                        with self.assertRaises(ValueError):
                            validate_request_spec(spec, config_for(kind))

    def test_spec_cylinder_and_time_validation(self) -> None:
        spec = request_for().spec
        for cylinder in (None, -1, 100, True, 1.5, "60"):
            with self.subTest(cylinder=cylinder), self.assertRaises(ValueError):
                validate_request_spec(replace(spec, cylinder=cylinder), config_for())
        for cylinder in (0, 99):
            validate_request_spec(replace(spec, cylinder=cylinder), config_for())
        for value in (None, 0, 0.25, 2):
            validate_request_spec(replace(spec, service_time=value), config_for())
        for value in (True, -1, "2", float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_request_spec(replace(spec, service_time=value), config_for())
        with self.assertRaises(ValueError):
            validate_request_spec(replace(spec, device_id="unknown"), config_for())
        with self.assertRaises(ValueError):
            validate_request_spec(None, config_for())
        for kind, operation in ((DeviceType.PRINTER, "PRINT"),
                                (DeviceType.KEYBOARD, "READ")):
            with self.assertRaises(ValueError):
                validate_request_spec(replace(spec, operation=operation),
                                      config_for(kind))

    def test_request_offsets(self) -> None:
        registry = {"d0": config_for()}
        spec = request_for().spec
        requests = {0: spec, 2: spec}
        validate_request_offsets(requests, 3, registry)
        validate_request_offsets({2: spec}, 2.5, registry)
        self.assertEqual(requests, {0: spec, 2: spec})
        for offset in (-1, 3, True, 0.5, "0", "01"):
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                validate_request_offsets({offset: spec}, 3, registry)
        for burst in (0, -1, True, "3", float("nan"), float("inf")):
            with self.subTest(burst=burst), self.assertRaises(ValueError):
                validate_request_offsets({}, burst, registry)
        for requests, configs in (
            ([], registry), ({0: None}, registry), ({0: spec}, {}),
            ({}, {"wrong": config_for()}), ({}, None),
        ):
            with self.subTest(requests=requests), self.assertRaises(ValueError):
                validate_request_offsets(requests, 3, configs)


class TestDeviceRuntimeValidation(unittest.TestCase):
    """Validate stable snapshots rather than implementing lifecycle transitions."""

    def test_valid_queued_active_and_terminal_states(self) -> None:
        runtime = create_device_runtime(config_for())
        runtime.queue = ["r0"]
        validate_runtime_state(runtime, {"r0": request_for()})
        for kind in OperationKind:
            validate_runtime_state(*busy_runtime(kind))
        runtime.queue.clear()
        for state in (RequestState.COMPLETED, RequestState.FAILED,
                      RequestState.CANCELLED):
            for started in (None, 2):
                if state == RequestState.COMPLETED and started is None:
                    continue
                validate_runtime_state(runtime, {"r0": request_for(
                    state=state, start_time=started, finish_time=9,
                    error="device failed" if state == RequestState.FAILED else None,
                )})

    def test_valid_offline_failed_and_reset_generation(self) -> None:
        runtime = create_device_runtime(config_for())
        runtime.state = DeviceState.OFFLINE
        validate_runtime_state(runtime, {})
        runtime.state = DeviceState.FAILED
        runtime.failure_reason = "unplugged"
        validate_runtime_state(runtime, {})
        fresh = create_device_runtime(runtime.config)
        fresh.generation = runtime.generation + 1
        validate_runtime_state(fresh, {})

    def test_runtime_structure_errors(self) -> None:
        for change in (
            {"state": "IDLE"}, {"generation": True}, {"generation": -1},
            {"head_position": 100}, {"direction": "UP"}, {"queue": ()},
            {"queue": ["r0", "r0"]}, {"queue": [[]]},
            {"queue": ["unknown"]}, {"active_request_id": "unknown"},
            {"state": DeviceState.BUSY}, {"failure_reason": "failed"},
            {"state": DeviceState.FAILED},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_runtime_state(replace(
                    create_device_runtime(config_for()), **change,
                ), {})
        with self.assertRaises(ValueError):
            validate_runtime_state(None, {})
        with self.assertRaises(ValueError):
            validate_runtime_state(create_device_runtime(config_for()), [])
        for field, value in (("head_position", 0), ("direction", ScanDirection.UP)):
            runtime = create_device_runtime(config_for(DeviceType.PRINTER))
            setattr(runtime, field, value)
            with self.assertRaises(ValueError):
                validate_runtime_state(runtime, {})

    def test_queue_ownership_and_state_correspondence(self) -> None:
        for queue, active, request in (
            ([], None, request_for()),
            (["r0"], None, request_for(state=RequestState.CANCELLED, finish_time=2)),
            (["r0"], "r0", request_for()),
            (["r0"], None, request_for(spec=replace(
                request_for().spec, device_id="other",
            ))),
            ([], None, request_for(state=RequestState.ACTIVE, start_time=2)),
        ):
            runtime = create_device_runtime(config_for())
            runtime.queue = queue
            runtime.active_request_id = active
            with self.subTest(queue=queue, active=active):
                with self.assertRaises(ValueError):
                    validate_runtime_state(runtime, {"r0": request})
        for state in (DeviceState.OFFLINE, DeviceState.FAILED):
            runtime = create_device_runtime(config_for())
            runtime.state = state
            runtime.failure_reason = "failed" if state == DeviceState.FAILED else None
            runtime.queue = ["r0"]
            with self.assertRaises(ValueError):
                validate_runtime_state(runtime, {"r0": request_for()})

    def test_bad_request_records(self) -> None:
        for change in (
            {"pid": " "}, {"request_id": ""}, {"generation": 1},
            {"sequence": True}, {"state": "QUEUED"}, {"submitted_at": -1},
            {"resolved_service_time": 0}, {"start_time": 0},
            {"start_time": 2}, {"finish_time": 3}, {"error": "unexpected"},
            {"state": RequestState.ACTIVE},
            {"state": RequestState.COMPLETED, "finish_time": 3},
            {"state": RequestState.CANCELLED},
            {"state": RequestState.CANCELLED, "finish_time": 0},
            {"state": RequestState.FAILED, "finish_time": 3},
        ):
            request = request_for(**change)
            runtime = create_device_runtime(config_for())
            if request.state == RequestState.QUEUED:
                runtime.queue = ["r0"]
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_runtime_state(runtime, {"r0": request})
        runtime = create_device_runtime(config_for())
        for records in ({"wrong": request_for()}, {"r0": None}):
            with self.assertRaises(ValueError):
                validate_runtime_state(runtime, records)
        runtime.queue = ["r0", "r1"]
        with self.assertRaises(ValueError):
            validate_runtime_state(runtime, {
                "r0": request_for(), "r1": request_for(request_id="r1"),
            })

    def test_operation_validation(self) -> None:
        for change in (
            {"token": ""}, {"generation": 1}, {"kind": "SERVICE"},
            {"started_at": -1}, {"started_at": 0}, {"due_at": float("inf")},
            {"due_at": 1}, {"due_at": 8}, {"request_id": "other"},
            {"source_cylinder": 49}, {"target_cylinder": 61},
            {"target_cylinder": 100},
        ):
            runtime, records = busy_runtime()
            runtime.active_operation = replace(runtime.active_operation, **change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_runtime_state(runtime, records)
        runtime, records = busy_runtime()
        runtime.state = DeviceState.IDLE
        with self.assertRaises(ValueError):
            validate_runtime_state(runtime, records)

    def test_service_and_endpoint_validation(self) -> None:
        for change in ({"source_cylinder": 60}, {"target_cylinder": 60},
                       {"due_at": 10}):
            runtime, records = busy_runtime(OperationKind.SERVICE)
            runtime.active_operation = replace(runtime.active_operation, **change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_runtime_state(runtime, records)
        runtime, records = busy_runtime(OperationKind.SERVICE)
        runtime.head_position = 50
        with self.assertRaises(ValueError):
            validate_runtime_state(runtime, records)
        runtime, records = busy_runtime(OperationKind.SCAN_ENDPOINT)
        runtime.direction = ScanDirection.DOWN
        with self.assertRaises(ValueError):
            validate_runtime_state(runtime, records)
        runtime.active_operation = replace(
            runtime.active_operation, target_cylinder=0, due_at=27,
        )
        validate_runtime_state(runtime, records)
        runtime.config = replace(runtime.config, policy=SchedulingPolicy.FCFS)
        with self.assertRaises(ValueError):
            validate_runtime_state(runtime, records)

    def test_zero_time_service_and_seek_are_valid(self) -> None:
        runtime, records = busy_runtime(OperationKind.SERVICE)
        runtime.config = replace(runtime.config, default_service_time=0)
        records["r0"].resolved_service_time = 0
        runtime.active_operation = replace(runtime.active_operation, due_at=7)
        validate_runtime_state(runtime, records)
        runtime, records = busy_runtime()
        runtime.config = replace(runtime.config, disk=replace(
            runtime.config.disk, seek_time_per_cylinder=0,
        ))
        runtime.active_operation = replace(runtime.active_operation, due_at=2)
        validate_runtime_state(runtime, records)

    def test_derived_due_time_must_be_finite(self) -> None:
        runtime, records = busy_runtime(OperationKind.SERVICE)
        runtime.config = replace(runtime.config, default_service_time=1e308)
        records["r0"].resolved_service_time = 1e308
        runtime.active_operation = replace(
            runtime.active_operation, started_at=1e308, due_at=1e308,
        )
        with self.assertRaises(ValueError):
            validate_runtime_state(runtime, records)

    def test_validation_does_not_mutate_runtime_or_registry(self) -> None:
        runtime, records = busy_runtime()
        before = replace(runtime, queue=runtime.queue.copy())
        request_before = replace(records["r0"])
        validate_runtime_state(runtime, records)
        self.assertEqual(runtime, before)
        self.assertEqual(records["r0"], request_before)
        runtime.queue.append("missing")
        with self.assertRaises(ValueError):
            validate_runtime_state(runtime, records)
        self.assertEqual(runtime.queue, ["missing"])

    def test_printer_and_keyboard_service_snapshots(self) -> None:
        for kind, operation in ((DeviceType.PRINTER, "PRINT"),
                                (DeviceType.KEYBOARD, "READ")):
            with self.subTest(kind=kind):
                runtime = create_device_runtime(config_for(kind))
                runtime.state = DeviceState.BUSY
                runtime.active_request_id = "r0"
                request = request_for(
                    spec=DeviceRequestSpec(device_id="d0", operation=operation),
                    state=RequestState.ACTIVE, start_time=2,
                )
                runtime.active_operation = DeviceOperation(
                    token="o0", generation=0, kind=OperationKind.SERVICE,
                    request_id="r0", started_at=2, due_at=4,
                )
                validate_runtime_state(runtime, {"r0": request})
                runtime.active_operation = replace(
                    runtime.active_operation, kind=OperationKind.REQUEST_SEEK,
                    source_cylinder=0, target_cylinder=1,
                )
                with self.assertRaises(ValueError):
                    validate_runtime_state(runtime, {"r0": request})

    def test_explicit_zero_override_is_not_default(self) -> None:
        runtime, records = busy_runtime(OperationKind.SERVICE)
        records["r0"].spec = replace(records["r0"].spec, service_time=0)
        records["r0"].resolved_service_time = 0
        runtime.active_operation = replace(runtime.active_operation, due_at=7)
        validate_runtime_state(runtime, records)
        records["r0"].resolved_service_time = 2
        with self.assertRaises(ValueError):
            validate_runtime_state(runtime, records)

    def test_endpoint_cannot_start_at_its_target(self) -> None:
        runtime, records = busy_runtime(OperationKind.SCAN_ENDPOINT)
        runtime.head_position = 99
        runtime.active_operation = replace(
            runtime.active_operation, source_cylinder=99, due_at=2,
        )
        with self.assertRaises(ValueError):
            validate_runtime_state(runtime, records)

    def test_invalid_terminal_timestamps_and_failure_reasons(self) -> None:
        runtime = create_device_runtime(config_for())
        for finished in (None, True, float("nan"), float("inf"), -1, 1):
            request = request_for(
                state=RequestState.CANCELLED, start_time=2, finish_time=finished,
            )
            with self.subTest(finished=finished), self.assertRaises(ValueError):
                validate_runtime_state(runtime, {"r0": request})
        for reason in (None, "", " ", 7):
            runtime.state = DeviceState.FAILED
            runtime.failure_reason = reason
            with self.subTest(reason=reason), self.assertRaises(ValueError):
                validate_runtime_state(runtime, {})

    def test_foreign_history_is_not_owned_by_this_device(self) -> None:
        runtime = create_device_runtime(config_for())
        foreign = request_for(spec=replace(request_for().spec, device_id="disk1"))
        validate_runtime_state(runtime, {"r0": foreign})
        runtime.queue = ["r0"]
        with self.assertRaises(ValueError):
            validate_runtime_state(runtime, {"r0": foreign})


if __name__ == "__main__":
    unittest.main()
