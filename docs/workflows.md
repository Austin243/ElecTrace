# Using ElecTrace

ElecTrace can analyze descriptor files that already exist or stage the calculations
needed for selected frames. External programs and `POTCAR` files remain user-supplied.

## Install and inspect

```bash
python -m pip install "git+https://github.com/Austin243/ElecTrace.git"
electrace --help
electrace doctor
```

Create a TOML configuration for an ordered VASP path and analyze existing evidence:

```bash
electrace init electrace.toml --images /absolute/path/to/frames
# Set descriptor_source and descriptor_dir in electrace.toml when needed.
electrace analyze electrace.toml
electrace inspect electrace-results
```

Supported inputs include normalized ElecTrace feature JSON, Bader/BaderKit output,
critic2 `CPREPORT` JSON, LOBSTER integrated bond lists, and Chargemol/DDEC charges.
`analysis.json` is the canonical result; CSV and HTML files are derived views.

By default, atoms are re-matched at every step by element and minimum-image distance
(`[matching] atom_identity = "geometric"`), with `max_atom_displacement` as the
motion cutoff. For NEB images and other paths that keep one atom order, set
`atom_identity = "index"`. ElecTrace then pairs the i-th atom of each frame, stops
with an error if the element sequences differ, and records each displacement without
letting it lower match confidence; `max_atom_displacement` is not used.

Events compare adjacent frames only. `track_summary.csv`, `track_summary` in
`analysis.json`, and the report's whole-path table give one row per feature track:
its first and last frames, whether it spans the path, and the net change, max − min,
and largest single step for charge, volume, and each configured descriptor. A change
spread over many frames appears there even when no single step crosses an event
threshold.

## Golden path from existing results

The repository includes a small, real native-output example generated from a
seven-frame HCN-to-HNC static scan:

```text
examples/hcn-hnc/frames/
├── 00/
│   ├── POSCAR
│   ├── OSZICAR
│   ├── ACF.dat
│   └── critic2.features.json
└── 01/ ... 06/
```

After installing ElecTrace from the repository root:

```bash
cd examples/hcn-hnc
electrace analyze electrace.toml
electrace inspect electrace-results
```

With ElecTrace 0.1.0a5, the checked configuration produces 7 frames, 35 feature
observations, 20 events (19 descriptor changes and one candidate
bond-critical-point connectivity change), and no warnings. See the
[example guide](https://github.com/Austin243/ElecTrace/blob/main/examples/hcn-hnc/README.md)
for what to inspect and the
[generation record](https://github.com/Austin243/ElecTrace/blob/main/examples/hcn-hnc/generation/PROVENANCE.md)
for scientific and redistribution provenance.

For local descriptors, place recognized files directly in each frame directory.
An external descriptor directory may instead be flat (`descriptors/00.json`) or
mirrored (`descriptors/00/features.json`). `descriptor_source = "auto"` searches
local files only when `descriptor_dir` is absent and external files only when it
is set; use `"merge"` to combine both locations. Duplicate feature IDs are an
error.

Recognized descriptor basenames are `electrace.features.json`, `features.json`,
`bader.json`, `badelf.json`, `critic2.features.json`, `ICOHPLIST.lobster`,
`ICOBILIST.lobster`, `DDEC6_even_tempered_net_atomic_charges.xyz`, and `ACF.dat`.
`CHGCAR` and `ELFCAR` are volumetric inputs, not feature descriptors by
themselves.

## Select frames and stage calculations

Create and edit a workflow manifest:

```bash
electrace workflow init electrace-workflow.yaml --images /absolute/path/to/frames
electrace workflow validate electrace-workflow.yaml --json
electrace workflow stage electrace-workflow.yaml --json
```

The manifest supplies executable paths, template directories, dependencies,
scheduler settings, and a frame selector. Selectors accept `all`, explicit indices,
or Python-style slices:

| Selector | Result |
| --- | --- |
| `all` | Every discovered frame |
| `0,3,7` | Frames 0, 3, and 7 |
| `1:8` | Frames 1 through 7 |
| `0:10:2` | Every second frame from 0 through 8 |
| `0,2:9:2` | Frame 0, then 2, 4, 6, and 8 |

Staging writes an isolated workspace and does not execute a program. Review the
generated commands and inputs before authorizing work.

## Supported calculation steps

| Goal | Step | Capability | Collected output |
| --- | --- | --- | --- |
| VASP static calculation | `vasp` | user-defined | manifest `expected_outputs` |
| Bader charge analysis | `bader` | `bader_charges` | `ACF.dat` |
| BaderKit charge analysis | `baderkit` | `bader_charges` | `bader.json` |
| BadELF or NNA analysis | `baderkit` | `elf_basins` or `non_nuclear_attractors` | `badelf.json` |
| QTAIM/ELF critical points | `critic2` | `qtaim_critical_points` or `non_nuclear_attractors` | `critic2.features.json` |
| Integrated COHP/COBI | `lobster` | `cohp` or `cobi` | `ICOHPLIST.lobster` or `ICOBILIST.lobster` |

Chargemol can be planned and its existing results can be parsed, but workflow v1 does
not stage a Chargemol step.

Each step points to an executable entry and may declare a template directory,
dependencies, expected outputs, environment variables, and plugin options. VASP
templates normally provide `INCAR`, `KPOINTS`, and `POTCAR`; ElecTrace places the
selected structure at `POSCAR`. Descriptor steps can consume files from dependency
steps with `options.input_links`.

## Run locally

Set `scheduler.kind: local`, stage the workflow, and authorize the reviewed commands:

```bash
electrace workflow run electrace-workflow.yaml --yes --max-parallel 2
electrace workflow status electrace-workflow.yaml --json
```

Independent jobs may run concurrently. A failed dependency blocks its dependents.
The command verifies declared inputs and expected outputs but cannot determine DFT or
descriptor convergence.

## Submit through Slurm

Set `scheduler.kind: slurm` and put ordinary `#SBATCH` options under
`scheduler.directives`. First generate scripts without submitting:

```bash
electrace workflow submit electrace-workflow.yaml --job-name electrace
```

Review the scripts, then authorize submission:

```bash
electrace workflow submit electrace-workflow.yaml --job-name electrace --yes --force
```

Use a manifest scheduler directive such as `exclude: "node[01-08]"` when particular
nodes must not be used. ElecTrace does not query Slurm availability or choose resources.

## Collect and analyze

After the declared outputs exist:

```bash
electrace workflow status electrace-workflow.yaml --json
electrace workflow analyze electrace-workflow.yaml --json
```

Analysis collects supported outputs, normalizes them, matches atoms and electronic
features across frames, and writes the same result bundle used for existing evidence.

## Scientific boundaries

- Bader and DDEC populations are not unique formal oxidation states.
- ICOHP/ICOBI values depend on the projection basis and integration convention.
- critic2 support normalizes critical-point topology; it does not integrate ELF basins.
- Native spin-resolved LOBSTER list ingestion is unsupported.
- Filesystem completion is not proof of electronic, grid, or projection convergence.
- ElecTrace never supplies licensed pseudopotentials or chooses universal VASP settings.

See [Scientific scope and limitations](scientific-scope.md) before interpreting events.
