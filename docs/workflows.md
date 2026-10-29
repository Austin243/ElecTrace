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
rows) and the nearest image otherwise. A two-atom feature whose translation does not
say which atom it moves (`lattice_translation_atom`), as in LOBSTER features saved as
canonical JSON by earlier versions, has no bond row; a warning counts such features
and names their frames, and reading the native LOBSTER list again restores the rows.

Rows are keyed by atoms and image, never by feature track. A bond that forms or
breaks, or a critical point that moves to another pair, ends one bond's rows and
begins another's, and the report marks where each run of rows begins and ends.
`bond_id` names one pair and image along the path and stays the same when an atom
wraps across the cell between frames. Atoms are each frame's 0-based POSCAR indices,
as for `focus_atoms`; with geometric identity, a warning names bond atoms matched to
another index.

In a molecular crystal, weak intermolecular contacts give bond, ring, and cage critical
points with densities near 0.01–0.03 a.u. that appear and disappear between frames,
and their births, deaths, and connectivity changes can outnumber the reaction's own
events. A cutoff per kind leaves such events out of the report:

```toml
[events.critical_point_min_field]
bond_critical_point = 0.05
```

An event is left out when every critical point in it stays below the cutoff for its
kind in the frame where it exists: the new point of a birth, the lost point of a death,
and both frames of a connectivity change. Property changes, other features, kinds
without a cutoff, and points without a `field` are always reported. Event numbering,
confidences, tracks, summaries, and bond rows use every critical point, so the IDs of
reported events keep gaps. `suppressed_events` in `analysis.json` records the cutoffs
and the counts left out per kind and event type over every atom, before any focus,
and the report's timeline says how many were left out. Ring critical points of covalent rings, such as a triazine, also
have densities near 0.02 a.u., so set a ring cutoff only when ring formation is not
under study. The value is in the units of the critical point's `field`: a.u. for
critic2.

Before reading a critical point's birth, death, or connectivity change as chemistry,
check that the search found every point. For each frame's critic2 `CPREPORT` JSON,
`critical_point_counts` in `analysis.json` counts every cell critical point by
signature, n (nuclei and non-nuclear attractors), b, r, and c, and gives the Morse
sum n − b + r − c. A complete search sums to 0 in a periodic cell and to 1 for a
molecule (`structure.is_molecule`). Any other sum means points are missing from that
frame: missed by the search, or removed by `DISCARD`, as the vacuum points around a
molecule in a large cell are. A warning names those frames, the report says so above
the event timeline, and its frame inventory lists the counts. The counts use every
critical point, before any density cutoff or focus, and stay with
`match_details = false`. The critic2 document's metadata in
`provenance.descriptor_documents` holds the same counts, so canonical JSON written
from it keeps them. A frame without a critic2 document, a canonical critic2 document
without counts (earlier versions dropped the nuclei), or a point without a signature
gives a null sum and a `reason`; reading the native `CPREPORT` JSON again counts it.

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
  exist. With `bond_critical_point` under `[events.critical_point_min_field]`, a change
  whose critical point stays below that density in both frames is ignored, and
  `weak_changes_ignored` counts such changes. Without the cutoff, weak contacts in a
  molecular crystal can select a large share of the atoms.

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
warns and names it. In each frame, the cutoff of every element pair present there
must be at most half the frame's smallest lattice-plane spacing, so that an atom pair
has only one image within it; a smaller or more skewed cell stops the analysis. Bonds
are compared by each frame's own atom indices, so every frame must list the same
elements in the same order.

`focus` in `analysis.json` then also gives each atom's `rules`, `selection = "auto"`,
and, under `rules`, the cutoffs used, the bonds that change, the spin threshold, the
number of connectivity changes, and the reason any rule did not run. If no rule
selects an atom, the analysis warns and reports every feature, as without a focus.

## Spectator noise floor

Atoms away from the reaction still change charge, volume, and spin between frames.
When a focus holds atoms, from a list or from `"auto"`, `noise_floor` in
`analysis.json` and the report give, for each producer, element, and quantity
(charge, volume, spin), the median and the 95th and 99th percentiles of the
adjacent-frame |Δ| of spectator atoms: the atom tracks that never touch a focus atom.
Values are in each source tool's units, with the numbers of atoms and steps behind
them. The atom values are the per-atom Bader, BaderKit, or DDEC charge features with
their volumes and spins; BaderKit's per-maximum basins are left out so that no atom
counts twice. Without a focus, the reacting atoms cannot be told apart from
spectators, so there is no floor and the outputs are unchanged.

Each reported property change of an atom-charge feature then carries `noise_floor`:
for each flagged charge, volume, or spin change, the change and its magnitude divided
by the floor's median, 95th, and 99th percentiles for that element. A ratio is `null`
where the percentile is zero, as for spin in a closed-shell path. Volume events are
flagged by relative change, but the ratio uses the absolute change, like the floor.
The report's timeline shows the ratio to the 99th percentile. This is reporting only:
thresholds, events, and confidences do not change.

## Smaller outputs for large cells

By default, `analysis.json` and `report.html` also hold every frame's feature list
and every atom and feature match edge with its scoring evidence. With about 1,500
critic2 and Bader features per frame, these records make up most of both files. To
leave them out:

```toml
[outputs]
match_details = false
```

Tracks, events with their evidence, `track_summary`, `focus`, the CSV files, frame
counts, unmatched IDs, and match diagnostics do not change. Every feature observation
is still in `tracks` unless a focus filters it, but tracks do not carry voxel supports
(`voxel_indices`), so these are only in the default output. The configuration echo in
`analysis.json` records the setting. Matching is deterministic, so a rerun with the
default restores the omitted records for review.

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
blocks by position, so ZVAL is taken per block of the species line and counts, and
the element orders must agree. Adjacent blocks of one element stay apart, as in
`Fe Fe O` for two magnetic sublattices or `Ga As H H` for two pseudo-hydrogens.
Labels are compared by element symbol, so `C_h` or `H1.25` matches `C` or `H`. When
the file is missing, a species has no ZVAL, the orders differ, or a VASP 4 `POSCAR`
names no species, the frame keeps populations only. A warning then names the frames
and the reason, which is also `net_charge_unavailable` in each frame's `metadata` in
`analysis.json`. ZVAL is never assumed. In an NEB, whose endpoint directories usually
have no `OUTCAR`, place the band's `POTCAR`, or a link to it, in each frame directory;
`electrace workflow analyze` collects the `POTCAR` its VASP step used.

DDEC charges are already net charges and are unchanged. BaderKit basins and
oxidation states get no net charge. `net_charge` is tracked with its feature: it is a
column of `feature_tracks.csv` and a quantity in the whole-path summary. Each frame's
sum of net charges, `net_charge_sum`, is recorded with the ZVAL values under
`provenance.descriptor_documents`, whose producer `vasp` is then listed in
`provenance.descriptor_producers`, and shown in the report's frame inventory. The sum
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

With `LAECHG = .TRUE.`, VASP also writes the frozen core density (`AECCAR0`) and the
valence density with its PAW one-centre corrections (`AECCAR2`). Their sum
approximates the all-electron density, whose maxima sit on the nuclei, and is the
usual Bader reference. Use it for both partitions so that charge and spin share their
basins:

```bash
electrace volume AECCAR0 --add AECCAR2
bader CHGCAR -ref CHGCAR_sum
electrace volume CHGCAR --split-spin
cd spin && bader CHGCAR_mag -ref ../CHGCAR_sum
```

`--add` writes `CHGCAR_sum` beside `AECCAR0`, or in `--output-dir`, in the layout
Bader reads, and reports each file's integral and the sum's. It uses the first grid
of each file and stops, writing nothing, when the two files differ in grid, lattice,
species, or atom positions, as files from different runs or images do. `--force`
replaces an existing `CHGCAR_sum`.

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
| AECCAR0 + AECCAR2 reference | `volume` | `aeccar_sum` | none; writes `CHGCAR_sum` |
| Spin split | `volume` | `split_spin` | none; writes `CHGCAR_tot`, `CHGCAR_mag` |
| Bader charge analysis | `bader` | `bader_charges` | `ACF.dat` |
| Bader spin populations | `bader` | `spin_populations` | `spin/ACF.dat` |
| BaderKit charge analysis | `baderkit` | `bader_charges` | `bader.json` |
| BadELF or NNA analysis | `baderkit` | `elf_basins` or `non_nuclear_attractors` | `badelf.json` |
| QTAIM/ELF critical points | `critic2` | `qtaim_critical_points` or `non_nuclear_attractors` | `critic2.features.json` |
| Integrated COHP/COBI | `lobster` | `cohp` or `cobi` | `ICOHPLIST.lobster` or `ICOBILIST.lobster` |

Chargemol can be planned and its existing results can be parsed, but workflow v1 does
not stage a Chargemol step. `volume` steps run ElecTrace itself, through an
`executables` entry that names the installation the jobs should use, on grids from
earlier steps; see [All-electron density analysis](#all-electron-density-analysis).

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

## All-electron density analysis

Bader charges and spin populations on the AECCAR0 + AECCAR2 reference, and critic2
critical points of the all-electron density, take six steps per frame.
[`examples/electrace.density-workflow.yaml`](https://github.com/Austin243/ElecTrace/blob/main/examples/electrace.density-workflow.yaml)
is a complete manifest:

| Step | Kind and capability | Command in the step directory |
| --- | --- | --- |
| `vasp` | `vasp` | VASP with `LCHARG`, `LAECHG`, and `ISPIN = 2`; `expected_outputs` lists `CHGCAR`, `AECCAR0`, `AECCAR2` |
| `reference` | `volume`, `aeccar_sum` | `electrace volume AECCAR0 --add AECCAR2` |
| `charges` | `bader`, `bader_charges` | `bader CHGCAR -ref CHGCAR_sum` |
| `split` | `volume`, `split_spin` | `electrace volume CHGCAR --split-spin` |
| `spin` | `bader`, `spin_populations` | `bader CHGCAR_mag -ref CHGCAR_sum` |
| `critic2` | `critic2` with `seeds: periodic` | `critic2 -q critic2.in critic2.cro` |

Each step links its inputs from the steps in its `depends_on`. The `volume` steps
write into their own directories, replace their outputs when a job reruns, and are
not collected. The spin step's `ACF.dat` is collected as `spin/ACF.dat`, so analysis
attaches each atom's magnetization to its Bader charge. For an `ISPIN = 1` run, leave
out `split` and `spin`.

A `spin_populations` step needs `reference_file`. Validation stops unless the
workflow also has a `bader_charges` step whose basins are the same: the same
`reference_file`, or, for a charge step without one, `CHGCAR_tot`. Staging stops when
a `volume`, spin, or periodic critic2 step needs a file that none of its dependencies
declares as an output and no template or input link provides, such as `AECCAR2`
missing from the VASP step's `expected_outputs`, and when any step needs a file that a
`volume` step writes without depending on that step. These mistakes would otherwise
appear only after the VASP job.

### critic2 with periodic seeds

With `seeds: periodic`, staging writes `critic2.in` in the critic2 step directory.
The input reads the crystal from `AECCAR0`, searches AECCAR0 + AECCAR2 with
`SMOOTHRHO`, discards points where that density is below 1e-5 a.u. as vacuum
artifacts, and starts the search from explicit seeds:

- one `SEED LINE` for each atom pair within `seed_distance`, counting periodic images,
  holding the `seed_points` interior points that `SEED PAIR` would use, k/(n + 1) of
  the way along the pair for k = 1 … n (one point is written as a `SEED POINT` at the
  midpoint);
- with `seed_triplets`, one `SEED POINT` at the barycenter of each atom triplet whose
  three distances are all within `seed_distance`, each triplet once.

| Option | Default | Meaning |
| --- | --- | --- |
| `seeds` | `auto` | `auto`: the plan without seeds, unchanged; `periodic`: the input above |
| `seed_distance` | 6.0 | Pair and triplet distance in bohr, critic2's length unit |
| `seed_points` | 20 | Points on each pair's line |
| `seed_triplets` | `true` | Add triplet barycenters |

critic2's own `SEED PAIR` and `SEED TRIPLET`, in 1.2 through the current master, use
the atoms' in-cell positions without periodic images, so a bond across a cell face gets
no seeds; without seeds, `AUTO` subdivides the Wigner–Seitz cell. In 33 images of three
288-atom acetonitrile NEB bands, `SEED PAIR DIST 6 NPTS 20` missed 21 covalent C–H and
N–H bond critical points, all across a cell face, and left Morse sums of −5 to 7
(zero in 5 images); `AUTO` alone gave 94 in the one image tried. The defaults above
recovered all 21 and gave zero in 27 images and ±1 in the other 6. Six bohr (3.2 Å)
spans covalent bonds and the shorter intermolecular contacts. Those inputs had
74,000–86,000 seeds per image, and each image took 66–77 minutes on 32 OpenMP threads.
Each frame's sum is in `critical_point_counts` in `analysis.json`.

Pairs come from an exact image search. On each axis, an image within the distance is
at most 1/2 + `seed_distance`/h lattice vectors from the component-rounded one, where h
is the spacing of that axis's lattice planes, so skewed cells need no reduced basis.
When a cell is narrower than twice the distance, a pair can have several images
within it, and an atom can bond to its own image; each image gets its own line. In the
acetonitrile cells every pair had one image within the distance, the minimum image,
and the generated inputs reproduce the inputs that ran, line for line, from the same
positions. A structure with no pair within the distance stops validation, because
critic2 would otherwise fall back to its default seeding.

The seeds are placed from the frame's structure, which staging copies into the step
directory, while critic2 reads the crystal from the `AECCAR0` header. The two agree
to the header's six decimals when the VASP step does not move the atoms (`NSW = 0`),
as for the images of a path. The `CRYSTAL` line names the structure's species in
order; critic2 uses these names only when the `AECCAR0` header has none. A periodic
step takes no `field_file` or `input_file`, plans `qtaim_critical_points` only, and
cannot have `critic2.in` in its template directory. `electrace plan critic2` with
`seeds = "periodic"` lists `critic2.in` as an input that staging writes.

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
For [net charges](#net-atomic-charges), each frame's collection also holds the
`POTCAR` its VASP step used or, when that step has none, the source frame's own
`POTCAR` or `OUTCAR`. A VASP step's `OUTCAR` is not collected, because its energy
would replace the source frame's.

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
