# Changelog

## Unreleased

### Added

- Opt-in `[outputs] atom_groups` (named lists of 0-based atom indices) and
  `molecule_groups` (each molecule of the first frame, under the bond cutoffs of
  `focus_atoms = "auto"`). `atom_groups` in `analysis.json`, `atom_groups.csv`, and the
  report give each group's summed charge, net charge, and spin in every frame, null
  where any atom lacks a value, with whole-path changes. Molecules that keep every
  bond and hold no focus atom give a noise floor of adjacent-frame changes, and each
  group's largest step is also given as a multiple of it; `cut_bonds` lists the bonds
  a group cuts. In three 288-atom acetonitrile NEB bands, the 99th percentile of
  spectator-molecule steps was 0.005 and 0.014 e in the two molecular bands (44 and
  46 molecules), against 0.05–0.10 e for single spectator atoms in the same runs. In
  E10dir501 p35 the donor fragment went from −0.19 to −0.57 e at the barrier and back
  to −0.01 e, and the transferred proton from +0.19 to +0.57 e, ending at +0.36 e on
  N; their largest steps were 26 and 12 times the floor. Group sums equal the sums of
  the atoms' `ACF.dat` values in every frame. Without either setting, outputs are
  unchanged.
- `electrace figure` draws one `analysis.json` as a path figure: the energy profile,
  the density at the critical points of chosen bonds over their distances, and the net
  charges over the spins of chosen atoms, on one frame axis. A dashed line marks the
  highest-energy frame from `barrier`, and a dot between two frames marks each step
  with an event on a chosen bond or atom, on the quantity that changed. It writes PDF
  and SVG with editable text and a 600 dpi PNG, one PNAS column wide, and `--json`
  returns the plotted values, events, and warnings. Missing values are gaps, never
  zeros; distances come from the frame geometry for the bond rows' periodic image;
  panels without data, and bonds or atoms that a focus left out, are not drawn and a
  warning says why. `--ylim` sets shared scales. Matplotlib is the optional `plot`
  extra, also in `all`; nothing else needs it. Existing outputs are unchanged.
- Opt-in critic2 plan option `seeds = "periodic"`. Workflow staging writes `critic2.in`,
  which searches the all-electron density AECCAR0 + AECCAR2 with `SMOOTHRHO`, discards
  points below 1e-5 a.u., and seeds one `SEED LINE` per atom pair within
  `seed_distance` bohr (default 6), counting periodic images, with `seed_points`
  interior points (default 20), plus a `SEED POINT` at each atom-triplet barycenter
  (`seed_triplets`, default true). critic2's own `SEED PAIR` and `SEED TRIPLET` use
  in-cell positions, so bonds across a cell face get no seeds. The image search is
  exact for skewed cells and seeds every image when a cell is narrower than twice the
  distance. For the 33 images of three 288-atom acetonitrile bands, the inputs
  reproduce, line for line, the ones whose searches recovered 21 covalent bond
  critical points missed by `SEED PAIR` and gave Morse sums of zero in 27 images
  (before: 5). The default plan is unchanged.
- Workflow steps for Bader analysis on the all-electron reference. A `volume` step
  runs ElecTrace on an earlier step's grids: `aeccar_sum` writes `CHGCAR_sum` and
  `split_spin` writes `CHGCAR_tot` and `CHGCAR_mag`. Bader's new `spin_populations`
  capability runs `bader CHGCAR_mag -ref REFERENCE`, and its `ACF.dat` is collected as
  `spin/ACF.dat`; validation requires a `bader_charges` step with the same basins.
  Staging stops when a volume, spin, or periodic critic2 step needs a file that no
  dependency, template, or input link provides, or when a step needs a volume step's
  output without depending on it. `examples/electrace.density-workflow.yaml` chains
  VASP, the sum, Bader charges, the split, Bader spin, and critic2. Workflows without
  these steps stage the same files and scripts as before.
- `electrace volume AECCAR0 --add AECCAR2` writes `CHGCAR_sum` in the layout Bader
  reads. It stops, writing nothing, when the two files differ in grid, lattice,
  species, or atom positions, as files from two images do. For a 288-atom
  acetonitrile image, its `CHGCAR_sum` is byte-identical to the one Bader used.
- critic2 critical-point counts per frame. `critical_point_counts` in `analysis.json`
  counts every cell critical point in the `CPREPORT` JSON by signature: n (nuclei and
  non-nuclear attractors), b, r, and c, with the Morse sum n − b + r − c. A complete
  search sums to 0 in a periodic cell and to 1 for a molecule
  (`structure.is_molecule`). A warning names each frame whose sum differs: critical
  points are missing there, missed by the search or removed by `DISCARD`, so
  critical-point births, deaths, and connectivity changes at that frame may not be
  chemistry. The counts use every critical point, before any density cutoff or focus,
  and the critic2 document's metadata keeps them, so canonical JSON written from it
  does too. A frame without a critic2 document, a canonical document without counts
  (as earlier versions wrote), or a point without a signature gives a null sum with a
  reason, and a warning names those frames. The report says so above the event
  timeline and lists the counts in the frame inventory. Nothing is dropped or refused.
  The HCN example sums to +1 in every frame, because its input discards the vacuum
  points around the molecule. In three 288-atom acetonitrile NEB bands, the counts
  and sums equal critic2's own in all 66 searches: the first search sums to 0 in 5 of
  33 images (−5 to +7 elsewhere), and the minimum-image reseeding in 27 of 33. Without
  critic2 data, outputs are unchanged apart from an empty `critical_point_counts`.
- Opt-in `[events.critical_point_min_field]`, a density cutoff per critical-point
  kind (`bond_critical_point`, `ring_critical_point`, `cage_critical_point`). A birth,
  death, or connectivity change whose critical points all stay below their kind's
  cutoff is left out of the reported events, and `suppressed_events` counts what was
  left out. With a `bond_critical_point` cutoff, `focus_atoms = "auto"` ignores weak
  connectivity changes. In three 288-atom acetonitrile NEB bands, 72 of 83, 125 of
  133, and 148 of 164 bond-critical-point births, deaths, and connectivity changes
  stayed below 0.05 a.u., every ring and cage birth and death stayed below 0.03 a.u.,
  and the automatic focus fell from 93–105 atoms to 4–22. Numbering, confidences,
  tracks, summaries, and bond rows are unchanged; without the table, outputs are
  unchanged.
- Opt-in `[outputs] match_details = false` leaves each frame's feature list and the
  atom and feature match edges out of `analysis.json` and `report.html`. Tracks,
  events, `track_summary`, `focus`, and the CSV files do not change, and a rerun with
  the default restores the omitted records. For a synthetic 288-atom path with about
  1,540 features per frame, `analysis.json` drops from 45 to 14 MiB and the report
  from 26 to 9 MiB; with a four-atom focus, to 1.4 and 0.8 MiB. Without the setting,
  outputs are unchanged.
- A per-bond view: one record per atom pair, periodic image, and frame for each
  feature tied to exactly two atoms, such as critic2 bond critical points and LOBSTER
  bonds. Each record has the interatomic distance (the minimum image, or the
  feature's own lattice translation), the critical point's `field` and `laplacian`,
  and LOBSTER `icohp` and `icobi`, with missing values null. Features on one pair and
  image are joined; two that give the same quantity keep separate rows. It is
  `bonds` in `analysis.json`, `bonds.csv`, and a report table. Rows are keyed by atoms
  and image, never by feature track, so a bond that forms or breaks, or a critical
  point that moves to another pair (HCN `E00004`, C–H → N–H), begins or ends rows.
  `bond_id` follows a pair and image through atoms that wrap across the cell between
  frames. With `focus_atoms`, only pairs that include a focus atom are reported. A
  translated feature that does not say which atom it moves, as LOBSTER features saved
  as canonical JSON by earlier versions do, gets no row, and a warning counts them.
- `net_charge` (ZVAL minus population) on Bader and BaderKit atom populations when
  the frame directory's `POTCAR` or `OUTCAR` gives a ZVAL for every block of the
  POSCAR species line, in order. Blocks follow the species line and counts, so
  `Fe Fe O` keeps its two Fe blocks. Populations stay in `charge`. Otherwise,
  including for a VASP 4 POSCAR without species names, the charges stay populations,
  and a warning and the frame's `net_charge_unavailable` metadata give the reason;
  ZVAL is never assumed. DDEC charges, already net charges, are unchanged. Net
  charges are carried into feature tracks (a `net_charge` column in
  `feature_tracks.csv`), the whole-path summary, and the report, and each frame's sum
  is recorded under `provenance.descriptor_documents` with producer `vasp`.
  `electrace workflow analyze` collects each frame's `POTCAR` from its VASP step, or,
  when that step has none, the source frame's `POTCAR` or `OUTCAR`, so collected Bader
  charges get net charges too. Frame metadata records the POSCAR `species` line and `species_counts`.
- `[outputs] focus_atoms = "auto"` selects the atoms whose covalent bonds change
  (element-pair distance cutoffs under minimum image, with defaults for C, N, and H
  pairs and overrides under `[outputs.focus_bond_cutoffs]`), whose spin changes
  between adjacent frames by at least the `spin` event threshold, or that take part
  in a bond-critical-point connectivity change. `focus` records the rules that
  selected each atom; with no atom selected, a warning says so and every feature is
  reported, as without a focus. Explicit lists are unchanged.
- A spectator noise floor when a focus holds atoms: the median, 95th, and 99th
  percentiles of adjacent-frame |Δ| of atom charge, volume, and spin for each
  producer and element, over atoms outside the focus, in `analysis.json`
  (`noise_floor`) and the report. Each reported property change of an atom gives its
  flagged changes as multiples of the floor. Without a focus, there is no floor and
  outputs are unchanged. This is reporting only.
- Per-atom spin populations for collinear `ISPIN = 2` VASP runs.
  `electrace volume CHGCAR --split-spin` writes `spin/CHGCAR_tot` and
  `spin/CHGCAR_mag` unchanged in value and sign; it refuses one-grid, truncated,
  mismatched, and non-collinear files. Bader's `spin/ACF.dat` from
  `bader CHGCAR_mag -ref CHGCAR_tot` becomes the `spin` descriptor on each frame's
  Bader atom features. A frame without spin data has no spin value, and the analysis
  names such frames when spin is configured or present elsewhere.
- Spin-resolved LOBSTER `ICOHPLIST`/`ICOBILIST` files, both the two spin blocks of
  LOBSTER 3.x–5.0 and the two-column rows of LOBSTER 5.1: `icohp`/`icobi` is the sum
  of both channels, with `_up` and `_down` channel values.
- Opt-in `[outputs] focus_atoms`, a list of 0-based POSCAR atom indices for large
  cells. Matching still uses every atom and feature; events, feature tracks, the
  whole-path summary, the CSV files, and the report keep only tracks tied to those
  atoms and the events on them. `analysis.json` records the focus with reported and
  total counts. Without the list, outputs are unchanged.
- Events are labelled `before`, `spanning`, or `after` the highest-energy frame when
  every frame has an energy (`barrier_position`, with the peak frames and their rises
  in `barrier`), and the report marks the peak and the events on the energy profile.
  Pressure series are not placed. This is reporting only.
- `workflow validate` and `workflow stage` stop when a frame's POSCAR species order
  differs from the VASP template `POTCAR`'s `TITEL` order, naming the frame and both
  orders. Validation warns when the order cannot be checked.
- Opt-in `[matching] atom_identity = "index"` for paths with one fixed atom order,
  such as NEB images. Atoms are paired by position after an element-sequence check;
  displacement stays in the evidence but no longer lowers atom-match or candidate
  connectivity confidence. The `"geometric"` default is unchanged.
- A whole-path summary for each feature track in `analysis.json` (`track_summary`),
  `track_summary.csv`, and the HTML report: first and last frames, path coverage,
  net change, max − min, and the largest single step for charge, volume, and each
  configured descriptor. It is reporting only and adds no events or thresholds.
- Candidate bond-critical-point neighbor changes, with separate atom identity,
  motion and shared-neighbor-relative evidence in the HTML report.
- A compact seven-frame HCN-to-HNC example with GPU-generated VASP energies and
  native Bader and critic2 results.
- Public BSD-3-Clause packaging under the ElecTrace/`electrace` name.
- Ordered VASP path discovery, periodic atom and feature matching, event evidence,
  provenance, and JSON/CSV/HTML reports.
- Normalization for canonical feature JSON and primary BaderKit, critic2, LOBSTER,
  Bader, and Chargemol outputs.
- YAML workflows for selected frames, source-isolated staging, explicit local or
  Slurm execution, output collection, and analysis.
- Read-only `electrace workflow validate --json`, a compact coding-agent guide, and
  an installable ElecTrace skill.
- Streaming plain VASP grid summaries and periodic sampling through `electrace volume`.

### Changed

- Matching is much faster for large cells; assignments, confidences, diagnostics,
  events, and outputs are unchanged. Only pairs that can match are scored: a
  vectorized lower bound on the minimum-image distance skips pairs beyond the
  displacement gates, and compatibility is checked once per group of features with
  the same kind and semantics. The image search is skipped when the
  component-rounded image is shorter than half the cell's smallest singular value,
  since no other image can then be nearer. Bond-critical-point motion evidence is
  computed only for reported edges, and the bundled assignment solver used without
  SciPy is vectorized. A 288-atom acetonitrile NEB band (11 images, Bader charges and
  spins) takes 0.6 s instead of 9.5 s with SciPy and 4 s instead of 76 s without it.
  A synthetic path with about 1,540 features per frame takes 6 s instead of 191 s,
  with peak memory 0.66 GB instead of 1.5 GB.
- A LOBSTER bond with a lattice translation records `lattice_translation_atom`, the
  atom whose image the translation selects. That is LOBSTER's second label, which
  can be the lower index (`Cl5 Na1`), so sorted `neighbor_atom_indices` alone could
  not place the image.
- A LOBSTER bond from an atom to its own periodic image lists the atom once in
  `neighbor_atom_indices`, with `lattice_translation` naming the image. Such rows,
  common in real `ICOHPLIST` files, previously made the whole file fail. LOBSTER
  lists each such bond from both ends (translations T and −T); the two rows give one
  feature, and mirrored rows that disagree are an error.
- Orbital-resolved LOBSTER rows (`Na1_3s`, `Fe1_3d_z^2`) are checked against their
  atom-pair row and skipped, and the count is recorded in the document metadata.
  They previously failed, or a label ending in a digit was read as an atom
  (`Fe1_3d_z^2` as atom 2). Atom labels must be an element and an index, such as
  `Na1`, so LCFO fragment labels such as `AlN_1` are errors rather than atoms.
- `electrace volume` reads `CHGCAR` files with PAW augmentation occupancies and
  per-atom moment lines, and reports a truncated grid instead of stopping at the
  first augmentation block.
- A spin-polarized LOBSTER list yields one feature per bond; its second spin block
  was previously read as duplicate bonds. Malformed or reordered spin blocks are
  errors.
- The report's whole-path table groups rows by quantity and lists the largest
  max − min first.
- Machine-readable command results use the versioned `electrace.cli.v1` envelope when
  `--json` is supported and requested.
- External execution requires explicit `--yes`; plugin planning remains non-executing.
- Split/merge candidates require compatible charge or volume conservation evidence.
- Periodic geometry and assignment reuse reduce repeated matching work; SciPy remains
  an optional accelerator.
- The lean pre-release surface omits legacy parser aliases, the synthetic benchmark,
  HDF5-wrapped JSON, exhaustive alternative-assignment enumeration, and topology-
  specific hysteresis.
- Supported and tested Python versions are 3.11–3.13.
