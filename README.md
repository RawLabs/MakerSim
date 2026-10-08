<p align="center">
  <img src="frontend/public/favicon.svg" alt="MakerSim logo" width="110">
</p>

<h1 align="center">MakerSim</h1>
<p align="center"><strong>Follow the force.</strong><br>Design · Analyze · Make</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-0.1.0--playground-60A5FA" alt="Version 0.1.0 playground">
  <img src="https://img.shields.io/badge/demo-live%20playground-334155" alt="Live playground demo">
  <img src="https://img.shields.io/badge/backend-Python%20%2B%20FastAPI-009688" alt="Python and FastAPI backend">
  <img src="https://img.shields.io/badge/viewer-Three.js%20%2B%20Vite-0F172A" alt="Three.js and Vite viewer">
</p>

<p align="center">
  <a href="https://makersim.rawcastdigital.com/"><strong>Live demo</strong></a> ·
  <a href="https://makersim-playground.rawcastdigital.com/">Open the playground</a> ·
  <a href="https://shftstate.rawcastdigital.com/#contact">Request a testing session</a>
</p>

[![MakerSim landing page with a rotatable 3D bracket, held mounting points, a downward pull, and relative stress colors](artifacts/landing-hero.png)](https://makersim.rawcastdigital.com/)

MakerSim helps makers explore how a force travels through a 3D-printed part.
Upload an STL, choose your filament and print settings, paint the surfaces held
still, and place a pull. An elasticity solver returns a relative stress
heatmap and an optional movement preview with an adjustable display scale.

The landing page stays available when the testing server is offline. Its 3D hero
uses a precomputed result from the included bracket; the playground runs your
own simulation when the server is online. You can also run MakerSim locally.

**Version 0.1.0 is an early playground checkpoint.** STL viewing, painted holds,
distributed loads, print-property assumptions, and simulation are implemented.
Results support design exploration; they do not predict failure or establish
safe working loads. See [physics and limits](#physics-and-limits).

## Quick start

From an installed checkout:

```bash
./scripts/start.sh
```

Open the local URL printed by the launcher. It tries **http://127.0.0.1:8000**,
then ports 8001–8010 if occupied, and finally an available system-assigned port.
To choose a port, run `./scripts/start.sh 8001`. Keep the terminal open while
working; press `Ctrl+C` to stop the app.

1. Choose **Try a backpack bracket** for a ready-to-run example, or **Upload your
   STL** in the center of the 3D workspace. After upload, file details, units, and
   replacement controls appear under **Your part** on the left.
2. Set your filament, walls, top/bottom layers, infill, and print orientation.
3. Choose **Hold here** and click or paint mounted surfaces. Choose **Pull here**
   and click a load surface, then drag the arrow or select a direction preset.
4. Set the weight or force and choose **Simulate**. Explore the heatmap, switch
   back to the original part, or enable **Show movement**. Movement starts at
   **1× actual scale**; select a larger or smaller display scale when needed.
   The chosen scale stays fixed between runs so different loads can be compared.

The included bracket starts with two mounting-hole patches held still and a
25 lb downward pull. Clear its selections to try your own setup. Use **Orbit**
to reach another side. Direction presets use the STL's axes, with Z up; the
contact-size slider applies to new clicks.

## Local AI agent interface

An agent can use MakerSim directly through the local CLI or stdio MCP server.
Both call the same solver as the app. Python 3.11+ and `uv` are sufficient for
this workflow; Node.js and a browser are only needed for the viewer.

On MCP connection, MakerSim supplies the design/test/review workflow in its
initialization instructions. `get_workflow` exposes the same guidance as a tool;
CLI agents can read it with `.venv/bin/makersim workflow`. It tells the agent to
save CAD revisions, reports and captures, present alternatives for user review,
and continue from the exact revision the user selects. The default first review
checkpoint is a baseline plus up to three useful candidates unless the user
sets another budget. User instructions take precedence. This is agent guidance,
not an enforced autonomous CAD controller; the client must pass it to the agent
and provide access to its CAD tool.

```bash
uv sync
.venv/bin/makersim schema
.venv/bin/makersim workflow
.venv/bin/makersim materials
.venv/bin/makersim inspect backend/data/backpack-bracket.stl
.venv/bin/makersim validate backend/data/bracket-test.json
.venv/bin/makersim run backend/data/backpack-bracket.stl \
  --test backend/data/bracket-test.json \
  --output .makersim/runs/bracket-r1.json --revision cad-r1
.venv/bin/makersim result .makersim/runs/bracket-r1.json --view metrics
```

[`backend/data/bracket-test.json`](backend/data/bracket-test.json) is a working
example with fixtures, print settings, 5 lb and 50 lb load cases, a tip probe,
and a 1 mm movement limit. It calculates approximately 0.395 mm and 3.948 mm
maximum movement respectively; the heavy case exceeds that numerical limit.
Use a new output filename for each run. Reports are never overwritten.

CLI operations return JSON on stdout. Successful analysis exits 0 even when
displacement criteria are `not_met`; file, validation and solver errors exit 2
with an `error.code`, message and optional details. Argument usage errors appear
on stderr. Without displacement limits the criteria are `not_assessed`.
`--fields` adds full surface positions, displacement vectors and stress values;
compact reports contain measurements, probe results, equilibrium checks,
sampled patch coverage, material provenance and validity notes.

### Connect a local MCP client

Use the installed `makersim-mcp` executable as a stdio server. For clients that
accept a `mcpServers` configuration:

```json
{
  "mcpServers": {
    "makersim": {
      "command": "/absolute/path/to/MakerSim/.venv/bin/makersim-mcp",
      "args": []
    }
  }
}
```

On this checkout the command is
`/home/legion/Projects/python-playground/MakerSim/.venv/bin/makersim-mcp`.
Replace the path for other machines. The server requires no HTTP listener,
browser session or AI model download. Use absolute paths in tool arguments so
the client's working directory does not affect file selection. MCP operations
return structured JSON; operation errors also set `isError: true`.

| Tool | Purpose |
| --- | --- |
| `get_workflow` | Read the automatic iteration, capture, human review and branching procedure. |
| `get_test_schema` | Discover the versioned test-file format. |
| `get_goal_schema` | Discover structured relative goals against a baseline. |
| `list_materials` | List material IDs or inspect one material's properties and sources. |
| `inspect_model` | Check STL units, original bounds and coarse-mesh readiness. |
| `validate_test` | Validate a saved specification without solving. |
| `run_test` | Solve all load cases, measure probes and optionally save a new report. |
| `get_result` | Read a saved report; `view="metrics"` omits large field arrays. |
| `query_field` | Retrieve bounded pages or spatial regions of saved surface samples. |
| `compare_runs` | Compare compatible reports, sampling fingerprints and effective stiffness changes. |
| `evaluate_goals` | Assess relative movement/stress/volume targets; optionally save an evaluation. |
| `check_convergence` | Run a bounded refinement study and report observed numerical stability. |
| `capture_result` | Render a saved full-field result into geometry, stress and movement images; return previews to the agent. |
| `create_review` | Assemble a portable visual history of revision reports with captures. |

### CAD → test → iterate

An agent can create or revise a part in Onshape, export its STL to a local file,
inspect the file, then run a saved MakerSim test. It can use movement, stress
concentrations and probe measurements to guide the next CAD revision. CAD export
and editing remain the CAD client's responsibility; MakerSim accepts the file.

Keep the test specification fixed across revisions. After exporting the next
STL, run it with that same test and a new report filename, then compare:

```bash
.venv/bin/makersim compare .makersim/runs/bracket-r1.json \
  .makersim/runs/bracket-r2.json
```

STL files have no unit metadata: set `stl_unit` to `mm` or `inch`. All fixture,
load and probe points and radii are **millimetres**. The default `source_mm`
coordinate frame preserves the original exported CAD axes after unit conversion;
`centered_mm` explicitly uses the browser solver's recentered frame. Inspection
and reports provide both bounds and the translation between them. Normals and
load directions follow the exported axes. Preserve the CAD origin and export
orientation across revisions; changing them requires updating the test locations.

Reports record STL and normalized-test SHA-256 hashes, an optional CAD revision,
the solver version, and material-property hashes. Comparisons reject changed
tests, material physics or solver versions. A matching test does not establish
mesh convergence: geometry changes can alter voxel sampling and contact coverage.
Read the reported patch coverage and validity notes. The small-displacement flag
uses a 2% maximum-span heuristic, not a validated applicability limit.
Calculated displacement criteria do not establish failure or safe working loads;
the existing [physics limits](#physics-and-limits) apply to agent runs too.

### Relative goals and compact queries

A goal string records intent. For numerical targets such as **at least 30% less
tip movement and at most 20% more volume**, save a separate goals file:

```json
{
  "schema_version": 1,
  "requirements": [
    {"metric": "surface_probe_max_displacement_mm", "load_case": "downward",
     "probe": "tip", "operator": "at_most", "change_percent": -30},
    {"metric": "solid_volume_mm3", "operator": "at_most", "change_percent": 20}
  ]
}
```

Use the exact case/probe names from your test. `max_displacement_mm` and
`peak_surface_stress_mpa` are also supported; `at_least` supports lower bounds
on percentage change. Run `makersim goal-schema` to discover the format.

```bash
.venv/bin/makersim evaluate baseline.json candidate.json --goals goals.json \
  --output .makersim/evaluations/candidate.json
.venv/bin/makersim result candidate.json --view metrics
.venv/bin/makersim field candidate.json --case downward \
  --field surface_stress_mpa --limit 50 --bounds 10 -12 15 35 12 40
```

Evaluation reports `met`, `not_met` or `not_assessed` separately from the test's
absolute limits and numerical confidence. A zero baseline makes percentage
change undefined and is not assessed. Percentage comparisons allow 1e-9 percentage
points for floating-point roundoff. A calculated goal can be met without mesh
convergence being verified.

`get_result` keeps its existing full-report default; use `view="metrics"` for
compact agent reads, with a field availability/count summary. `query_field`
accepts a case, field, offset, limit (1–1000), and optional `bounds_mm` as two XYZ
corners in report coordinates. CLI bounds use six values. Pages return original
surface indices, positions, values and `next_offset`; only matching nodes count
toward offsets. Queries preserve the report and do not rerun the solver.

New reports fingerprint selected coordinates to 1e-9 mm for every fixture,
load and probe, plus normalized load weights. Comparisons expose `sampling_status`
and per-selection changes, actual coverage and effective XY/build modulus and
solid-fraction changes. Identical material and print settings can still produce
different effective stiffness because the print-property approximation uses
geometry. Legacy reports remain readable; identical bounds/counts without
fingerprints give **unknown** sampling identity.

### Mesh refinement and numerical confidence

Local runs accept `--resolution` (8–100 cells along the longest dimension) and
`--max-cells` (occupied-cell budget, up to 20000). Defaults remain 28 and 4800.
Reports record requested and actual cell sizes and any budget-driven coarsening.
Use `--strict-pitch` to fail when the requested cells exceed the budget.

```bash
.venv/bin/makersim converge candidate.stl --test test.json \
  --output-dir .makersim/convergence/candidate --resolutions 28 35 44 \
  --max-cells 12000 --displacement-tolerance 5 --stress-tolerance 10
```

`check_convergence` runs 3–6 increasing resolutions with strict budgets, saving
each immutable report and `convergence.json`. It checks global displacement,
every probe and peak stress independently. `within_tolerance` requires the last
two successive changes to be within each tolerance and actual cell sizes to
decrease at every level. Otherwise it returns `not_converged`, or `not_assessed`
if actual meshes did not refine. A budget/solver failure publishes no study.
Sampling comparisons explain changes in fixture/load/probe coverage. Assess
competing designs at common mesh settings; one finer solve is not convergence
evidence. Observed mesh stability does not validate printed-material assumptions.

### Capture revisions and branch from a chosen design

Each test can automatically save four PNGs per load case: plain geometry for
aesthetic review, stress from two angles, and movement at an explicit display
scale. Rendering uses a local CPU backend, with no browser or display server.
The capture directory contains an image manifest, a full-field report copy for
later recapture, and a standalone `index.html` with embedded images.

```bash
.venv/bin/makersim run part-r4.stl --test test.json \
  --output .makersim/runs/r4.json --revision R4 \
  --images .makersim/captures/r4 --stress-max 1 --movement-scale 100 \
  --branch stiffness --goal "Reduce movement within the volume budget"
```

Choose a common `--stress-max` in **MPa** for the revision series. Without it,
each report uses its own largest surface stress for the legend; those colors
cannot compare magnitudes across reports. Movement defaults to 1× actual scale;
use the same explicit multiplier for comparison images. Images label the
physical movement separately from its display multiplier. The stress pictures
show interpolated surface von Mises concentration, not stress-direction flow
lines or failure predictions. They are saved renders, not desktop screenshots.

For an existing report saved with `--fields`:

```bash
.venv/bin/makersim capture .makersim/runs/r4.json \
  --output-dir .makersim/captures/r4 --stress-max 1 --movement-scale 100
```

Captures use tighter framing and orthographic projection by default. Set
`--elevation`, `--azimuth`, `--zoom` (0.5–2), or `--projection persp` on `run` or
`capture`. Camera settings are saved in the manifest. Add underside/detail views:

```bash
.venv/bin/makersim capture .makersim/runs/r4.json \
  --output-dir .makersim/captures/r4-details --stress-max 1 \
  --elevation 30 --azimuth -120 --zoom 1.2 \
  --views geometry geometry_underside stress_underside stress_detail \
  --detail-bounds 10 -12 15 35 12 40
```

MCP accepts `camera={"elevation":30,"azimuth":-120,"zoom":1.2}`, `views`,
and `detail_bounds_mm=[[10,-12,15],[35,12,40]]`. Detail views crop to that region
in report coordinates. Default captures still contain four views per load case.

The original report is preserved; the new capture bundle has its own
`report.json`. If the STL has moved, use `--stl`; its hash must match the saved
revision. Compact reports without fields need a new run with `--images`.

Review geometry and behavior together, then choose any revision as the parent
of a new branch. For example, if R4 looks better for its intended use than R7:

```bash
.venv/bin/makersim review .makersim/runs/r4.json .makersim/runs/r7.json \
  --output .makersim/revision-review.html
.venv/bin/makersim run part-r4-a1.stl --test appearance-test.json \
  --output .makersim/runs/r4-a1.json --revision R4-A1 \
  --parent-report .makersim/runs/r4.json --branch appearance \
  --goal "Improve appearance while retaining acceptable stiffness" \
  --images .makersim/captures/r4-a1 --stress-max 1 --movement-scale 100
```

The review command accepts reports containing captures, including a capture
bundle's `report.json`. Two revision selectors let you inspect any pair side by
side. It embeds its PNGs so the HTML can be shared independently
and flags differing stress legend scales. MCP capture operations return geometry
and stress images directly as well as file paths, allowing an agent with vision
to inspect what it created.

Parent references record the saved report hash, CAD revision, STL hash and test
hash. Existing revisions and captures are never overwritten. Branch labels and
goal text document design intent; they do not select the preferred design or
change numerical criteria. Set numerical limits in the test specification. If
test conditions or limits change, rerun the chosen parent's STL with the new
test as the branch baseline, then compare its children under that same test.

## Installation

The source launcher uses Bash, Python 3.11+, Node.js 20.19+, npm, and
[uv](https://docs.astral.sh/uv/). A browser with WebGL is required for the viewer.

```bash
git clone https://github.com/RawLabs/MakerSim.git
cd MakerSim
uv sync
./scripts/start.sh
```

The launcher installs Python dependencies with `uv` when needed, installs the
frontend dependencies when absent, builds the frontend, and serves the workspace
and solver from one local Python process. Existing services on other ports are
left running. The source launcher is intended for Linux and other environments
with Bash; a native Windows launcher is not included.

The local launcher runs on `127.0.0.1`. Uploaded parts stay in the local process.
Each browser retains its last two uploads, within a six-model server budget;
models expire after two hours and disappear when the process stops. Dependencies
need internet access for their initial installation; Three.js is bundled locally.

### Hosted playground

Visit [makersim.rawcastdigital.com](https://makersim.rawcastdigital.com/) for the
live demo and server availability. If the server is offline, 
[request a testing session through ShftState](https://shftstate.rawcastdigital.com/#contact).

There is no account or database. A random HTTP-only browser cookie identifies
each temporary workspace; another workspace cannot solve or evict its parts.
Visitors should use non-sensitive designs. Multipart parsing can use temporary
files; models and results are not saved as permanent documents. Hosted parts
expire after 15 minutes and a one-minute cleanup removes expired models from
memory. The trial admits twelve models globally and two per workspace, two
concurrent uploads, and one simulation at a time. Uploads are at most 20 MB;
request bodies, mesh sizes, and requests per client are bounded.

## Demo screens

Captured from the actual application with the real local solver. The example
geometry and loading are a demonstration, not a validated bracket design.

### Load-path preview

![MakerSim showing the backpack bracket with a relative stress heatmap and a downward pull](artifacts/workspace-heatmap.png)

Warm colors identify concentrated stress within this run. Colors rescale with
each simulation and are not a failure scale.

### Small hook: simulation and real life

<p align="center">
  <img src="artifacts/MSRealLifeTest.png" alt="MakerSim poster comparing a small hook's simulated stress preview with a photograph of the permanently bent printed hook" width="680">
</p>

The physical hook retained a bend after holding a sweater and a pair of pants
overnight. Its material was believed to be PETG; the load was not weighed, and
the hook was subsequently heated in an attempt to reshape it. This is a
qualitative comparison, not a controlled validation. MakerSim shows an elastic
response with exaggerated movement; it does not model creep, temperature effects,
or permanent deformation over time.

<details>
<summary>Start a new part — center upload prompt</summary>

![MakerSim's initial workspace with one center upload prompt and print setup on the left](artifacts/workspace-empty.png)

</details>

<details>
<summary>Mobile workspace</summary>

<p align="center">
  <img src="artifacts/workspace-mobile.png" alt="MakerSim mobile layout with the viewer above part and load settings" width="390">
</p>

</details>

[Quick start](#quick-start) · [Physics and limits](#physics-and-limits) ·
[Verification](#verification) · [Logo and brand sheet](artifacts/MakerSimLogos.png)

## Current playground workflows

| In the workspace | What MakerSim helps you do |
| --- | --- |
| **Your part** | Load binary or ASCII STLs, check dimensions and units, and replace the part. |
| **Your print** | Choose filament, walls, solid layers, infill, and the build axis. |
| **Hold & pull** | Paint several held areas and place a distributed load patch with a draggable arrow. |
| **Add a little weight** | Use newtons, pounds-force, kilograms of hanging mass, or stone of hanging mass. |
| **Simulate** | Read applied force, calculated maximum movement and peak surface stress; explore relative stress and movement at a fixed, adjustable display scale. |

The viewer supports orbit, zoom, pan, camera fit, grid visibility, and optional
slow rotation. Changing material, print settings, holds, direction, or load clears
the previous result so the displayed preview matches the current setup.

STLs do not declare their units. MakerSim defaults to millimetres; choose inches
when appropriate and check the displayed dimensions. Files can contain up to
20 MB and 120,000 triangles. Open surfaces can be viewed but must be repaired
before solving. Simulation requires one connected, closed solid with consistent
triangle winding. Inconsistent triangle directions produce a repair message
before they can affect the print-property estimate. A hollow solid may have
separate closed surfaces around enclosed cavities; those are accepted after
containment checks. Disconnected solids, overlapping cavities, and touching
or crossing cavity surfaces remain rejected.

## Physics and limits

The solver classifies the closed STL with triangle scanlines, builds a bounded
voxel volume of up to 4,800 cells, and divides each cell into six conforming
linear tetrahedra. If coarse sampling splits a connected part or misses its
thickness, meshing retries with smaller cells within the same budget. Actual
separate solids remain rejected; meshing does not fill gaps or discard pieces.

The model assembles a three-dimensional orthotropic elasticity matrix in
**mm, N, MPa** and solves free degrees of freedom with SciPy. Held patches
constrain all three translations. Load clicks expand to at least a mesh-cell
surface patch and distribute the total force over several surface nodes.
Overlapping holds/loads, floating pieces, incomplete rigid-body constraints,
and excessive equilibrium residuals produce actionable errors.

Surface stress is a volume-weighted nodal von Mises scalar used to visualize
concentration, not an anisotropic failure criterion. The frontend interpolates
the field onto a subdivided version of the original STL. Each run rescales
colors using its 98th percentile, so colors alone cannot compare load magnitudes
across runs. Applied force (N), calculated maximum movement (mm), and peak surface
stress (MPa) are displayed separately. Movement defaults to actual scale (1×),
with explicit reduction or exaggeration options from 0.001× to 10,000×. The
chosen display scale stays fixed across reruns, so increasing a load tenfold
produces tenfold displayed movement under the same material and constraints.
Replacing the part resets the display scale to 1×. These are linear-elastic
calculations; large movements do not predict the real deformed shape.

### Mesh approximation

This is a staircase volume approximation rather than a boundary-conforming
tetrahedral mesh. Features smaller than roughly two cells can disappear; small
bolt holes and thin walls need particular care. There is no mesh-convergence
guarantee, and first-order tetrahedra can be overly stiff in bending. Selected
contact areas also approximate the coarse volume. A part whose connections
cannot be resolved within the mesh budget needs thicker features or simpler
geometry for this preview.

One default-mesh cantilever benchmark understated bending movement by about 23%
against its beam-theory reference using the meshed dimensions. Error varies with
geometry; this is not a correction factor for other parts.

### Print assumptions

Print settings use a heuristic effective solid fraction from walls, solid layers,
and infill. V1 assumes 0.45 mm extrusion width, 0.20 mm layers, and balanced
in-plane rasters. A symmetric positive definite compliance model uses separate
XY/Z moduli and in-plane shear stiffness; build-axis shear is estimated.

The build direction can be X, Y, or Z. Arbitrary rotations, explicit rasters,
individual strands, slicer toolpaths, and delamination are not modeled. Flexible
materials and large movements are especially approximate. There are no failure
predictions, certified loads, yield/plasticity, creep, temperature effects,
contact, buckling, impact, or large-deformation analysis.

## Material sources and provenance

[`backend/data/materials.json`](backend/data/materials.json) and
[`makersim_filament_master.csv`](backend/data/makersim_filament_master.csv) are
preserved from the T7 MakerSim archive without importing its architecture.
[`backend/materials.py`](backend/materials.py) maps catalog entries into a small
physics schema with explicit XY/Z Young’s modulus, XY shear modulus, Poisson
ratio, density, and optional directional strengths. The solver uses stiffness,
not strength thresholds.

The 12 generic FDM baselines in
[`fdm_baselines.json`](backend/data/fdm_baselines.json) are adapted from
[ichris97/multimaterial-3d-printing](https://github.com/ichris97/multimaterial-3d-printing/blob/master/src/multimaterial_3d/core/materials.py),
file SHA `91a9e899c542041058a8ba835b2256e969720563`. They represent well-tuned,
solid prints rather than verified product measurements. The upstream MIT notice
is retained in [`BASELINE_LICENSE`](backend/data/BASELINE_LICENSE). Its CLT,
post-processing, failure estimates, and geometry-only stress maps are not imported.
Raster and geometry-precheck ideas remain deferred.

Unsupported archive entries remain visible but cannot be simulated when usable
stiffness is absent or continuous reinforcement exceeds this model. Missing Z
stiffness and shear are labeled assumptions; missing tensile strength is not
manufactured. Archived values have not been reverified against current datasheets.
Future measurements should override individual fields with their source and
print conditions.

External catalogs and printed-test corrections are separate future inputs.
References include [Open Filament Database](https://api.openfilamentdatabase.org/),
[OpenPrintTag](https://github.com/OpenPrintTag/openprinttag-database),
[FilamentCat](https://filamentcat.com/en), and
[3DPIceland](https://www.iskort.is/3dp/). V1 does not automatically ingest them:
identity matching, permitted reuse, units, test conditions, and field provenance
need a deliberate adapter.

## Development

Run the backend with reload enabled:

```bash
uv sync
.venv/bin/python -m uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000
```

In another terminal, run the Vite frontend:

```bash
cd frontend
npm install
npm run dev
```

The frontend opens at **http://127.0.0.1:5173** and proxies `/api` to Python.

| Path | Contents |
| --- | --- |
| `backend/` | Local CLI/MCP, local and hosted API, STL parsing, material adapter, and elasticity solver. |
| `backend/data/` | Example STL, archived materials, generic baselines, and their license. |
| `frontend/` | Vite workspace, UI, viewer, and bundled Three.js. |
| `deploy/` | Static landing page, 3D hero sources, and isolated VM deployment files. |
| `scripts/` | Launcher, checks, example generation, and browser QA transport. |
| `tests/` | Solver, API, launcher, and viewer interaction checks. |
| `artifacts/` | Brand sheet, landing hero, application screenshots, and the real-life hook comparison poster. |

## Verification

From the repository root:

```bash
./scripts/check.sh
```

This runs the Python suite, real Three.js interaction checks integrated with
solver output, and the production build. The Python suite includes affine
tetrahedral strain, an analytical axial bar, equilibrium,
tenfold-load scaling, orientation, print settings, retained holes, patch spreading,
thin-feature refinement, enclosed cavities, shell containment and intersection,
mesh-budget limits, mixed winding, extreme direction
vectors, nonfinite JSON validation, atomic model storage, browser workspace
isolation, streamed body limits, hosted rate limits, and upload concurrency.
Local agent checks cover CAD coordinate preservation, multi-load and probe
measurements, artifact preservation, report compatibility, CLI execution outside
the checkout, local PNG capture and review, branch lineage, and a real MCP
client/server exchange over stdio, including image previews.

In-process HTTP tests run worker functions inline because the restricted build
environment blocks cross-thread wakeup sockets; those tests do not verify worker
concurrency. The stdio MCP test uses a real subprocess and requires worker wakeup
sockets; run the checks in an environment that permits them. The Node interaction
checks exercise actual geometry and raycasting
without WebGL.

For browser verification with Playwright and Chromium installed:

```bash
MAKERSIM_CHROMIUM=/usr/bin/chromium node scripts/browser-smoke.mjs
```

`MAKERSIM_PLAYWRIGHT_MODULE` can point to an existing Playwright module. The
harness serves built files through browser request interception and connects to
the real API in-process. It checks center upload, left-side replacement, painted
holds, arrow dragging, solving, reruns, unit conversion, help, movement, and mobile
layout. Full WebGL browser checks pass. Chromium needs socket access and may need
to run outside a restrictive sandbox. This intercepted harness does not verify
a live localhost deployment.

The offline revision review has its own optional browser check:

```bash
node scripts/review-smoke.mjs .makersim/revision-review.html
```

Use the same `MAKERSIM_PLAYWRIGHT_MODULE` and `MAKERSIM_CHROMIUM` overrides when
needed. An optional second argument saves a screenshot of the comparison.

## License and third-party notices

A project-wide license for MakerSim has not yet been selected. The supplied
logo and artwork are not granted a separate reuse license by this repository.

Bundled third-party components retain their own licenses:

- Three.js r180 is vendored from the
  [official Three.js repository](https://github.com/mrdoob/three.js/tree/r180),
  with its [MIT license](frontend/src/vendor/LICENSE) retained locally.
- The generic FDM baselines retain their upstream
  [MIT notice](backend/data/BASELINE_LICENSE).

The original [logo and brand sheet](artifacts/MakerSimLogos.png) is included
alongside the screenshots and supplies the application header artwork.
