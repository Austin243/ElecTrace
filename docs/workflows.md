# External calculation workflows

ElecTrace can stage an ordered VASP path, explicitly run or submit selected
external calculations, collect supported descriptor outputs, and analyze the
collected series. This is an opt-in orchestration layer. It does not design a
scientifically converged VASP, BaderKit, critic2, or LOBSTER calculation for you.

The plugin command-plan API remains non-executing. Only `electrace workflow run
--yes` and `electrace workflow submit --yes` cross the execution boundary. Review
every generated job or Slurm script and follow the policies of the machine on which
it will run.

## Safety and directory boundary

Keep the workflow manifest, source frames, templates, and generated workspace
separate:

```text
path-study/
├── electrace-workflow.yaml
├── source-frames/
│   ├── 00/CONTCAR
│   ├── 01/CONTCAR
│   └── 02/CONTCAR
└── templates/
    ├── vasp-static/
    │   ├── INCAR
    │   ├── KPOINTS
    │   └── POTCAR       # user-supplied; never commit it
    └── lobster/
        └── lobsterin
```

The important boundaries are:

- `project.root` is read-only. ElecTrace copies the selected structure but never
  writes into a source frame.
- `project.workspace` must be outside `project.root`. Staging records
  `.electrace-workspace.json` there and `--force` refuses to replace an unmarked or
  symlinked directory.
- Changing the YAML after staging invalidates the recorded manifest hash. Stage
  again rather than running a stale job graph.
- Template directories are copied into isolated job directories. They must contain
  only reviewed regular files, without nested directories or symlinks.
- ElecTrace does not install external programs, acquire licenses, download
  pseudopotentials, or provide a `POTCAR`. If your license permits it, place your own
  `POTCAR` in the private VASP template; staging copies that user-supplied template
  into the private workspace. Never commit or redistribute the file.

Relative `project.root`, `project.workspace`, `template_dir`, and executable paths
that contain a slash are resolved from the YAML file's directory. A bare executable
name such as `baderkit` is resolved through the execution environment's `PATH`.
`argv_prefix` is an argument array, not a shell fragment; for example, `["srun"]`
adds exactly one token. Shell expansion, pipes, redirection, and command
substitution are never inferred.

## Create and review the manifest

Generate a starter beside your project:

```bash
electrace workflow init electrace-workflow.yaml \
  --images /absolute/path/to/source-frames
```

Then edit it. The public contract is
[`electrace.workflow.v1`](https://github.com/Austin243/ElecTrace/blob/main/schemas/electrace.workflow.v1.schema.json),
and the repository also contains a compact
[public example](https://github.com/Austin243/ElecTrace/blob/main/examples/electrace.workflow.yaml).
The workflow YAML is distinct from the `electrace.toml` analysis configuration:
the YAML describes external jobs, while the TOML describes correspondence, event
detection, and result exports.

The following complete manifest uses only staging option names implemented in v1.
It runs one static VASP step, then branches to BaderKit BadELF basins and LOBSTER
ICOHP output. Replace every executable and template with settings validated for your
system before staging.

```yaml
schema_version: electrace.workflow.v1

project:
  root: source-frames
  series_kind: neb
  workspace: .electrace-work
  structure: CONTCAR
  fallback: POSCAR

# Canonical zero-based frame indices, not directory names.
frames: "all"

executables:
  vasp:
    path: /opt/vasp/bin/vasp_std
  baderkit:
    path: /opt/baderkit/bin/baderkit
  lobster:
    path: /opt/lobster/bin/lobster

scheduler:
  kind: local
  directives: {}

steps:
  - id: vasp_static
    kind: vasp
    executable: vasp
    template_dir: templates/vasp-static
    options:
      required_inputs:
        - INCAR
        - KPOINTS
        - POSCAR
        - POTCAR
      expected_outputs:
        - CHGCAR
        - ELFCAR
        - WAVECAR
      incar_overrides:
        LCHARG: true
        LELF: true
        LWAVE: true
        NSW: 0
        ISYM: -1

  - id: badelf_basins
    kind: baderkit
    executable: baderkit
    capability: elf_basins
    depends_on:
      - vasp_static
    options:
      charge_file: CHGCAR
      elf_file: ELFCAR

  - id: lobster_icohp
    kind: lobster
    executable: lobster
    capability: cohp
    template_dir: templates/lobster
    depends_on:
      - vasp_static
    options:
      input_file: lobsterin
      wavecar: WAVECAR
      structure_file: POSCAR
      potcar: POTCAR
```

BadELF output already carries BaderKit atomic population or oxidation-state arrays
when the external tool supplies them. Do not add a simultaneous BaderKit
`bader_charges` step for the same frame: `bader.json` and `badelf.json` both normalize
their atomic records as `baderkit:atom:*`, and analysis correctly rejects those
duplicate feature IDs. For a Bader-only workflow, replace `badelf_basins` with this
step and remove `ELFCAR` from the VASP expected outputs if no other step needs it:

```yaml
  - id: bader_populations
    kind: baderkit
    executable: baderkit
    capability: bader_charges
    depends_on:
      - vasp_static
    options:
      charge_file: CHGCAR
```

Dependencies must refer to earlier step IDs. Staging links declared dependency
outputs into each downstream job directory, while the runner still refuses to start
a child after a failed dependency. The VASP `required_inputs`, `expected_outputs`,
and `incar_overrides` keys above are orchestration checks, not proof that the
calculation is converged.

An optional step-level `resources` mapping is retained as advisory metadata in v1;
it does not control local concurrency or Slurm allocation. Use `--max-parallel` for
local concurrency and `scheduler.directives` for actual Slurm requests.

## Select frames

`frames` addresses the canonical zero-based order produced by deterministic frame
discovery. It does not address a directory merely because that directory is named
with the same number.

```yaml
frames: "all"       # every discovered frame
frames: "0,3,7"     # three explicit canonical indices
frames: "1:8"       # indices 1 through 7
frames: "0:10:2"    # every second frame among indices 0 through 9
frames: "::3"       # every third discovered frame
frames: "0,2:9:2"   # an explicit endpoint plus a slice
```

Slices follow Python's clipping and negative-index rules, including negative steps.
Comma-separated items are evaluated left to right. An out-of-range explicit index,
an empty result, a zero slice step, or selecting one frame twice is an error. Quote
selectors containing `:` so YAML cannot reinterpret them.

## Validate without writing

Resolve the manifest, frame selector, templates, executable vectors, scheduler,
dependencies, required inputs, and expected outputs before creating a workspace:

```bash
electrace workflow validate electrace-workflow.yaml --json
```

Validation is strictly read-only. It does not create the configured workspace, copy a
template, render a Slurm script, or launch a command. Missing executables are reported
as warnings because they may be supplied later by a compute-node module environment;
invalid templates, unresolved inputs, unsafe paths, and invalid job graphs are errors.
The resolved plan is returned inside an `electrace.cli.v1` response envelope and
follows the public
[`electrace.workflow-validation.v1` schema](https://github.com/Austin243/ElecTrace/blob/main/schemas/electrace.workflow-validation.v1.schema.json).

## Stage and inspect without executing

Staging discovers the selected frames, copies templates and structures, applies
`INCAR` overrides, constructs the dependency DAG, and writes auditable job records.
It does not launch a program:

```bash
electrace workflow stage electrace-workflow.yaml
electrace workflow status electrace-workflow.yaml --json
```

Use `workflow status` as a filesystem snapshot only. It reports required inputs and
expected outputs that currently exist; it does not query Slurm, parse convergence,
or establish that a scientific calculation succeeded.

If you intentionally changed a template or manifest, restage the marker-protected
workspace:

```bash
electrace workflow stage electrace-workflow.yaml --force
```

## Run locally

Local execution requires `scheduler.kind: local` and explicit confirmation:

```bash
electrace workflow run electrace-workflow.yaml --yes --max-parallel 2
electrace workflow status electrace-workflow.yaml
```

Commands are launched with `shell=False` in their dedicated job directories.
Standard output and standard error are captured in separate files, required inputs
are checked before launch, and expected outputs are checked afterward. The default
is one job at a time. `--keep-going` permits new independent jobs to start after a
failure; dependents of a failed job still do not run.

Without `--yes`, `workflow run` refuses to execute anything.

## Prepare or submit Slurm jobs

For Slurm, replace the local scheduler block with site-reviewed directives:

```yaml
scheduler:
  kind: slurm
  directives:
    partition: compute
    time: "00:30:00"
    nodes: 1
    ntasks: 8
    cpus-per-task: 1
    exclude: "node[1-8]"
```

If the site requires a launcher, add it as tokens to the relevant executable rather
than embedding a shell command:

```yaml
executables:
  vasp:
    path: /opt/vasp/bin/vasp_std
    argv_prefix:
      - srun
```

First prepare scripts without submitting them:

```bash
electrace workflow stage electrace-workflow.yaml
electrace workflow submit electrace-workflow.yaml --job-name electrace
```

This creates one dependency-ordered script per selected frame. Review the scripts,
their paths, requested resources, environment, and job name. Submit only after that
review:

```bash
electrace workflow submit electrace-workflow.yaml \
  --job-name electrace \
  --yes \
  --force
```

`--force` here replaces generated ElecTrace Slurm scripts; it is not the execution
confirmation. `--yes` invokes `sbatch` (or the executable supplied with
`--submit-command`). Submission returns a scheduler job ID, not a successful
calculation. Use your site's scheduler tools and then `workflow status` to review the
jobs and files.

ElecTrace journals every accepted ID in `slurm/submissions.json`. If an authorized
submission exits `1`, inspect `error.details.partial_submissions`, `record_path`, and
that journal before retrying. A prior journal blocks another submission even with
`--force`; archive or remove it only after reconciling every accepted ID. Never
resubmit an already accepted script blindly.

## Collect, analyze, and report

After every selected descriptor job has produced its declared output, collection
validates parser-supported files and publishes an isolated analysis input tree:

```bash
electrace workflow collect electrace-workflow.yaml
```

Collection rejects missing, empty, or symlinked descriptor outputs. It copies the
selected structures and recognized native or canonical descriptors without changing
the source frames or staged job directories. The managed tree is:

```text
.electrace-work/analysis/
├── .electrace-collection.json
├── input/
│   └── <analysis-frame>/CONTCAR
├── descriptors/
│   └── <analysis-frame>/{bader.json,badelf.json,ICOHPLIST.lobster,...}
├── electrace.toml
└── results/
```

The generated TOML points the core analyzer at this isolated, ordered series. Run
collection and analysis together with:

```bash
electrace workflow analyze electrace-workflow.yaml
electrace inspect .electrace-work/analysis/results
```

`workflow analyze` creates a collection when one is absent and reuses a valid
existing collection, so `workflow collect` followed by `workflow analyze` does not
need `--force`. Strict analysis is the default and fails on incomplete discovery or
ambiguous correspondence. Use `--permissive` only after reviewing that policy: it
records warnings and suppresses affected claims rather than resolving ambiguity.

Collection and analysis protect managed output. Re-collecting into an existing
collection, or replacing nonempty analysis results on a later analyze run, requires
an intentional `--force`:

```bash
electrace workflow analyze electrace-workflow.yaml --force
```

The result directory contains canonical `analysis.json`, a hash manifest, derived
CSV tables, and the offline `report.html` when enabled. JSON is the lossless source;
the report and tables are views. A clean artifact bundle does not mean that VASP,
BaderKit, LOBSTER, or the scientific interpretation was converged.

## Scientific checks before interpreting the report

### Bader and BadELF evidence

Bader atomic values are partition-dependent integrated electron populations, not
formal oxidation states. ElecTrace labels BaderKit `atom_charges` and basin charges
as positive electron populations. When a BaderKit document instead supplies its
`oxidation_states` array, ElecTrace preserves those signed values with the explicit
`bader_oxidation_state` definition; it does not derive, validate, or promote them as
a unique formal oxidation-state assignment.

BadELF requires the charge and ELF grids to be congruent. BaderKit's
[BadELF VASP guide](https://sweav02.github.io/baderkit/tutorials/vasp/badelf/)
warns that VASP normally writes `CHGCAR` and `ELFCAR` on different FFT meshes and
recommends matching `NGX/NGY/NGZ` with `NGXF/NGYF/NGZF`. Select and converge those
values for the material; the generic YAML above cannot supply universal numbers.
Grid agreement at one resolution is necessary but is not a grid-refinement study.

### critic2 boundary

The current critic2 adapter runs a conservative `AUTO` critical-point search and
collects native `CPREPORT` JSON as `critic2.features.json`. It normalizes critical
point topology; it does not parse `critic2.cro`, request attraction-basin
integration, or treat critical-point properties as integrated basin populations.
Selecting the critic2 `elf_basins` capability changes the analyzed scalar field to
`ELFCAR`, but the current handoff still reports ELF critical-point topology rather
than integrated ELF basin volumes or populations. Use the supported BaderKit BadELF
path when integrated BadELF basin records are required.
critic2 itself supports several
[basin-integration methods](https://aoterodelaroza.github.io/critic2/manual/integrate/),
but those methods are outside the current ElecTrace workflow handoff.

### LOBSTER and ICOHP evidence

Run LOBSTER from a converged VASP static calculation on the intended geometry. The
[LobsterPy input guide](https://jageo.github.io/LobsterPy/tutorial/commandlineinterface.html)
identifies the critical VASP controls: `NSW = 0`, `LWAVE = .TRUE.`, a LOBSTER-compatible
`ISYM` setting (`-1` for the complete mesh or `0` for the time-reversal half mesh),
and enough `NBANDS` for the chosen local projection basis. Choose and converge the
`KPOINTS` mesh rather than inheriting an unrelated relaxation mesh.

ElecTrace currently collects `ICOHPLIST.lobster` for `capability: cohp` and uses its
integrated values as bond-descriptor evidence. The ElecTrace 0.1 native-list contract
supports simple, non-spin rows with one terminal integrated value. Spin-resolved
LOBSTER list ingestion is not supported, and users must not interpret a parsed value
as channel-resolved evidence. The current parser does not guarantee detection of
every spin-resolved native variant.

ElecTrace does not parse `lobsterout`, verify LOBSTER convergence, judge the basis,
or enforce a spilling threshold. Retain and manually inspect the full LOBSTER output,
absolute/total charge spilling, band overlaps where applicable, basis choice, energy
range, k-point convergence, and VASP convergence. The LOBSTER authors emphasize that
[lower spilling indicates a more faithful projection](https://schmeling.ac.rwth-aachen.de/cohp/index.php?menuID=202),
and that an integrated COHP only
[hints at bond strength](https://schmeling.ac.rwth-aachen.de/cohp/index.php?menuID=3);
it is not a bond energy or a universal bond definition.

## Inspect a volumetric file directly

`electrace volume` is an independent, read-only diagnostic for a plain VASP scalar
grid:

```bash
electrace volume /path/to/ELFCAR
electrace volume /path/to/CHGCAR --json
electrace volume /path/to/ELFCAR --dataset 0 --sample 0.5,0.5,0.5 --json
```

It reports the grid shape, point count, cell volume, dataset count, and simple value
statistics, and can periodically sample one fractional coordinate. Compare the
reported `shape` for `CHGCAR` and `ELFCAR` when reviewing BadELF inputs.

The reader preserves the raw numbers exactly as stored; it does not assign physical
units or normalize CHGCAR values. It supports plain same-shape scalar datasets and
explicitly rejects augmentation blocks or ambiguous tails rather than guessing how
to skip them. It is a format and grid diagnostic, not a Bader/ELF integration or a
convergence test.
