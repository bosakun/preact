# Third-Party Inventory

Original PreAct code: Apache-2.0. Dependency versions are pinned in uv.lock and
web/package-lock.json. Preserve dependency notices when redistributing installations.

| Component | Purpose | License / release gate |
| --- | --- | --- |
| Python / FastAPI / Pydantic / httpx / pytest | Runtime, API, contracts, validation | Python PSF and package-specific permissive licenses; audit installed metadata before release |
| SQLAlchemy / psycopg | SQLite/PostgreSQL durability | MIT / LGPL psycopg terms; retain package licensing |
| NumPy / SciPy | Geometry, statistical calibration | BSD project licenses plus bundled binary-library terms; retain complete distribution notices |
| boto3 | S3 artifact storage | Apache-2.0 |
| mediapy and SDK-side helper dependencies | Isaac camera encoding and shared-source imports | Separately pinned in workers/isaac-bridge-requirements.txt; capture complete SDK binary/transitive notices after real worker setup |
| MuJoCo | Actual local Cartesian simulation | Apache-2.0; explicitly not NVIDIA |
| ConTree SDK/client | Token Factory Sandbox adapter | Apache-2.0; beta service admission required |
| React / React Flow / Vite / Lucide / Playwright | Future Tree, build, browser tests | MIT/ISC/Apache-2.0 package licenses; lockfile records versions |
| DM Sans / Manrope | Optional remote UI fonts | SIL Open Font License; [DM Sans notice](https://github.com/google/fonts/blob/main/ofl/dmsans/OFL.txt), [Manrope notice](https://github.com/google/fonts/blob/main/ofl/manrope/OFL.txt); system fallback works offline |
| Nemotron | Hosted NVIDIA reasoning | Check exact served model's model card/license and Token Factory terms before live release |
| Nemotron 3 Nano 4B / llama.cpp | Actual optional local reasoning and Metal runner | NVIDIA Nemotron Open Model License (Dec 15, 2025) / MIT; separately downloaded, not bundled or relicensed; see [local model](local-model.md) |
| Cosmos-Predict2.5 code/checkpoint | Conditioned visual futures | Code Apache-2.0; checkpoint NVIDIA model terms must be accepted and attributed separately |
| Isaac Sim / Franka assets | GPU simulation and robot | NVIDIA SDK/asset terms; not relicensed by PreAct |

The original checkout fixture and MuJoCo scene are repository-authored assets.
No third-party music, logos or sample robotics media is bundled. Hardware operation is
not claimed. The generated [dependency inventory](../reports/dependency-inventory.json) records
54 installed Python distributions, 106 locked frontend packages and installed notice hashes.
Regenerate with `uv run python scripts/dependency_inventory.py`. The original wheel declares
`License-Expression: Apache-2.0` and includes the root LICENSE. Three locked packages lack
structured license fields; reviewed notices are preserved here:

- [etils 1.14.0](licenses/etils-1.14.0.txt): Apache-2.0, copied from the installed distribution.
- [StrEnum 0.4.15](licenses/StrEnum-0.4.15.txt): MIT, copied from the installed distribution.
- [PyOpenGL 3.1.10](licenses/PyOpenGL-3.1.10.txt): complete multi-component notice from
  the [exact official PyPI source release](https://pypi.org/project/PyOpenGL/3.1.10/#files).
  Archive SHA-256 `c4a02d6866b54eb119c8e9b3fb04fa835a95ab802dd96607ab4cdb0012df8335`;
  notice SHA-256 `d37ec6e00a4f88bf80ec0b7b3b9dbea88534c1c66c2f529dc64c7c02e07a6a1b`.

Installed notice hashes remain in the inventory. Preserve upstream notices for the exact
redistributed binaries; a project-level license label does not cover every bundled library.
The application container also preserves these reviewed notices under `/app/docs/licenses/`.
This inventory is not a model/asset permission attestation or a certified SPDX/CycloneDX
SBOM. Verify exact model/asset terms at M11.

The optional conversion environment has its own exact package list in
`workers/local-model-requirements.txt`; those packages are not application dependencies
or included in the application inventory. Actual original/derived checkpoint and native
runner hashes are recorded separately. The official model repository's empty LICENSE file
does not replace its model card's governing NVIDIA license link; the PDF SHA-256 is
`2ffd837856bb99d4cee13d17f0b597ecfeb95c38e30abc08d3dffef7d589881d`.

The actual conversion environment now has a separate
[35-distribution inventory](../reports/local-model-conversion-inventory.json), matching
every exact requirement and hashing installed notices, including setuptools' vendored
components. Regenerate with `.cache/local-nvidia-env/bin/python
scripts/dependency_inventory.py --python-only --output reports/local-model-conversion-inventory.json`
on one line. The normal application inventory remains unchanged.

Five conversion packages lack structured license fields. Installed notices identify
gguf 0.19.0 and setuptools 78.1.0 as MIT, Jinja2 3.1.6 as BSD, and safetensors 0.8.0
as Apache-2.0. The tokenizers 0.22.2 wheel has no installed license notice; its exact
[official release license](https://github.com/huggingface/tokenizers/blob/v0.22.2/LICENSE)
is preserved in [tokenizers-0.22.2.txt](licenses/tokenizers-0.22.2.txt), with immutable
source revision and hash in [notice provenance](../reports/local-model-conversion-notices.json).
These are upstream main-license records, not a complete compiled Rust dependency
attestation. Conversion tools and model weights are downloaded separately; do not
redistribute their binaries without preserving their applicable full notices.
