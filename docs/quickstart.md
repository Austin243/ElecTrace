# Quick start

This guide exercises ElecTrace safely before you point it at scientific data. Commands
describe the intended 0.1 interface; `electrace COMMAND --help` is authoritative for
the installed revision while the project remains pre-release.

## 1. Install from a checkout

ElecTrace supports CPython 3.11–3.13.

```bash
git clone https://github.com/Austin243/ElecTrace.git
cd ElecTrace
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
```

This installs the runtime package without development tooling. Contributors should
instead follow the editable setup in `CONTRIBUTING.md`.

The base package stays lightweight by using ElecTrace's bundled deterministic
assignment solver. Install `electrace[accelerate]` to prefer SciPy's compiled solver
for larger analyses, `electrace[hdf5]` to write HDF5 output, or `electrace[all]` for
both optional runtime features. ElecTrace's VASP readers do not require ASE.

## 2. Diagnose the environment

```bash
electrace doctor
```

`doctor` should distinguish required Python capabilities from optional external
adapters. A missing critic2, BaderKit, LOBSTER, or Chargemol executable is expected
for users who only want the built-in demo or who are importing already-normalized
records. Without a configuration it checks the runtime and adapter executables; after
initialization, `electrace doctor --config electrace.toml` deterministically checks
every discovered frame and its required plugin inputs.

With `--json`, unconfigured executable checks appear in the `data.plugins` array. A
configured path check uses `data.frames`, whose entries identify each frame and contain
that frame's plugin reports; consumers should not mistake an empty `data.plugins` array
for a skipped configured-path check.

## 3. Run the deterministic demo

```bash
electrace demo --output .electrace-demo
electrace inspect .electrace-demo/electrace-results
```

The demo is a software smoke test, not a scientific benchmark. It should use small
synthetic or redistributable fixtures and should not require licensed software,
network access, or a DFT calculation.

Run the more adversarial, labeled regression suite separately:

```bash
electrace benchmark --output electrace-benchmark.json
```

This exercises periodic wrapping, controlled coordinate and cell perturbations,
event labels, conservation negative controls, semantic gates, and ambiguity handling.
It is still synthetic and therefore does not establish scientific accuracy. See the
[benchmark guide](benchmarking.md) for the exact scoring contract and limitations.

Record the exact command and installed revision when filing a bug:

```bash
python --version
electrace --version
electrace doctor
git rev-parse HEAD
```

## 4. Initialize a VASP path

The image root should contain deterministically ordered directories such as `00`,
`01`, …, each with a consistent atom list and a readable `CONTCAR` or `POSCAR`.

```text
neb-images/
├── 00/
│   └── CONTCAR
├── 01/
│   └── CONTCAR
└── 02/
    └── CONTCAR
```

Create a project configuration:

```bash
electrace init electrace.toml --images /absolute/path/to/neb-images
```

Review the generated file. A representative configuration is:

```toml
[project]
name = "Example NEB pathway"
series_kind = "neb"

[inputs]
root = "/absolute/path/to/neb-images"
descriptor_source = "auto"
structure_name = "CONTCAR"
fallback_structure_name = "POSCAR"
descriptor_name = "features.json"
prefer_contcar = true

[plugins.critic2]
enabled = true
executable = "critic2"

[plugins.baderkit]
enabled = false
executable = "baderkit"

[plugins.lobster]
enabled = false
executable = "lobster"
```

`inputs.descriptor_source` makes descriptor discovery explicit:

- `auto` is compatibility behavior for configurations that omit an explicit policy:
  it uses frame-local descriptors without `descriptor_dir` and external descriptors
  when that directory is present;
- `local` reads only each frame directory and ignores `descriptor_dir`;
- `external` requires `descriptor_dir` and reads only that tree;
- `merge` requires `descriptor_dir`, combines local and external records, and rejects
  duplicate feature IDs rather than silently choosing a source.

The structure and descriptor filenames must be basenames, not paths. The loader
recognizes canonical `features.json` and `electrace.features.json` records as well as
supported native-tool outputs. A plugin's `enabled` setting affects only diagnostics
and command planning; it never hides evidence that has already been produced.

Use absolute input paths for long-lived analyses, or record the project-root-relative
base unambiguously. Do not edit, reorder, or overwrite source calculations during an
analysis.

## 5. Inspect a plugin command plan

ElecTrace does not implicitly execute external scientific programs. Ask it for a
shell-free plan for one plugin, frame, and capability:

```bash
electrace plan critic2 /absolute/path/to/neb-images/03 \
  --capability qtaim_critical_points --config electrace.toml --json
```

Generate a deterministic plan for every discovered frame by passing the path root:

```bash
electrace plan critic2 /absolute/path/to/neb-images --all-frames \
  --capability qtaim_critical_points --config electrace.toml --json
```

With `--json`, the one-frame or batch plan is returned under `data` in an
`electrace.cli.v1` envelope. Batch `data.plans` remains in deterministic frame order.
Each element contains a canonical `frame` record and a `command` that follows the same
command-plan contract as the one-frame form.

The command prints JSON containing the argument vector, working directory, standard
input, required inputs, and expected outputs. Review it before an authorized workflow
executes anything. A plan is an audit record, not evidence that the tool ran or that
its output is valid.

critic2 plans request a standardized `critic2.features.json` through `CPREPORT`.
ElecTrace does not treat the human-readable raw `.cro` stream as normalized evidence.

## 6. Optional: orchestrate external calculations

`electrace plan` remains non-executing. If you instead want ElecTrace to stage and
explicitly run an authorized VASP→descriptor job graph, create a separate workflow
manifest:

```bash
electrace workflow init electrace-workflow.yaml \
  --images /absolute/path/to/neb-images
# Edit and review the YAML, executable paths, templates, frames, and scheduler.
electrace workflow validate electrace-workflow.yaml --json
electrace workflow stage electrace-workflow.yaml
electrace workflow status electrace-workflow.yaml --json
```

`workflow validate` resolves the complete selected job graph without creating the
workspace or writing any file.

For `scheduler.kind: local`, execution requires an explicit gate:

```bash
electrace workflow run electrace-workflow.yaml --yes --max-parallel 2
```

For `scheduler.kind: slurm`, first prepare and review scripts without submitting,
then repeat with authorization:

```bash
electrace workflow submit electrace-workflow.yaml --job-name electrace
electrace workflow submit electrace-workflow.yaml --job-name electrace --yes --force
```

After the expected descriptor outputs exist, isolate and analyze them:

```bash
electrace workflow collect electrace-workflow.yaml
electrace workflow analyze electrace-workflow.yaml
electrace inspect .electrace-work/analysis/results
```

`workflow analyze` performs collection when needed and produces a dedicated analysis
configuration and report below the marker-protected workspace. It is different from
top-level `electrace analyze electrace.toml`, which reads an existing TOML project
and never launches an external scientific program.

Read the [external workflow guide](workflows.md) before using `--yes`. It provides a
complete YAML manifest, frame-slice syntax, template rules, collection layout,
BadELF grid requirements, LOBSTER prerequisites, and the exact boundary between
collected evidence and scientific validation.

## 7. Analyze an existing project and inspect it

```bash
electrace analyze electrace.toml
electrace inspect electrace-results
```

With the default output settings, a successful run writes:

```text
electrace-results/
├── analysis.json
├── events.csv
├── feature_tracks.csv
├── manifest.json
└── report.html
```

`analysis.json` is the canonical analysis product. The tables and report are derived
views; `manifest.json` records the size and SHA-256 digest of each product.

Before interpreting results, confirm that:

- every expected frame was discovered in the intended order;
- atom counts, element ordering, and cells passed validation;
- the selected structure file is correct for every image;
- every enabled adapter reports its executable version and input hashes;
- warnings and partial plugin results have been reviewed;
- matching, persistence, descriptor, and minimum-confidence thresholds have been
  reviewed for the intended domain;
- conclusions survive appropriate grid, image-spacing, and DFT convergence checks.

## Troubleshooting

Run `electrace doctor` first. If the problem is reproducible with the built-in demo,
use the bug-report issue template and attach only non-sensitive logs. For a scientific
discrepancy, use the validation issue template and include the method, grid,
pseudopotentials, cell convention, and a minimal redistributable fixture when
licensing permits.

Never upload licensed pseudopotentials, proprietary executables, credentials, or
unpublished structures without authorization.
