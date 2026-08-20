"""Representation gates shared by the CLI and experiment harness."""

from __future__ import annotations

from enum import Enum

from .errors import SpatialModeError


class SpatialMode(str, Enum):
    OFF = "off"
    OPT_IN = "opt_in"
    REQUIRED = "required"

    @classmethod
    def parse(cls, value: str | "SpatialMode" | None) -> "SpatialMode":
        if isinstance(value, cls):
            return value
        normalized = str(value or cls.OFF.value).strip().lower().replace("-", "_")
        try:
            return cls(normalized)
        except ValueError as error:
            choices = ", ".join(item.value for item in cls)
            raise SpatialModeError(
                f"Unknown Spatial mode {value!r}",
                repair=f"Choose one of: {choices}",
            ) from error


class AuthoringPolicy:
    """Fail-closed representation policy; it never performs a fallback."""

    def __init__(self, mode: str | SpatialMode | None = None) -> None:
        self.mode = SpatialMode.parse(mode)

    def allow_spatial(self) -> None:
        if self.mode is SpatialMode.OFF:
            raise SpatialModeError(
                "Spatial authoring is disabled",
                repair="Set the project Spatial mode to opt_in or required",
            )

    def allow_raw_bpy(self) -> None:
        if self.mode is SpatialMode.REQUIRED:
            raise SpatialModeError(
                "Raw bpy authoring is forbidden while Spatial is required",
                repair="Use a Spatial source or explicitly change the project mode",
            )

    def describe(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "spatialAllowed": self.mode is not SpatialMode.OFF,
            "rawBpyAllowed": self.mode is not SpatialMode.REQUIRED,
            "fallbackAllowed": False,
        }
