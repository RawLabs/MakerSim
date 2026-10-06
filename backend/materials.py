"""Read retained MakerSim data through a small, new adapter."""
import csv
import json
import math
from pathlib import Path
from dataclasses import dataclass, asdict

DATA = Path(__file__).parent / "data"
BASELINE_URL = "https://github.com/ichris97/multimaterial-3d-printing/blob/master/src/multimaterial_3d/core/materials.py"


@dataclass(frozen=True)
class PhysicsMaterial:
    """Only the properties MakerSim understands, with explicit units.

    Strength fields are retained for future explanations, never used as a
    failure threshold. Product metadata belongs to a separate catalog entry.
    """
    E_xy_mpa: float
    E_z_mpa: float
    G_xy_mpa: float
    nu: float
    density_g_cm3: float | None = None
    tensile_xy_mpa: float | None = None
    tensile_z_mpa: float | None = None
    compression_mpa: float | None = None


def load_materials() -> dict[str, dict]:
    materials = {m["id"]: dict(m) for m in json.loads((DATA / "materials.json").read_text())}
    with (DATA / "makersim_filament_master.csv").open(newline="") as source:
        for row in csv.DictReader(source):
            # The CSV is the original source for the branded JSON entries.
            matching = next((m for m in materials.values()
                             if m["brand"] == row["brand"] and m["name"] == row["material_name"]), None)
            if matching is not None:
                for field, column in [('modulusZ','e_mpa_z'),('modulus','e_mpa_xy')]:
                    try:
                        value = float(row[column])
                        if math.isfinite(value) and value > 0:
                            matching[field] = value
                    except (ValueError, TypeError):
                        pass  # "Variable" and empty cells are not stiffness values.
    baselines = json.loads((DATA / "fdm_baselines.json").read_text())
    for baseline in baselines:
        material_id = "generic-" + baseline["key"].lower()
        if material_id not in materials:
            materials[material_id] = {"id": material_id, "brand": "Generic", "name": baseline["key"], "family": baseline["key"]}
        materials[material_id].update({
            "modulus": baseline["E"], "modulusZ": baseline["E_z"],
            "shearModulus": baseline["G_xy"], "poissonRatio": baseline["nu"],
            "density": baseline["density"], "tensileStrength": baseline["sigma_t"],
            "tensileStrengthZ": baseline["sigma_t_z"], "compressionStrength": baseline["sigma_c"],
            "source": "Representative FDM baseline", "source_url": BASELINE_URL,
            "category": baseline["category"], "basis": "representative_baseline",
        })
    for material in materials.values():
        family = material["family"]
        # Preserve every entry, but do not invent missing stiffness data or
        # treat continuous-fibre reinforcement as a homogeneous FFF plastic.
        material["supported"] = bool(material.get("modulus")) and "Continuous" not in material.get("notes", "") and "CCF" not in family and "Kevlar" not in family and "HSHT" not in family and material["id"] != "markforged-onyx-+-fiberglass"
        material["poissonRatio"] = material.get("poissonRatio", 0.35)
        if material["supported"]:
            e = float(material["modulus"])
            nu = material["poissonRatio"]
            ratio = 0.5 if "CF" in family else 0.7
            physical = PhysicsMaterial(
                E_xy_mpa=e, E_z_mpa=material.get("modulusZ",e*ratio),
                G_xy_mpa=material.get("shearModulus",e/(2*(1+nu))), nu=nu,
                density_g_cm3=material.get("density"),
                tensile_xy_mpa=material.get("tensileStrength"),
                tensile_z_mpa=material.get("tensileStrengthZ"),
                compression_mpa=material.get("compressionStrength"),
            )
            material["physics"] = asdict(physical)
            material["provenance"] = {
                "E_xy": material.get("basis", "archived_datasheet_value"),
                "E_z": material.get("basis", "archived_datasheet_value") if "modulusZ" in material else "assumed_ratio",
                "G_xy": material.get("basis", "archived_datasheet_value") if "shearModulus" in material else "derived_isotropic_estimate",
                "source": material.get("source", "MakerSim archive, source not recorded"),
                "url": material.get("source_url"),
                "conditions": "Baseline: 100% infill, 0.2 mm layers, good adhesion. MakerSim print corrections are heuristic.",
            }
    return materials


MATERIALS = load_materials()
