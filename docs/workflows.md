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
track is kept whole when any of its observations touches a focus atom. `focus` in
`analysis.json` lists the atoms with their elements and the reported and total
numbers of tracks, observations, and events. Warnings are not filtered. An index that
is not an atom of every frame stops the analysis before any output is written.

To let ElecTrace choose the atoms, set:

```toml
[outputs]
focus_atoms = "auto"
```

The focus then holds every atom that one of three rules selects:

- **bond**: a covalent bond to the atom is present in some frames and absent in
  others. Two atoms are bonded when their minimum-image distance is below the cutoff
  for their element pair.
- **spin**: the atom's spin population changes between adjacent frames by at least
  the `spin` threshold under `[events.descriptor_thresholds]`, the same test spin
  events use. Without that threshold the rule does not run, and the analysis warns if
  spin populations are present.
- **bcp**: the atom is a neighbor, before or after, of a bond-critical-point
  connectivity change. The rule runs when bond critical points, such as critic2's,
  exist.

The default cutoffs, in Å, are:

| Pair | C–C | C–N | N–N | C–H | N–H | H–H |
| --- | --- | --- | --- | --- | --- | --- |
| Cutoff | 1.75 | 1.75 | 1.65 | 1.30 | 1.25 | 0.90 |

Set or replace a pair's cutoff under `[outputs.focus_bond_cutoffs]`, naming the two
elements in either order; the other defaults stay:

```toml
[outputs.focus_bond_cutoffs]
C-O = 1.60
N-C = 1.70
```

A pair present in the structure without a cutoff is never bonded, and the analysis
warns and names it. Each cutoff must be at most half the smallest lattice-plane
spacing of every frame, so that an atom pair has only one image within it; a smaller
or more skewed cell stops the analysis. Bonds are compared by each frame's own atom
indices, so every frame must list the same elements in the same order.

`focus` in `analysis.json` then also gives each atom's `rules`, `selection = "auto"`,
and, under `rules`, the cutoffs used, the bonds that change, the spin threshold, the
number of connectivity changes, and the reason any rule did not run. If no rule
selects an atom, the analysis warns and reports every feature.

## Spectator noise floor

Atoms away from the reaction still change charge, volume, and spin between frames.
`noise_floor` in `analysis.json` and the report give, for each producer, element,
and quantity (charge, volume, spin), the median and the 95th and 99th percentiles of
the adjacent-frame |Δ| of spectator atoms, in each source tool's units, with the
numbers of atoms and steps behind them. The atom values are the per-atom Bader,
BaderKit, or DDEC charge features with their volumes and spins; BaderKit's
per-maximum basins are left out so that no atom counts twice. Spectators are the
atom tracks that never touch a focus atom, or every atom when there is no focus.
Without a focus, the floor includes any reacting atoms, which in a small cell can be
most of the sample.

Each property change of an atom-charge feature then carries `noise_floor`: for each
flagged charge, volume, or spin change, the change and its magnitude divided by the
floor's median, 95th, and 99th percentiles for that element. A ratio is `null` where
the percentile is zero, as for spin in a closed-shell path. Volume events are flagged
by relative change, but the ratio uses the absolute change, like the floor. The
report's timeline shows the ratio to the 99th percentile. This is reporting only:
thresholds, events, and confidences do not change.

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

- Bader and DDEC populations are not unique formal oxidation states.
- ICOHP/ICOBI values depend on the projection basis and integration convention.
- critic2 support normalizes critical-point topology; it does not integrate ELF basins.
- Spin populations depend on the Bader partition and keep VASP's sign; non-collinear
  spin is unsupported.
- Filesystem completion is not proof of electronic, grid, or projection convergence.
- ElecTrace never supplies licensed pseudopotentials or chooses universal VASP settings.

See [Scientific scope and limitations](scientific-scope.md) before interpreting events.
