# ElecTrace

ElecTrace tracks how electronic-structure features change across ordered periodic
structures, including VASP NEB images and static or pressure series. It combines
per-frame results from electronic-analysis tools with periodic atom and feature
matching to compare evidence across frames and produce a single analysis for the full
path.

ElecTrace can work with descriptor results that already exist, or it can stage and
launch configured calculations across selected frames through a local or Slurm
workflow.

## What it does

- Discovers ordered VASP frame directories and selects individual frames or slices.
- Reads `POSCAR`/`CONTCAR` structures and VASP `ELFCAR`/`CHGCAR` grids, and splits a
  spin-polarized `CHGCAR` into total and magnetization grids for Bader.
- Ingests supported Bader/BaderKit, critic2, LOBSTER, and Chargemol results, including
  per-atom spin populations and spin-resolved LOBSTER lists.
- Matches atoms and electronic features across periodic and changing cells.
- Builds feature correspondences and reports births, deaths, conservation-backed
  split/merge candidates, and descriptor changes.
- Stages VASP, Bader/BaderKit, critic2, and LOBSTER steps from a YAML workflow.
- Writes JSON, CSV, and HTML results with available source and tool provenance.

## Install

ElecTrace supports CPython 3.11–3.13. From a source checkout:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
```

The base installation includes a deterministic assignment solver. Use
`python -m pip install ".[accelerate]"` for the SciPy solver, or
`python -m pip install ".[all]"` for all optional features.

## Try the demo

The built-in demo runs without external scientific software:

```bash
electrace demo --output .electrace-demo
electrace inspect .electrace-demo/electrace-results
```

## Try real native outputs

The compact [HCN-to-HNC example](examples/hcn-hnc/README.md) includes seven
GPU-generated VASP frames plus native Bader and critic2 results:

```bash
cd examples/hcn-hnc
electrace analyze electrace.toml
electrace inspect electrace-results
```

It is an illustrative hand-defined static scan, not an optimized reaction path
or benchmark. No licensed pseudopotentials, executables, or large density files
are included.

## Analyze existing results

Create a configuration for an ordered VASP path, then point it to supported descriptor
files in the frame directories or an external descriptor directory:

```bash
electrace init electrace.toml --images /absolute/path/to/neb-images
# Review electrace.toml and set the descriptor source as needed.
electrace analyze electrace.toml
electrace inspect electrace-results
```

VASP scalar grids can also be summarized or sampled directly:

```bash
electrace volume /path/to/ELFCAR
electrace volume /path/to/ELFCAR --sample 0.5,0.5,0.5
electrace volume /path/to/CHGCAR --split-spin
```

For a spin-polarized (`ISPIN = 2`) run, `--split-spin` writes `spin/CHGCAR_tot` and
`spin/CHGCAR_mag` for a Bader spin-population run; see
[spin-polarized runs](docs/workflows.md#spin-polarized-runs).

## Run calculations across frames

The workflow YAML defines executable paths, input templates, dependencies, scheduler
settings, and frame selection. Selections can combine explicit indices with slices,
for example `frames: "0,2:11:2"`.

Create the manifest, edit it for the calculation, and stage the selected frames:

```bash
electrace workflow init electrace-workflow.yaml \
  --images /absolute/path/to/neb-images
# Set executable paths, templates, frames, and scheduler settings in the YAML.
electrace workflow validate electrace-workflow.yaml --json
electrace workflow stage electrace-workflow.yaml
```

Validation resolves the selected frames, commands, inputs, outputs, dependencies, and
scheduler settings without creating a workspace. Use `--json` when a script or coding
agent needs machine-readable output.

Run locally and analyze the collected outputs:

```bash
electrace workflow run electrace-workflow.yaml --yes --max-parallel 2
electrace workflow analyze electrace-workflow.yaml
```

With `scheduler.kind: slurm`, prepare scripts for review and then submit them:

```bash
electrace workflow submit electrace-workflow.yaml --job-name electrace
electrace workflow submit electrace-workflow.yaml --job-name electrace --yes --force
```

After the Slurm jobs finish, `electrace workflow analyze electrace-workflow.yaml`
collects the supported outputs—or reuses a valid existing collection—and runs the
same path analysis.

## Learn more

See [Using ElecTrace](docs/workflows.md), the [coding-agent reference](docs/agents.md),
and [scientific scope](docs/scientific-scope.md).

ElecTrace is available under the [BSD 3-Clause License](LICENSE).
