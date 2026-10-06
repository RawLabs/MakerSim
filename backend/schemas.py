from typing import Annotated, Literal

from pydantic import BaseModel, Field, ConfigDict, field_validator
import math

Finite = Annotated[float, Field(allow_inf_nan=False)]


class Patch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    point: tuple[Finite, Finite, Finite]
    radius: Annotated[Finite, Field(gt=0, le=1000)]
    normal: tuple[Finite, Finite, Finite]

    @field_validator("normal")
    @classmethod
    def nonzero_normal(cls, value):
        if math.sqrt(sum(x*x for x in value)) < 1e-8:
            raise ValueError("A patch needs a surface normal.")
        return value


class PrintSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    walls: int = Field(default=3, ge=1, le=20)
    top_layers: int = Field(default=4, ge=0, le=30)
    bottom_layers: int = Field(default=4, ge=0, le=30)
    infill: Finite = Field(default=20, ge=0, le=100)
    orientation: Literal["z", "x", "y"] = "z"


class SimulationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str = Field(min_length=1, max_length=64)
    material_id: str = Field(default="generic-pla", max_length=100)
    print_settings: PrintSettings = Field(default_factory=PrintSettings)
    fixtures: list[Patch] = Field(min_length=1, max_length=160)
    load: Patch
    direction: tuple[Finite, Finite, Finite]
    magnitude: Finite = Field(gt=0, le=100000)
    unit: Literal["N", "lbf", "kg", "stone"] = "lbf"

    @field_validator("direction")
    @classmethod
    def nonzero_direction(cls, value):
        if math.sqrt(sum(x*x for x in value)) < 1e-8:
            raise ValueError("Drag the force arrow or choose a direction.")
        return value


def force_newtons(magnitude: float, unit: str) -> float:
    return magnitude * {"N": 1.0, "lbf": 4.4482216152605, "kg": 9.80665, "stone": 14 * 4.4482216152605}[unit]
