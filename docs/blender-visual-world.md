# Blender visual world

Blender is an opt-in, non-authoritative representation adapter above the existing
MuJoCo LocalVerifier. It advertises no safety claims and does not enter Registry
verification planning. VisualRuntime instruments the ordinary Runtime event stream:
it saves source predictions and renders before selection/dispatch. After the ordinary
Core Runtime returns its durable finished result, VisualPostprocessor reads saved
Outcome/Prediction/Receipt records and renders only executed candidates. Core is unchanged.

## Installation and use

Install the locked `physical` and `dev` extras. No new host Python dependencies.
Install the official Blender 4.5 LTS binary from https://download.blender.org/release/Blender4.5/
and verify the published SHA256. On macOS, mount the official Apple Silicon DMG,
copy Blender.app and set PREACT_BLENDER to Contents/MacOS/Blender. Linux can extract
the official x64 archive. Blender has its own Python, bpy and NumPy; it runs in an
independent background process, never in the PreAct interpreter.

```bash
PREACT_BLENDER=/path/to/blender uv run python -m scripts.bench_blender_visual \
  --output .cache/NEW-blender-evidence
uv run python -m scripts.audit_blender_visual .cache/NEW-blender-evidence
```

The output path must be new. Open gallery.html after benchmark completion. It is
built from saved Prediction/Artifact/Receipt records, with embedded previews; no
frontend contracts change. Default benchmark protocol is benchmarks/blender-visual-v2.json.
Frozen v1 retains the initial PNG metadata reproduction failure and a three-step
incomplete transfer. v2 permits five Runtime steps; neither protocol is rewritten.
Blender integration tests are opt-in through PREACT_BLENDER; tests without the
variable exercise contracts/failures only. They do not establish real rendering.

## Geometry, coordinates and image semantics

Both systems use right-handed XYZ, Z up, and metres. Translation is identity.
MuJoCo box `size` is half extent; Blender cube size=2 scaled by half extents.
Obstacle centers and sizes, additional obstacles and cube_half come from State.
The floor top is z=0; the finite 4m square visual slab approximates MuJoCo's infinite
plane. The target is a green goal marker at the exact State target center, half
extents (0.04, 0.04, 0.002); it is not a physical collision object. The gripper is
a radius .018m sphere at State.object, without an artistic offset. State does not
retain mocap position or cube free-joint orientation: these are explicitly position
proxies / axis-aligned glyphs. Intermediate frames reuse actual retained MuJoCo
position samples, not interpolated physics. Render-only intermediate hypotheses
retain the initial scene metadata and replace position only; they are not complete
MuJoCo dynamic states. No rotation fidelity is claimed. The small gripper proxy may
be fully occluded inside the cube; its stable ID remains in the scene manifest.

Camera is fixed, perspective, horizontal sensor fit, square pixels. Intrinsics K
and world-to-camera CV transform are recorded per frame from Blender. CV axes are
right/down/forward; Blender camera local axes are right/up/back. Pixel coordinates
use top-left origin, boundaries [0,width] and [0,height], centers x+.5,y+.5.
Depth is float32 camera axial Z in metres, not radial range, with background 0.
Segmentation is uint16 stable object ID, background 0; invisible objects retain
manifest IDs. Depth and ID are unfiltered pixel-center ray intersections against
the evaluated Blender geometry. RGB uses actual Cycles CPU rendering with
antialiasing, Standard color transform, fixed seed/samples/threads, no denoising or
adaptive sampling. Edge samples can differ from pixel-center depth/segmentation.
Lighting, exact settings, version/build, worker hash and elapsed/resource usage are
recorded. PNG variable text metadata (date and timing) is omitted without changing
RGB pixels; those records stay in the manifest. Byte reproduction is tested on one binary/platform; cross-version or
cross-platform bit identity is not promised.

## Trust and failure boundaries

Every State, Action, MuJoCo Prediction, manifest and modality is content addressed
through existing Artifacts. Readers verify all hashes. Visual Prediction uses
EvidenceKind.VISUAL with unknown probabilities and no checks. Blender cannot
certify physics, pass Gate or overwrite Observation. Rendered observed outcomes
are synthetic too; real-world MuJoCo model mismatch remains unknown.

Input is bounded JSON, not Python/blend files. Worker code is fixed and packaged.
Requests restrict geometry, camera, frames (24), pixels (512x512), samples (64),
threads (8), input size (1MiB), output files (16MiB), total output (256MiB), and
host timeout (<=600s). Autoexec is disabled. Private job directories are removed
on completion/failure/cancellation; the process group is killed and drained before
return. No successful manifest is published for incomplete/error/timeout output.
This is a process boundary for owned Blender binaries, not an OS sandbox for a
malicious executable. An opt-in prerequisite render may prevent execution. Post-execution visual errors
are recorded independently after Core finishes, without changing the run result,
receipt, observation, calibration or trust; they are never physics success.

## Audit and evaluation scope

The separate auditor reads saved sources and durable Store records, reconstructs
scene translations, checks trace/state/action binding, matrices and object geometry,
and independently intersects analytic boxes/spheres for dense depth/ID comparison.
Sphere mesh approximation and silhouette ties use declared tolerances. It checks
actual outcomes only for complete receipts and verifies predictions precede the
selection. It does not infer outcomes of unexecuted alternatives. Development,
calibration and held_out samples come from split_scenes; this bounded engineering
benchmark is not generalization or real-camera validation. Results retain failures.

## Executed local acceptance

The frozen v2 evaluation used Blender 4.5.9 LTS build 8bf95cbd38d1 on Apple Silicon,
Cycles CPU, 192x144, 8 samples, seed 7 and two render threads. All 12 selected scenes
across development/calibration/held_out translated, rendered and passed the separate
analytic audit. 29 jobs produced 57 frames. The ordinary Runtime rendered seven
candidates, executed four with complete receipts, and completed the transfer safely;
three unexecuted alternatives have no outcome. All four aligned final-position,
projection, depth and segmentation differences were zero; this is deterministic
same-model agreement, not real-world MuJoCo validation.

Independent box depth max error was 1.8051e-6 metres; segmented pixels exceeded no
independently projected bounds. Reproduction matched exact RGB/depth/ID bytes in
separate Blender processes. Peak renderer RSS was 247,087,104 bytes, total worker
CPU 50.064837 seconds, render time 14.053791 seconds, and mean process startup/shutdown
0.853832 seconds. The retained corpus has 182 hash-verified artifacts (about 7.4MB).
Expected real-worker error, timeout and cancellation all failed without success;
the integrity probe rejected tampering. Incomplete-output failures use explicitly
separate process-stub tests, never real-render success claims.

See [measured summary](../reports/blender-visual-v2-results.json) and
[actual rendered preview](../reports/blender-visual-preview.png). Raw replay inputs,
ledger, receipts, modality arrays, manifests and complete gallery remain in the
excluded `.cache/blender-final-evidence/` bundle; use the commands above to reproduce.
The independent auditor requires those original inputs, not the abbreviated summary.

Acceptance uses flat root comparison (`Policy(search=False)`). Deeper hypothetical
Future Tree input states are outside this adapter's audited scope. Renderer memory
is recorded as peak RSS; Linux also enforces a 4GiB address-space ceiling. CPU time
is bounded to 4800 seconds and host wall time to 600 seconds at most. Process startup
and shutdown are reported together as measured overhead, separately from render time.

Full fixed-source Python regression passed 753 tests (3 opt-in Blender cases skipped,
then passed with actual Blender separately). The targeted contract + real-Blender
suite passed 63 tests; Frontend E2E passed 6. Ruff check/format, frontend contracts/
build and current-source wheel/sdist integrity audit passed. The checksum-pinned
Ubuntu Blender CI job also passed actual rendering and the independent replay audit.


## Post-execution isolation and retries

Postprocessing runs after the Core episode finishes, outside Core's episode timeout
and exception handlers. Observation evidence, physical prediction comparison,
calibration rows, trust updates, final observation, run result and `finished` are
already durable before any post-execution Blender process starts. No Core, Gate,
Registry, World or execution-store contract changes were needed.

Each attempt binds run, node, receipt, outcome sequence and original visual Prediction
artifact to a deterministic job key and a unique attempt ID. The processor revalidates
complete receipts, source artifacts, State/Action fingerprints, prediction event and
camera/render settings before rendering. `visual_postprocess_failed` records operation,
exception/cause type, message, artifact references, missing modalities/comparison,
retryability and process cleanup. No successful artifact is invented on failure.
Ordinary errors are reported per receipt; other receipts can continue. Cancellation
is recorded and re-raised to the caller **outside Core**, so the saved Core result
remains complete. Diagnostic persistence failures also propagate without rewriting Core.

Retry with `await runtime.postprocessor.run(runtime.run_id)`, or construct
`VisualPostprocessor(store, artifacts, adapter)` against the original saved Store.
Keep the original camera/settings and serialize external writers; reuse one processor
for concurrent callers. Cross-process coordination is outside this adapter's scope.
Valid existing renders are reused, missing comparisons are resumed, complete jobs
are revalidated without adding duplicate success events. Corruption, conflicting
bindings or duplicate records fail closed. No World action, calibration update or
receipt transition is performed by retry. Cancellation after a durable comparison
commit records `comparison_committed=true` and reconciles on retry.

`audit_postprocessing` independently checks Core completion and diagnostic bindings;
it does not certify missing visual output. Normal `audit_run` still requires every
executed comparison and rejects incomplete rendering. The gallery displays failed
attempts separately from authoritative Core outcomes and synthesized result images.

The repair regression first failed on the original implementation: a safe MuJoCo
release completed its receipt, then the injected post-render exception prevented
all following Core evidence/comparison/calibration/trust/finish events. The same
regression now passes. Actual Blender tests also cover post-render timeout/cancel,
partial-comparison retry, cancellation after commit, wrong receipt and artifact tamper.
The targeted suite passed 73 cases (8 use real Blender). Re-evaluation with unchanged
frozen v2 again rendered 57 frames in 12 scenes, selected/executed 4 of 7 candidates,
completed safely and independently audited 182 artifacts. All four aligned position,
projection/depth/segmentation differences remain zero in the same MuJoCo model.
See [new measured report](../reports/blender-postprocessing-v1-results.json).
Earlier frozen reports and the initial reproduction/trial bundles remain preserved.
