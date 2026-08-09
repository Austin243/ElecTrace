# Scientific scope and limitations

ElecTrace organizes evidence across an ordered path. It does not replace electronic-
structure theory, convergence testing, or scientific judgment.

## Current scope

ElecTrace supports VASP-style NEB and static-series directories with consistent atom
content. It records frame provenance, matches atoms and normalized electronic
features under periodic boundary conditions, proposes birth/death and conservation-
backed split/merge events, tracks descriptor changes, and produces an auditable
report. Inputs can include BaderKit, critic2, LOBSTER, Bader, Chargemol/DDEC, and
canonical ElecTrace feature records.

The separate workflow layer can stage and explicitly launch user-configured VASP and
analysis programs locally or through Slurm. It does not create pseudopotentials,
choose electronic-structure settings, or establish convergence.

The current release does not claim:

- validated feature identity through reconstruction or large cell changes;
- calibrated event probabilities, false-event rates, or universal thresholds;
- automatic grid-refinement or electronic-convergence audits;
- a unique oxidation state, bond definition, reaction mechanism, or causal event;
- AIMD-scale temporal statistics; or
- Quantum ESPRESSO or CP2K ingestion.

## Interpreting results

An event means selected, method-dependent descriptors changed between sampled frames
under recorded thresholds. Agreement among charge, ELF/QTAIM, and ICOHP/ICOBI
evidence can strengthen an interpretation but does not make it unique.

- Bader and DDEC charges depend on the partitioning scheme and are not automatically
  formal oxidation states.
- A bond critical point or bond path is a QTAIM feature, not a universal bond test.
- ELF topology depends on the wavefunction or density, grid, and analysis settings.
- COHP/COBI depends on basis, projection, and integration choices; ICOHP is not a
  universal bond energy.
- Event location is limited by the spacing of the supplied frames.

## Numerical limitations

Small basins and near-degenerate critical points may appear or disappear with grid
resolution. Consecutive-frame persistence filters isolated labels but cannot replace
a convergence study. For property changes, persistence tracks the matched lineage,
not whether the numerical change remains above its threshold in every later frame.

Fractional coordinates handle boundary crossings, and deformation-aware distances
support related cells. Large reconstruction or atom permutation is not uniquely
solvable. The matcher reports one deterministic best assignment and does not
enumerate near-optimal alternatives, so close mappings require manual review.

Inconsistent functionals, pseudopotentials, charge states, spin treatments, smearing,
grids, or projection settings can create artificial changes. Provenance is retained
when available but cannot make inconsistent calculations comparable. Confidence
labels are deterministic thresholds, not calibrated probabilities.

Before drawing a conclusion, verify path order and calculation settings, repeat
important observations with tighter convergence and grid settings, vary matching and
event thresholds, inspect surprising mappings or missing evidence, and archive the
configuration, normalized records, hashes, and software versions.
