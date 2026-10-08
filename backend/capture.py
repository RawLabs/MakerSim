"""Local, headless revision images and a portable visual review document."""
import base64
import hashlib
from html import escape
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir

import numpy as np
from scipy.spatial import cKDTree
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from typing import Literal

from .schemas import Finite

from .local import AgentError, get_result, load_mesh, read_bytes, write_json

MAX_RENDER_TRIANGLES = 160_000
HEAT = ["#3769e8", "#2aa8f1", "#33c7b4", "#ead861", "#f69b47", "#ef5c52"]
DEFAULT_VIEWS = ["geometry", "stress_isometric", "stress_side", "movement"]
VIEWS = DEFAULT_VIEWS + ["geometry_underside", "stress_underside", "stress_detail"]


class CaptureCamera(BaseModel):
    model_config = ConfigDict(extra="forbid")
    elevation: Finite = Field(default=24, ge=-90, le=90)
    azimuth: Finite = Field(default=-60, ge=-360, le=360)
    zoom: Finite = Field(default=1.15, ge=.5, le=2)
    projection: Literal["ortho", "persp"] = "ortho"


def camera_options(camera, views, detail_bounds_mm):
    try:
        camera = CaptureCamera.model_validate(camera or {}).model_dump(mode="json")
    except ValidationError as error:
        raise AgentError("invalid_capture", "Invalid camera elevation, azimuth, zoom or projection.") from error
    views = list(DEFAULT_VIEWS) if views is None else views
    if not isinstance(views, list) or not views or len(views) > len(VIEWS) or any(view not in VIEWS for view in views) or len(set(views)) != len(views):
        raise AgentError("invalid_capture", f"Choose unique capture views from {VIEWS}.")
    if detail_bounds_mm is not None:
        try:
            bounds = np.asarray(detail_bounds_mm, dtype=float)
            if bounds.shape != (2, 3) or not np.isfinite(bounds).all() or (bounds[0] >= bounds[1]).any():
                raise ValueError()
        except (ValueError, TypeError) as error:
            raise AgentError("invalid_capture", "Detail bounds require finite [[xmin,ymin,zmin],[xmax,ymax,zmax]] with positive extents.") from error
        detail_bounds_mm = bounds.tolist()
    if "stress_detail" in views and detail_bounds_mm is None:
        raise AgentError("invalid_capture", "stress_detail needs detail_bounds_mm in report coordinates.")
    return camera, views, detail_bounds_mm


def capture_options(stress_max_mpa, movement_scale):
    if stress_max_mpa is not None and (not np.isfinite(stress_max_mpa) or stress_max_mpa <= 0):
        raise AgentError("invalid_capture", "Stress legend maximum must be a positive finite MPa value.")
    if not np.isfinite(movement_scale) or not .001 <= movement_scale <= 10000:
        raise AgentError("invalid_capture", "Movement display scale must be between 0.001 and 10000.")


def refine_surface(triangles, target):
    """Bisect long edges without changing the STL surface, within a display budget."""
    triangles = triangles.copy()
    for _ in range(10):
        lengths = np.sum((triangles-np.roll(triangles, -1, axis=1))**2, axis=2)
        edge = lengths.argmax(axis=1)
        selected = lengths.max(axis=1) > target**2
        if not selected.any() or len(triangles)+selected.sum() > MAX_RENDER_TRIANGLES:
            break
        rows = np.arange(selected.sum())
        source = triangles[selected]
        a, b, c = (source[rows, (edge[selected]+offset) % 3] for offset in range(3))
        mid = (a+b)/2
        triangles = np.concatenate([triangles[~selected], np.stack([a, mid, c], axis=1), np.stack([mid, b, c], axis=1)])
    vertices, inverse = np.unique(triangles.reshape(-1, 3), axis=0, return_inverse=True)
    return vertices, inverse.reshape(-1, 3)


def surface_fields(case):
    if "fields" not in case:
        raise AgentError("fields_required", "Capture needs surface fields. Run with --fields, or capture during run with --images.")
    try:
        fields = case["fields"]
        positions = np.asarray(fields["positions_mm"], dtype=float)
        displacement = np.asarray(fields["displacement_mm"], dtype=float)
        stress = np.asarray(fields["surface_stress_mpa"], dtype=float)
        if (positions.ndim != 2 or positions.shape[1] != 3 or len(positions) < 4
                or displacement.shape != positions.shape or stress.shape != (len(positions),)
                or not all(np.isfinite(a).all() for a in (positions, displacement, stress)) or (stress < 0).any()):
            raise ValueError("Invalid surface arrays")
    except (ValueError, TypeError, KeyError) as error:
        raise AgentError("invalid_fields", "Expected matching finite position, displacement and nonnegative stress arrays.") from error
    return positions, displacement, stress


def interpolate(vertices, positions, displacement, stress, pitch):
    distances, indices = cKDTree(positions).query(vertices, k=4)
    weights = 1/np.maximum(distances**2, (pitch*.15)**2)
    weights /= weights.sum(axis=1, keepdims=True)
    return (np.sum(displacement[indices]*weights[:, :, None], axis=1),
            np.sum(stress[indices]*weights, axis=1))


def draw_image(path, vertices, faces, stress, report, case, bounds, stress_max, view, movement_scale, camera):
    # Import only for image operations. No display server or browser is used.
    os.environ.setdefault("MPLCONFIGDIR", str(Path(gettempdir())/"makersim-matplotlib"))
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.colors import LinearSegmentedColormap, Normalize
    from matplotlib.cm import ScalarMappable
    from matplotlib.figure import Figure
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    fig = Figure(figsize=(10, 7), dpi=120, facecolor="#f4f7fb")
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(projection="3d", facecolor="#f4f7fb")
    cmap = LinearSegmentedColormap.from_list("makersim", HEAT)
    norm = Normalize(vmin=0, vmax=stress_max, clip=True)
    triangles = vertices[faces]
    geometry = view.startswith("geometry")
    if geometry:
        normal = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
        normal /= np.maximum(np.linalg.norm(normal, axis=1)[:, None], 1e-12)
        light = np.array([-.4, -.7, 1.])
        light /= np.linalg.norm(light)
        brightness = .55+.45*np.maximum(0, normal@light)
        colors = np.column_stack([brightness[:, None]*np.array([.54, .67, .8]), np.ones(len(faces))])
    else:
        colors = cmap(norm(stress[faces].mean(axis=1)))
    ax.add_collection3d(Poly3DCollection(triangles, facecolors=colors, edgecolors="none", antialiased=False,
                                        zsort="average", axlim_clip=view == "stress_detail"))
    ax.set_proj_type(camera["projection"])
    ax.view_init(elev=0 if view == "stress_side" else -abs(camera["elevation"] or 24) if view.endswith("underside") else camera["elevation"],
                 azim=90 if view == "stress_side" else camera["azimuth"])
    low, high = np.asarray(bounds)
    span = max(float((high-low).max()), .1)
    center = (high+low)/2
    half = np.maximum((high-low)/2+span*.04, span*.08)
    for setter, mid, radius in zip((ax.set_xlim, ax.set_ylim, ax.set_zlim), center, half):
        setter(mid-radius, mid+radius)
    ax.set_box_aspect(half, zoom=camera["zoom"])
    ax.set_xlabel("X (mm)"); ax.set_ylabel("Y (mm)"); ax.set_zlabel("Z (mm)")
    if geometry:
        ax.set_axis_off()
    spec = report["test_spec"]
    if view.startswith("stress"):
        def in_view(point):
            return view != "stress_detail" or np.all((np.asarray(point) >= low) & (np.asarray(point) <= high))
        for patch in spec["fixtures"]:
            if in_view(patch["point"]):
                ax.scatter(*patch["point"], color="#139b76", s=75, depthshade=False)
        load = spec["load_cases"][report["load_cases"].index(case)]
        point = np.asarray(load["load"]["point"])
        direction = np.asarray(load["direction"])
        start = point-direction*span*.17
        if in_view(point):
            ax.quiver(*start, *direction, length=span*.17, color="#bc422c", linewidth=2, arrow_length_ratio=.25)
            ax.scatter(*point, color="#bc422c", s=35, depthshade=False)
    revision = report["source"].get("revision") or Path(report["source"]["path"]).stem
    label = {"geometry": "Geometry · original STL", "geometry_underside": "Geometry · underside",
             "stress_isometric": "Surface stress · isometric", "stress_underside": "Surface stress · underside",
             "stress_detail": "Surface stress · detail", "stress_side": "Surface stress · side",
             "movement": f"Movement · {movement_scale:g}× display scale"}[view]
    fig.suptitle(f"{revision} | {label}\n{case['name']} · {case['force_newtons']:.4g} N", fontsize=15)
    caption = (f"Actual max movement: {case['max_displacement_mm']:.6g} mm   |   "
               f"Peak surface stress: {case['peak_surface_stress_mpa']:.6g} MPa")
    if geometry:
        caption += "\nDimensions: "+" × ".join(f"{v:g}" for v in report["model"]["dimensions_mm"])+" mm"
    if view.startswith("stress"):
        caption += "\nDetail crop · metrics refer to the whole part" if view == "stress_detail" else "\nGreen markers: holds · red arrow: applied force"
    if not geometry:
        bar = fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), ax=ax, fraction=.035, pad=.10, shrink=.75)
        bar.set_label("Surface von Mises stress (MPa)")
        if case["peak_surface_stress_mpa"] > stress_max:
            caption += f"\nValues above {stress_max:g} MPa use the highest color."
    fig.text(.5, .055, caption, ha="center", fontsize=10)
    fig.text(.5, .018, "Linear-elastic preview · interpolated surface field · mesh convergence not checked", ha="center", fontsize=9, color="#526171")
    fig.subplots_adjust(left=.02, right=.9, top=.83, bottom=.13 if geometry else .20)
    with path.open("xb") as destination:
        fig.savefig(destination, format="png", metadata={"Software": "MakerSim local capture"})
    fig.clear()


def review_document(reports):
    """Embed captures so the review file can be moved or shared on its own."""
    cards = []
    scales = set()
    movement_scales = set()
    options = []
    for report in reports:
        captures = report.get("captures")
        if not isinstance(captures, dict) or not captures.get("images"):
            raise AgentError("captures_required", "Each review report needs captures. Use run --images or capture first.")
        scales.add(captures["stress_max_mpa"])
        movement_scales.add(captures["movement_display_scale"])
        revision = report["source"].get("revision") or Path(report["source"]["path"]).stem
        options.append(f'<option value="{len(cards)}">{escape(revision)}</option>')
        iteration = report.get("iteration", {})
        parent = iteration.get("parent") or {}
        lineage = parent.get("revision") or parent.get("source_sha256", "None")
        pictures = []
        for entry in captures["images"]:
            data = read_bytes(entry["path"], 10*1024*1024)
            if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise AgentError("capture_changed", f"Capture hash does not match: {entry['path']}")
            uri = base64.b64encode(data).decode("ascii")
            pictures.append(f'<figure><img loading="lazy" src="data:image/png;base64,{uri}" alt="{escape(entry["view"], quote=True)}"><figcaption>{escape(entry["case_name"])} · {escape(entry["view"])}</figcaption></figure>')
        rows = "".join(f'<tr><td>{escape(c["name"])}</td><td>{c["max_displacement_mm"]:.6g} mm</td><td>{c["peak_surface_stress_mpa"]:.6g} MPa</td><td>{escape(c["criteria_status"])}</td></tr>' for c in report["load_cases"])
        goal = iteration.get("goal") or report["test_spec"]["name"]
        cards.append(f'<article><h2>{escape(revision)}</h2><p><b>Branch:</b> {escape(iteration.get("branch") or "Unspecified")} · <b>Parent:</b> {escape(lineage)}</p><p><b>Goal:</b> {escape(goal)}</p><p>Solid volume: {report["model"]["solid_volume_mm3"]:.6g} mm³ · STL hash: <code>{escape(report["source"]["sha256"])}</code></p><p><b>Report to branch from:</b> <code>{escape(report.get("report_path") or "Unsaved report")}</code></p><table><tr><th>Load case</th><th>Max movement</th><th>Peak surface stress</th><th>Numerical criteria</th></tr>{rows}</table><div class="pictures">{"".join(pictures)}</div></article>')
    legend_note = ("All revisions use the same stress legend maximum." if len(scales) == 1 else
                   "Stress legend maxima differ between revisions. Compare numeric values, or recapture with a common --stress-max.")
    if len(movement_scales) > 1:
        legend_note += " Movement display scales differ. Recapture with a common --movement-scale for visual movement comparison."
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MakerSim revision review</title>
<style>body{{font:16px/1.5 system-ui;background:#edf2f7;color:#172839;margin:0}}main{{max-width:1500px;margin:auto;padding:24px}}article{{background:white;border-radius:10px;padding:24px;margin:24px 0;min-width:0}}.pictures,.comparison{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}.comparison .pictures{{grid-template-columns:1fr}}figure{{margin:0}}img{{width:100%;height:auto}}figcaption{{font-size:13px;color:#526171}}table{{border-collapse:collapse;width:100%;table-layout:fixed}}td,th{{overflow-wrap:anywhere;text-align:left;padding:8px;border-bottom:1px solid #d8e1eb}}code{{overflow-wrap:anywhere}}select{{font:inherit;margin:8px;padding:6px}}summary{{cursor:pointer}}@media(max-width:900px){{.pictures,.comparison{{grid-template-columns:1fr}}}}</style>
<main><h1>MakerSim revision review</h1><p>Review geometry, aesthetics and calculated behavior together. Choose any revision as the parent of a new branch; numerical ranking does not choose the preferred design.</p><p>{legend_note}</p><p>Goal text records intent. Absolute limits come from the test; relative targets are assessed in separate comparison evaluations. If the test changes, rerun the chosen parent as the new branch baseline before comparing.</p>
<label>Left revision <select id="left-revision">{"".join(options)}</select></label><label>Right revision <select id="right-revision">{"".join(options)}</select></label><section id="comparison" class="comparison"></section>
<details><summary>All saved revisions ({len(cards)})</summary><div id="history">{"".join(cards)}</div></details></main>
<script>const cards=Array.from(document.querySelector('#history').children);const left=document.querySelector('#left-revision');const right=document.querySelector('#right-revision');right.value=String(cards.length-1);function compare(){{document.querySelector('#comparison').replaceChildren(cards[Number(left.value)].cloneNode(true),cards[Number(right.value)].cloneNode(true));}}left.addEventListener('change',compare);right.addEventListener('change',compare);compare();</script></html>'''


def export_captures(report, output_dir, stl_path=None, stress_max_mpa=None, movement_scale=1, *,
                    camera=None, views=None, detail_bounds_mm=None):
    capture_options(stress_max_mpa, movement_scale)
    camera, views, detail_bounds_mm = camera_options(camera, views, detail_bounds_mm)
    target = Path(output_dir).expanduser().resolve()
    if target.exists():
        raise AgentError("output_exists", "Capture directory already exists. Choose a new revision directory.")
    mesh, source = load_mesh(stl_path or report["source"]["path"], report["test_spec"]["stl_unit"])
    if source["sha256"] != report["source"]["sha256"]:
        raise AgentError("source_changed", "The STL no longer matches the saved result. Use the original revision STL.")
    original = mesh.triangles
    if mesh.volume < 0:
        original = original[:, ::-1]
    if report["test_spec"]["coordinate_frame"] == "source_mm":
        original = original+mesh.source_center_mm
    vertices, faces = refine_surface(original, float(mesh.extents.max())/50)
    # Small triangles make painter ordering more reliable around overlapping ribs.
    # Subdivision preserves the original surface; disable seams during rasterization.
    geometry_vertices, geometry_faces = vertices, faces
    bounds = [original.min(axis=(0, 1)), original.max(axis=(0, 1))]
    if detail_bounds_mm is not None and (np.any(np.asarray(detail_bounds_mm)[1] <= bounds[0]) or np.any(np.asarray(detail_bounds_mm)[0] >= bounds[1])):
        raise AgentError("invalid_capture", "Detail bounds do not intersect this part.")
    prepared = []
    pitch = float(report["mesh"]["cell_mm"])
    if not np.isfinite(pitch) or pitch <= 0:
        raise AgentError("invalid_fields", "A positive finite mesh pitch is required.")
    for case in report["load_cases"]:
        positions, displacement, stress = surface_fields(case)
        u, s = interpolate(vertices, positions, displacement, stress, pitch)
        prepared.append((vertices+u*movement_scale, s))
    legend_max = stress_max_mpa or max(max(case["fields"]["surface_stress_mpa"]) for case in report["load_cases"])
    legend_max = max(float(legend_max), 1e-12)
    moving_bounds = [np.minimum(bounds[0], np.min([p.min(axis=0) for p, _ in prepared], axis=0)),
                     np.maximum(bounds[1], np.max([p.max(axis=0) for p, _ in prepared], axis=0))]
    result = {"schema_version": 1, "renderer": "matplotlib_agg", "source_sha256": source["sha256"],
              "test_sha256": report["test_sha256"], "stress_max_mpa": legend_max,
              "stress_scale": "fixed" if stress_max_mpa is not None else "report_maximum",
              "movement_display_scale": movement_scale, "coordinate_frame": report["test_spec"]["coordinate_frame"],
              "camera": camera, "views": views, "detail_bounds_mm": detail_bounds_mm,
              "images": [], "review_path": str(target/"index.html"), "manifest_path": str(target/"captures.json"),
              "report_path": str(target/"report.json")}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".makersim-capture-", dir=target.parent) as temporary:
            stage = Path(temporary)
            for index, (case, (moved, stress)) in enumerate(zip(report["load_cases"], prepared), 1):
                for view in views:
                    filename = f"case-{index:02d}-{view}.png"
                    path = stage/filename
                    display_vertices = geometry_vertices if view.startswith("geometry") else moved if view == "movement" else vertices
                    draw_image(path, display_vertices, geometry_faces if view.startswith("geometry") else faces, stress, report, case,
                               detail_bounds_mm if view == "stress_detail" else moving_bounds if view == "movement" else bounds,
                               legend_max, view, movement_scale, camera)
                    result["images"].append({"case_name": case["name"], "view": view, "path": str(target/filename),
                                             "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            # Build the standalone review using staged files, while recording final paths.
            staged_report = {**report, "report_path": result["report_path"], "captures": {**result, "images": [
                {**entry, "path": str(stage/Path(entry["path"]).name)} for entry in result["images"]]}}
            (stage/"index.html").write_text(review_document([staged_report]), encoding="utf-8")
            write_json(stage/"captures.json", result)
            # Keep a full-field copy for later recapture, preserving the original report.
            write_json(stage/"report.json", {**report, "captures": result, "report_path": result["report_path"]})
            target.mkdir()  # Exclusive reservation: preserve all existing revision captures.
            for path in stage.iterdir():
                (target/path.name).hardlink_to(path)
    except FileExistsError as error:
        raise AgentError("output_exists", "Capture directory already exists. Choose a new revision directory.") from error
    except OSError as error:
        raise AgentError("file_error", f"Cannot save captures: {error.strerror}") from error
    return result


def capture_report(report_path, output_dir, stl_path=None, stress_max_mpa=None, movement_scale=1, *,
                   camera=None, views=None, detail_bounds_mm=None):
    return export_captures(get_result(report_path), output_dir, stl_path, stress_max_mpa, movement_scale,
                           camera=camera, views=views, detail_bounds_mm=detail_bounds_mm)


def create_review(report_paths, output_path):
    if not 1 <= len(report_paths) <= 32:
        raise AgentError("invalid_review", "Choose between 1 and 32 saved revision reports.")
    reports = [get_result(path) for path in report_paths]
    document = review_document(reports)
    target = Path(output_path).expanduser().resolve()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as output:
            output.write(document)
    except FileExistsError as error:
        raise AgentError("output_exists", "Review file already exists. Choose a new filename.") from error
    except OSError as error:
        raise AgentError("file_error", f"Cannot save review: {error.strerror}") from error
    return {"review_path": str(target), "revisions": len(reports),
            "notes": ["Visual choice and goals are independent of numerical ranking; branching preserves the parent."]}
