---
name: electrace
description: Analyze Bader, BadELF, QTAIM/critic2, LOBSTER, DDEC, spin-population, and VASP grid evidence across ordered periodic frames, including VASP NEB paths. Use ElecTrace to select frames, stage local or Slurm calculations, parse outputs, track changes, and generate reports. Do not use it as a convergence or formal-oxidation-state oracle.
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

For a large cell, set `[outputs] focus_atoms` to the reacting atoms (0-based POSCAR
indices), or to `"auto"` to select the atoms whose bonds, spin, or bond-critical-point
neighbors change, so the report stays readable. Matching still uses every atom. Quote
the reported and total counts from `focus` in `analysis.json`, and for `"auto"` each
atom's `rules` and any warning about missing bond cutoffs or a missing spin threshold.
Never present a focused run as the whole path. Compare charge, volume, and spin
events with `noise_floor`, the spectator percentiles for each element, before
describing a change as chemical. When every frame has an energy, events carry
`barrier_position` (`before`, `spanning`, or `after` the highest-energy frame); that
frame is not a located transition state.

## Missing calculations

```bash
electrace workflow init electrace-workflow.yaml --images /absolute/path/to/frames
# Review frames, executables, templates, dependencies, and scheduler settings.
electrace workflow validate electrace-workflow.yaml --json
electrace workflow stage electrace-workflow.yaml --json
```

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
- Filesystem completion does not establish electronic, grid, projection, or
  scientific convergence.
