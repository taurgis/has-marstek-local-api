"""Compatibility matrix for model and firmware differences."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Any, Final

_LOGGER = logging.getLogger(__name__)

HW_VERSION_2: Final = "2.0"
HW_VERSION_3: Final = "3.0"
HW_VERSION_ANY: Final = "*"

MODEL_ANY: Final = "*"

_MODEL_TOKENS: Final[tuple[str, ...]] = (
    "venusa",
    "venusb",
    "venusc",
    "venusd",
    "venuse",
)


@dataclass(frozen=True)
class DeviceCapabilities:
    """Capabilities derived from model identifier."""

    supports_pv: bool
    pv_channels: int


_DEFAULT_CAPABILITIES: Final = DeviceCapabilities(
    supports_pv=False,
    pv_channels=0,
)

_CAPABILITIES_BY_MODEL: Final[dict[str, DeviceCapabilities]] = {
    "venusa": DeviceCapabilities(supports_pv=True, pv_channels=4),
    "venusd": DeviceCapabilities(supports_pv=True, pv_channels=4),
    "venusc": DeviceCapabilities(supports_pv=False, pv_channels=0),
    "venuse": DeviceCapabilities(supports_pv=False, pv_channels=0),
}


def _parse_firmware_version(firmware_version: int | str | None) -> int:
    if firmware_version is None:
        return 0
    if isinstance(firmware_version, int):
        return firmware_version
    try:
        return int(str(firmware_version))
    except ValueError:
        return 0


def parse_hardware_version(device_model: str | None) -> str:
    """Extract hardware version from device model string."""
    if not device_model:
        return HW_VERSION_2
    match = re.search(r"(\d+\.\d+)", device_model)
    if match:
        return match.group(1)
    return HW_VERSION_2


def get_base_model(device_model: str | None) -> str:
    """Get base model name without hardware version suffix."""
    if not device_model:
        return ""
    return re.sub(r"\s+\d+\.\d+.*$", "", device_model)


def _normalize_model(model: str | None) -> str:
    if not model:
        return ""
    return "".join(ch for ch in model if ch.isalnum()).lower()


def get_model_key(device_model: str | None) -> str:
    """Return normalized model key (e.g., 'venusa', 'venuse')."""
    normalized = _normalize_model(get_base_model(device_model))
    for token in _MODEL_TOKENS:
        if token in normalized:
            return token
    return MODEL_ANY


class CompatibilityMatrix:
    """Compatibility matrix for model/hardware/firmware scaling."""

    SCALING_MATRIX: dict[str, dict[str, dict[str, dict[int, float]]]] = {
        "bat_temp": {
            MODEL_ANY: {
                HW_VERSION_2: {0: 1.0, 154: 0.1},
                HW_VERSION_3: {0: 1.0, 139: 10.0},
            },
        },
        "bat_cap": {
            MODEL_ANY: {
                HW_VERSION_2: {0: 100.0, 154: 1.0},
                HW_VERSION_3: {0: 1.0, 139: 0.1},
            },
        },
        "bat_capacity": {
            MODEL_ANY: {
                HW_VERSION_2: {0: 100.0, 154: 1.0},
                HW_VERSION_3: {0: 1.0, 139: 0.1},
            },
        },
        "bat_rated_capacity": {
            MODEL_ANY: {
                HW_VERSION_2: {0: 100.0, 154: 1.0},
                HW_VERSION_3: {0: 1.0, 139: 0.1},
            },
        },
        "bat_power": {
            MODEL_ANY: {
                HW_VERSION_2: {0: 10.0, 154: 1.0},
                HW_VERSION_3: {0: 1.0},
            },
        },
        "total_grid_input_energy": {
            MODEL_ANY: {
                HW_VERSION_2: {0: 0.1, 154: 0.01},
                HW_VERSION_3: {0: 1.0},
            },
        },
        "total_grid_output_energy": {
            MODEL_ANY: {
                HW_VERSION_2: {0: 0.1, 154: 0.01},
                HW_VERSION_3: {0: 1.0},
            },
        },
        "total_load_energy": {
            MODEL_ANY: {
                HW_VERSION_2: {0: 0.1, 154: 0.01},
                HW_VERSION_3: {0: 1.0},
            },
        },
        "bat_voltage": {
            MODEL_ANY: {
                HW_VERSION_2: {0: 100.0},
                HW_VERSION_3: {0: 100.0},
            },
        },
        "bat_current": {
            MODEL_ANY: {
                HW_VERSION_2: {0: 100.0},
                HW_VERSION_3: {0: 100.0},
            },
        },
    }

    def __init__(self, device_model: str | None, firmware_version: int | str | None) -> None:
        self.device_model = device_model or ""
        self.firmware_version = _parse_firmware_version(firmware_version)
        self.base_model = get_base_model(self.device_model)
        self.hardware_version = parse_hardware_version(self.device_model)
        self.model_key = get_model_key(self.device_model)

        _LOGGER.debug(
            "Compatibility matrix: model=%s base=%s key=%s hw=%s fw=%d",
            self.device_model,
            self.base_model,
            self.model_key,
            self.hardware_version,
            self.firmware_version,
        )

    @property
    def capabilities(self) -> DeviceCapabilities:
        return _CAPABILITIES_BY_MODEL.get(self.model_key, _DEFAULT_CAPABILITIES)

    def scale_value(self, value: Any, field: str) -> Any:
        """Scale a raw API value based on model/hardware/firmware."""
        if value is None or not isinstance(value, (int, float)):
            return value

        field_map = self.SCALING_MATRIX.get(field)
        if not field_map:
            return value

        model_map = field_map.get(self.model_key) or field_map.get(MODEL_ANY)
        if not model_map:
            return value

        hw_map = model_map.get(self.hardware_version) or model_map.get(HW_VERSION_ANY)
        if not hw_map:
            return value

        applicable = [
            (fw_ver, divisor)
            for fw_ver, divisor in hw_map.items()
            if fw_ver <= self.firmware_version
        ]
        if not applicable:
            return value

        selected_fw_ver, divisor = max(applicable, key=lambda item: item[0])
        _LOGGER.debug(
            "Scaling %s: fw=%d hw=%s model=%s -> fw=%d divisor=%s",
            field,
            self.firmware_version,
            self.hardware_version,
            self.model_key,
            selected_fw_ver,
            divisor,
        )
        return value / divisor

    def get_info(self) -> dict[str, Any]:
        """Return compatibility diagnostics."""
        return {
            "device_model": self.device_model,
            "base_model": self.base_model,
            "model_key": self.model_key,
            "hardware_version": self.hardware_version,
            "firmware_version": self.firmware_version,
        }