"""Device contracts, pure scheduling, and execution with caller-owned time.

SimulationEngine consumes operation callbacks and terminal notifications; Flask
uses json_inputs for workload conversion. See README.md, Device Manager inputs
and execution, for configuration, metrics, dashboard use, and known limitations.
"""

from .models import (
    DeviceConfig, DeviceOperation, DeviceRequest, DeviceRequestSpec, DeviceRuntime,
    DeviceState, DeviceType, DiskGeometry, OperationKind, RequestState,
    ScanDirection, SchedulingPolicy, create_device_runtime,
)
from .validation import (
    validate_device_config, validate_device_configs, validate_request_offsets,
    validate_request_spec, validate_runtime_state,
)
from .scheduler import DeviceDecisionKind, DeviceSchedulingDecision, select_next
from .device_manager import DeviceManager, DeviceNotification, OperationResult

__all__ = [
    "DeviceConfig", "DeviceOperation", "DeviceRequest", "DeviceRequestSpec",
    "DeviceRuntime", "DeviceState", "DeviceType", "DiskGeometry", "OperationKind",
    "RequestState", "ScanDirection", "SchedulingPolicy", "create_device_runtime",
    "validate_device_config", "validate_device_configs", "validate_request_offsets",
    "validate_request_spec", "validate_runtime_state",
    "DeviceDecisionKind", "DeviceSchedulingDecision", "select_next",
    "DeviceManager", "DeviceNotification", "OperationResult",
]
