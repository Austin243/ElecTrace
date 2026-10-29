---
name: electrace
description: Analyze Bader, BadELF, QTAIM/critic2, LOBSTER, DDEC, spin-population, and VASP grid evidence across ordered periodic frames, including VASP NEB paths. Use ElecTrace to select frames, stage local or Slurm calculations, parse outputs, track changes, and generate reports and path figures. Do not use it as a convergence or formal-oxidation-state oracle.
---

# Use ElecTrace

Use the `electrace` CLI instead of recreating its parsers, periodic matching, or
workflow staging. Confirm the installed interface with `electrace --help`. If it is
missing, offer installation from `git+https://github.com/Austin243/ElecTrace.git` in
an environment authorized by the user.

Read [references/workflows.md](references/workflows.md) for frame selectors and the
capability map.

## Existing outputs

```bash
electrace init electrace.toml --images /absolute/path/to/frames
# Review descriptor_source and descriptor_dir.
electrace doctor --config electrace.toml --json
electrace analyze electrace.toml --json
```

Use `analysis.json` as the canonical result. Report warnings, missing frames, and
provenance. Do not silently add `--permissive` to relax frame discovery.

For NEB images and other paths that keep one atom order, set
`[matching] atom_identity = "index"`; the default `"geometric"` re-matches atoms by
element and distance at every step. Events compare adjacent frames only, so also read
`track_summary` in `analysis.json` (or `track_summary.csv`) for each track's net
change, range, and largest single step over the whole path.

For per-bond trajectories, read `bonds` in `analysis.json` (or `bonds.csv`): one row
per atom pair, periodic image, and frame with the distance, critic2 `field` and
`laplacian`, and LOBSTER `icohp`/`icobi`. Rows begin or end where no feature ties the
pair, so do not join a pair's rows to another pair's through a shared feature track.
Bader charges are electron populations; `net_charge` (ZVAL − population) exists only
where the frame's `POTCAR` or `OUTCAR` gave ZVAL, and a warning names the frames
without it. In molecular crystals, set `[events.critical_point_min_field]`, for
example `bond_critical_point = 0.05`, so weak-contact critical points do not swamp the
events; report `suppressed_events`, and leave ring cutoffs off when rings may form.
Before calling a critical point's birth, death, or connectivity change chemistry,
read `critical_point_counts`: a frame whose critic2 Morse sum n − b + r − c is not 0
(1 for a molecule) is missing points, missed or removed by `DISCARD`, and a warning
names it.

For a large cell, set `[outputs] focus_atoms` to the reacting atoms (0-based POSCAR
indices), or to `"auto"` to select the atoms whose bonds, spin, or bond-critical-point
neighbors change, so the report stays readable. Matching still uses every atom. Quote
the reported and total counts from `focus` in `analysis.json`, and for `"auto"` each
atom's `rules` and any warning about missing bond cutoffs or a missing spin threshold.
Never present a focused run as the whole path. With a focus, compare charge,
volume, and spin events with `noise_floor`, the percentiles of atoms outside the
focus for each element, before describing a change as chemical; without a focus
there is no floor. `[outputs] match_details = false` leaves each frame's feature
list and the match edges out of `analysis.json` and the report; tracks, events, and
summaries do not change. Rerun with the default before reviewing a surprising match.
When every frame has an energy, events carry
`barrier_position` (`before`, `spanning`, or `after` the highest-energy frame); that
frame is not a located transition state.

To draw chosen bonds and atoms along the path, install `electrace[plot]` and run
`electrace figure RESULTS --bond C41-H227 --atom H227 --output NAME --json`. It
writes PDF, SVG, and a 600 dpi PNG of the energy, the bond-critical-point densities
over the distances, and the net charges over the spins, with events marked. Report
its warnings: a missing panel, an atom outside the focus, or a density gap is
missing evidence, not zero. Use `--ylim` to put several figures on the same scales.

## Missing calculations

```bash
electrace workflow init electrace-workflow.yaml --images /absolute/path/to/frames
# Review frames, executables, templates, dependencies, and scheduler settings.
electrace workflow validate electrace-workflow.yaml --json
electrace workflow stage electrace-workflow.yaml --json
```

For Bader charges, spin populations, and critic2 critical points on the all-electron
density, start from the repository's `examples/electrace.density-workflow.yaml`:
`volume` steps (`aeccar_sum`, `split_spin`) run ElecTrace on the VASP grids, a Bader
`spin_populations` step integrates the magnetization in the charge step's basins, and
critic2 `seeds: periodic` seeds every atom pair and triplet across periodic images.
Staging writes `critic2.in`; its header gives the seed count. Use it only for a VASP
single point (`NSW = 0`), because the seeds come from the staged structure.

Validation and staging do not execute scientific programs. For local execution, use
`electrace workflow run MANIFEST --yes` only after the user authorizes every staged
command. For Slurm, generate scripts without `--yes`, review them, and submit with
`--yes --force` only after authorization; `--force` replaces the reviewed scripts.

After the declared outputs exist:

```bash
electrace workflow status electrace-workflow.yaml --json
electrace workflow analyze electrace-workflow.yaml --json
```

## Boundaries

- Never invent a `POTCAR`, executable, credential, or private input.
- If validation reports a POSCAR/POTCAR species-order mismatch, ask the user for a
  `POTCAR` in the structure's species order; do not reorder structure files.
- Never add `--yes`, `--force`, or `--permissive` merely to make a command succeed.
- Treat Bader/DDEC values, ELF/QTAIM topology, and ICOHP/ICOBI as method-dependent
  evidence, not unique oxidation states, bonds, mechanisms, or energies.
- Treat missing evidence as missing and surface external-tool failures.
- Never assume an `ISPIN` value. Spin populations follow the Bader partition and
  VASP's sign; a frame without them has no spin value, not zero.
- Never assume a ZVAL or compute net charges by hand from populations.
  `workflow analyze` collects the `POTCAR` the VASP step used. For existing outputs,
  ask the user to place the frames' `POTCAR` or `OUTCAR` in each frame directory.
- Filesystem completion does not establish electronic, grid, projection, or
  scientific convergence.
