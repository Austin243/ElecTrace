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
critic2 `CPREPORT` JSON, LOBSTER integrated bond lists, Chargemol/DDEC charges, and
per-atom spin populations from spin-polarized runs.
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
threshold. The report's table has one group per quantity, listing tracks by max − min,
largest first; quantities are not ranked against each other because their units
differ. The CSV and JSON keep track order.

`bonds.csv`, `bonds` in `analysis.json`, and the report's per-bond table give one row
per atom pair, periodic image, and frame for each feature tied to exactly two atoms:
critic2 bond critical points, through their two attractor atoms, and LOBSTER bonds.
A row holds the interatomic distance in Å, the critical point's `field` and
`laplacian` in critic2's units, and LOBSTER's `icohp` (eV) and `icobi`; a value with no
source in that frame is empty, never zero. Features on one pair and image are joined,
so a critical point and LOBSTER's ICOHP for the same bond share a row; two features
that give the same quantity keep separate rows, and a warning names them. The distance
uses the feature's `lattice_translation` when it has one (LOBSTER's eight-column
rows) and the nearest image otherwise.

Rows are keyed by atoms and image, never by feature track. A bond that forms or
breaks, or a critical point that moves to another pair, ends one bond's rows and
begins another's, and the report marks where each run of rows begins and ends.
`bond_id` names one pair and image along the path and stays the same when an atom
wraps across the cell between frames. Atoms are each frame's 0-based POSCAR indices,
as for `focus_atoms`; with geometric identity, a warning names bond atoms matched to
another index.

When every frame has an energy, each event is labelled `before`, `spanning`, or
`after` the highest-energy frame (`barrier_position` in `analysis.json` and
`events.csv`). An event spans the barrier when its step starts or ends at that frame,
so the frame spacing cannot place it on either side. Frames that tie exactly at the
maximum all count as the peak. `barrier` in `analysis.json` records the peak frames
and their rise above the first and last frames, and the report's energy profile marks
the peak and counts the events on each step. Events are not placed when a frame lacks
an energy, as NEB endpoint directories without `OUTCAR` or `OSZICAR` do, or for a
pressure series.

## Focus on the reacting atoms

In a large cell, most features belong to spectator molecules. List the atoms of
interest by 0-based POSCAR index:

```toml
[outputs]
focus_atoms = [12, 13, 40, 41, 97]
```

Matching still uses every atom and feature, so assignments, IDs, and confidences do
not change. Events, `feature_tracks.csv`, `track_summary`, the other CSV files, and
the report keep only the tracks tied to those atoms, through `neighbor_atom_indices`
or, for an atom-centered feature, the atom itself, plus the events on those tracks. A
track is kept whole when any of its observations touches a focus atom. Bond rows are
kept when either atom is a focus atom. `focus` in `analysis.json` lists the atoms with
their elements and the reported and total numbers of tracks, observations, events,
and bond rows. Warnings are not filtered. An index that is not an atom of every frame
stops the analysis before any output is written.

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
bond-critical-point connectivity change), 14 bond rows, and one warning: the example
has no `POTCAR` or `OUTCAR`, so its Bader charges stay electron populations. See the
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
themselves. Per-atom spin populations are read from `spin/ACF.dat` in a frame
directory, or from `descriptors/<label>/spin/ACF.dat` in a mirrored external
directory.

A LOBSTER list gives one bond feature per atom-pair row. Orbital-resolved rows,
whose labels name an orbital (`Na1_3s`, `Fe1_3d_z^2`), split the atom-pair row above
them by orbital. They are checked against that row, skipped, and counted as
`skipped_orbital_rows` in the document metadata; an orbital row without its atom-pair
row is an error. A bond from an atom to its own periodic image, such as `Na1 Na1`
with translation `0 0 -1`, lists the atom once in `neighbor_atom_indices`, and
`lattice_translation` names the image. LOBSTER lists such a bond from both ends, as
translations T and −T with the same distance and values, so the first row becomes
the feature and the mirrored row is counted as `collapsed_self_image_rows` in the
document metadata; mirrored rows that disagree are an error. Atom labels must be an
element and an index, such as `Na1`. LCFO fragment lists and n-center `NcICOBILIST` files are not supported.
A translated bond also records `lattice_translation_atom`, the atom whose image the
translation selects: LOBSTER's second label, which can be the lower index, as in
`Cl5 Na1`.

## Net atomic charges

Bader `ACF.dat` charges and BaderKit `atom_charges` are electron populations, kept in
`charge`. When the frame directory holds a `POTCAR` or an `OUTCAR` (the first found, in
that order), ElecTrace reads each entry's `TITEL` and `ZVAL`, from an `OUTCAR` only
its copy of the `POTCAR` headers, and adds `net_charge` = ZVAL − population to each
Bader and BaderKit atom feature. VASP pairs `POTCAR` entries with `POSCAR` species
blocks by position, so ZVAL is taken per block and the element orders must agree;
labels are compared by element symbol, so `C_h` or `H1.25` matches `C` or `H`. When
the file is missing, a species has no ZVAL, or the orders differ, the frame keeps
populations only and a warning names the frames and the reason. ZVAL is never
assumed. In an NEB, whose endpoint directories usually have no `OUTCAR`, place the
band's `POTCAR`, or a link to it, in each frame directory.

DDEC charges are already net charges and are unchanged. BaderKit basins and
oxidation states get no net charge. `net_charge` is tracked with its feature: it is a
column of `feature_tracks.csv` and a quantity in the whole-path summary. Each frame's
sum of net charges, `net_charge_sum`, is recorded with the ZVAL values under
`provenance.descriptor_documents` and shown in the report's frame inventory. The sum
is zero only when the populations place every valence electron on an atom.

## Spin-polarized runs

ElecTrace reads only what a run wrote. It never takes `ISPIN` from `INCAR` or assumes
a value. A collinear `ISPIN = 2` `CHGCAR` holds the total density followed by the
magnetization density (spin up minus spin down). In each frame directory:

```bash
electrace volume CHGCAR --split-spin
bader CHGCAR
cd spin && bader CHGCAR_mag -ref CHGCAR_tot
```

The first command writes `spin/CHGCAR_tot` and `spin/CHGCAR_mag` with the values and
sign unchanged, and reports the integrated magnetization, which matches the `mag=`
value in `OSZICAR`. It stops with an error, and writes nothing, for a `CHGCAR` with
one grid (`ISPIN = 1`, or the magnetization was not written), a truncated or missing
second grid, mismatched grids, or non-collinear output. `bader CHGCAR` reads only the
total-density grid and writes the charges to `ACF.dat`. The last command integrates
the magnetization within the same total-density basins and writes `spin/ACF.dat`.

During analysis, each atom's value in `spin/ACF.dat` becomes the `spin` descriptor
on that frame's Bader atom-charge features and is tracked with them. Like other
descriptors, it is thresholded and summarized once listed under
`[events.descriptor_thresholds]`, and it enters matching costs only when given a
scale under `[matching.descriptor_scales]`:

```toml
[events.descriptor_thresholds]
spin = 0.1
```

A frame without `spin/ACF.dat` has no spin value, never zero. When spin is
configured or present in other frames, `analysis.json` names the frames that lack
it. Each frame's `atomic_spin_sum` is recorded with its spin document under
`provenance.descriptor_documents`.

Spin-resolved LOBSTER lists give one feature per bond, from either the two spin
blocks of LOBSTER 3.x–5.0 or the two-column rows of LOBSTER 5.1. `icohp` (or
`icobi`) is the sum of both channels, which is what a non-spin-polarized run reports,
and `icohp_up` and `icohp_down` (or `icobi_up` and `icobi_down`) hold LOBSTER's
spin 1 and spin 2.

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
not stage a Chargemol step. Workflow v1 also does not stage the spin split, which
must run between the VASP and Bader jobs; use the commands in
[Spin-polarized runs](#spin-polarized-runs).

Each step points to an executable entry and may declare a template directory,
dependencies, expected outputs, environment variables, and plugin options. VASP
templates normally provide `INCAR`, `KPOINTS`, and `POTCAR`; ElecTrace places the
selected structure at `POSCAR`. Descriptor steps can consume files from dependency
steps with `options.input_links`.

VASP pairs `POSCAR` species with `POTCAR` entries by position, so validation and
staging stop when a frame's species order differs from the template `POTCAR`'s
`TITEL` order. Labels are compared by element symbol, so `Fe_pv` or `H.75` still
matches `Fe` or `H`. Validation warns when the order cannot be checked: a `POTCAR`
without `TITEL` lines, or a VASP 4 structure file without species names, for which
only the species count is compared.

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

- Bader and DDEC charges, and net charges from Bader populations, are not unique
  formal oxidation states.
- ICOHP/ICOBI values depend on the projection basis and integration convention.
- critic2 support normalizes critical-point topology; it does not integrate ELF basins.
- Spin populations depend on the Bader partition and keep VASP's sign; non-collinear
  spin is unsupported.
- Filesystem completion is not proof of electronic, grid, or projection convergence.
- ElecTrace never supplies licensed pseudopotentials or chooses universal VASP settings.

See [Scientific scope and limitations](scientific-scope.md) before interpreting events.
