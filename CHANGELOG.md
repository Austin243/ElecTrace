# Changelog

## Unreleased

### Added

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
