"""Versioned, file-backed test inputs shared by the local CLI and MCP server."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .schemas import Finite, Patch, PrintSettings, normalized_vector


class LoadCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    load: Patch
    direction: tuple[Finite, Finite, Finite]
    magnitude: Finite = Field(gt=0, le=100000)
    unit: Literal["N", "lbf", "kg", "stone"] = "N"
    max_displacement_mm: Finite | None = Field(default=None, gt=0)

    @field_validator("direction")
    @classmethod
    def normalize_direction(cls, value):
        return normalized_vector(value, "A load case needs a finite, nonzero direction.")


class SurfaceProbe(Patch):
    name: str = Field(min_length=1, max_length=100)
    max_displacement_mm: Finite | None = Field(default=None, gt=0)


class LocalTestSpec(BaseModel):
    """Points/radii are always mm. source_mm uses the uncentered STL axes."""
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    name: str = Field(default="Part test", min_length=1, max_length=200)
    stl_unit: Literal["mm", "inch"] = "mm"
    coordinate_frame: Literal["source_mm", "centered_mm"] = "source_mm"
    material_id: str = Field(default="generic-pla", min_length=1, max_length=100)
    print_settings: PrintSettings = Field(default_factory=PrintSettings)
    fixtures: list[Patch] = Field(min_length=1, max_length=160)
    load_cases: list[LoadCase] = Field(min_length=1, max_length=16)
    surface_probes: list[SurfaceProbe] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def unique_names(self):
        for values, label in ((self.load_cases, "Load case"), (self.surface_probes, "Surface probe")):
            names = [value.name for value in values]
            if len(set(names)) != len(names):
                raise ValueError(f"{label} names must be unique.")
        return self


class RelativeRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: Literal["solid_volume_mm3", "max_displacement_mm", "peak_surface_stress_mpa",
                    "surface_probe_max_displacement_mm"]
    load_case: str | None = Field(default=None, min_length=1, max_length=100)
    probe: str | None = Field(default=None, min_length=1, max_length=100)
    operator: Literal["at_most", "at_least"] = "at_most"
    change_percent: Finite

    @model_validator(mode="after")
    def scope(self):
        if (self.metric == "solid_volume_mm3") != (self.load_case is None):
            raise ValueError("Case metrics require load_case; solid volume has no load_case.")
        if (self.metric == "surface_probe_max_displacement_mm") != (self.probe is not None):
            raise ValueError("Only a surface probe metric requires probe.")
        return self


class ComparisonGoals(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    requirements: list[RelativeRequirement] = Field(min_length=1, max_length=128)
