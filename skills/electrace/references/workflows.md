# ElecTrace workflow reference

Read this reference only when choosing a descriptor capability, building a workflow,
or interpreting a current support boundary. Confirm exact flags with
`electrace COMMAND --help` because ElecTrace is pre-1.0.

## Frame selection

The workflow manifest accepts `all` or comma-separated indices and Python-style slices:

| Selection | Frames |
| --- | --- |
| `all` | Every discovered frame |
| `0,3,7` | Exact indices 0, 3, and 7 |
| `1:8` | Indices 1 through 7 |
| `0:10:2` | Every second frame from 0 through 8 |
| `::3` | Every third discovered frame |
| `0,2:9:2` | Index 0, then 2, 4, 6, and 8 |

Selections preserve expression order and reject duplicate or empty resolution.

## Capability map

| Goal | Plugin or workflow step | Capability | Required scientific output |
| --- | --- | --- | --- |
| Bader populations | `bader` | `bader_charges` | `ACF.dat` |
| BaderKit populations | `baderkit` | `bader_charges` | `bader.json` |
| BadELF/NNA evidence | `baderkit` | `elf_basins` or `non_nuclear_attractors` | `badelf.json` |
| QTAIM critical points | `critic2` | `qtaim_critical_points` | `critic2.features.json` |
| ELF topology/NNA | `critic2` | `non_nuclear_attractors` | `critic2.features.json` |
| Integrated COHP | `lobster` | `cohp` | `ICOHPLIST.lobster` |
| Integrated COBI | `lobster` | `cobi` | `ICOBILIST.lobster` |
| DDEC charges | `chargemol` planning or existing results only | `ddec_charges` | supported DDEC charge output |

Use `electrace doctor --config CONFIG --json` to check configured executables and
required inputs across discovered frames. Use `electrace plan PLUGIN FRAME
--capability CAPABILITY --json` to inspect the exact argument vector, standard input,
required inputs, expected outputs, and working directory without execution.

## Existing-output route

```bash
electrace init electrace.toml --images /absolute/path/to/frames --json
# Set series_kind, descriptor_source, and descriptor_dir as required.
electrace doctor --config electrace.toml --json
electrace analyze electrace.toml --json
electrace inspect electrace-results --json
```

Use this route for existing Chargemol/DDEC results. Do not add `chargemol` to a
workflow manifest.

## Calculation route

```bash
electrace workflow init electrace-workflow.yaml --images /absolute/path/to/frames --json
# Configure frames, executables, templates, steps, dependencies, and scheduler.
electrace workflow validate electrace-workflow.yaml --json
electrace workflow stage electrace-workflow.yaml --json
electrace workflow status electrace-workflow.yaml --json
```

For local work, run only after authorization:

```bash
electrace workflow run electrace-workflow.yaml --yes --max-parallel 2 --json
```

For Slurm, prepare first and submit second:

```bash
electrace workflow submit electrace-workflow.yaml --job-name electrace --json
electrace workflow submit electrace-workflow.yaml --job-name electrace --yes --force --json
```

On exit `1`, use `error.details.partial_submissions` and `record_path`, and inspect the
`slurm/submissions.json` journal before retrying. Never duplicate an already accepted
submission. A prior journal blocks another submission even with `--force`; remove or
archive it only after reconciling accepted IDs.

Then collect and analyze:

```bash
electrace workflow status electrace-workflow.yaml --json
electrace workflow analyze electrace-workflow.yaml --json
```

Every `--json` response uses the versioned `electrace.cli.v1` envelope. Check `ok`,
`error`, `warnings`, and `artifacts`; do not scrape human-formatted output. Workflow
validation resolves the plan without creating the workspace or any generated file.

## Current boundaries

- External VASP, Bader/BaderKit, critic2, LOBSTER, and Chargemol installations remain
  user-supplied. ElecTrace does not select convergence settings or supply `POTCAR`.
- BaderKit `bader.json` and `badelf.json` both normalize atomic records to
  `baderkit:atom:*`; do not attach both for one frame without an explicit resolution.
- critic2 support tracks critical-point topology and NNAs, not integrated ELF basin
  populations.
- Native spin-resolved LOBSTER list ingestion is unsupported.
- `workflow status` reports staged files and expected outputs, not scheduler state or
  scientific convergence.
- Bader/DDEC populations are not definitive formal oxidation states, and ICOHP is not
  a transferable bond energy.
