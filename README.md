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
- Reads `POSCAR`/`CONTCAR` structures and supported plain `ELFCAR`/`CHGCAR`-like grids.
- Ingests supported Bader/BaderKit, critic2, LOBSTER, and Chargemol results.
- Matches atoms and electronic features across periodic and changing cells.
- Builds candidate feature correspondences and reports births, deaths, split/merge
  candidates, descriptor changes, and ambiguities.
- Stages VASP, Bader/BaderKit, critic2, and LOBSTER steps from a YAML workflow.
- Writes JSON, CSV, HTML, and optional HDF5 results with available source and tool
  provenance.

## Install

ElecTrace supports CPython 3.11–3.13. From a source checkout:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
```

The base installation includes a deterministic assignment solver. Use
`python -m pip install ".[accelerate]"` for the SciPy solver,
`python -m pip install ".[hdf5]"` for HDF5 export, or
`python -m pip install ".[all]"` for both.

## Try the demo

The built-in demo runs without external scientific software:

```bash
electrace demo --output .electrace-demo
electrace inspect .electrace-demo/electrace-results
```

## Analyze existing results

Create a configuration for an ordered VASP path, then point it to supported descriptor
files in the frame directories or an external descriptor directory:

```bash
electrace init electrace.toml --images /absolute/path/to/neb-images
# Review electrace.toml and set the descriptor source as needed.
electrace analyze electrace.toml
electrace inspect electrace-results
```

Plain VASP scalar grids can also be summarized or sampled directly:

```bash
electrace volume /path/to/ELFCAR
electrace volume /path/to/ELFCAR --sample 0.5,0.5,0.5
```

## Run calculations across frames

The workflow YAML defines executable paths, input templates, dependencies, scheduler
settings, and frame selection. Selections can combine explicit indices with slices,
for example `frames: "0,2:11:2"`.

Create the manifest, edit it for the calculation, and stage the selected frames:

```bash
electrace workflow init electrace-workflow.yaml \
  --images /absolute/path/to/neb-images
# Set executable paths, templates, frames, and scheduler settings in the YAML.
electrace workflow stage electrace-workflow.yaml
```

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

See the [quick start](docs/quickstart.md), [workflow guide](docs/workflows.md), and
[scientific scope](docs/scientific-scope.md) for detailed configuration and supported
data formats.

ElecTrace is available under the [BSD 3-Clause License](LICENSE).
