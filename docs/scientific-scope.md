# Scientific scope and limitations

ElecTrace is designed to organize evidence across a path. It is not a replacement
for electronic-structure theory, convergence testing, or scientific judgment.

## ElecTrace 0.1 scope

The first release focuses on:

- VASP-style ordered NEB image directories and static pressure-series directories;
- fixed or closely related periodic cells with consistent atom content;
- deterministic frame discovery and structure provenance;
- periodic atom correspondence and validation;
- normalized BaderKit and critic2 feature records;
- optional LOBSTER bonding descriptors;
- optional Chargemol/DDEC atomic-charge records;
- experimental deformation-aware feature costs and periodic voxel-overlap evidence;
- birth/death candidates and conservation-gated split/merge hyperedges;
- descriptor change points and an automated, auditable report;
- a non-executing plugin-planning boundary plus a separate, confirmation-gated local
  and Slurm workflow orchestrator.

Chargemol planning and conservative DDEC charge parsing are implemented, but external
DDEC execution is never implicit and cross-method charge fusion remains uncalibrated.

## Explicitly outside 0.1 claims

- Scientifically validated feature identity across large or reconstructive cell
  changes.
- Calibrated basin-lineage classification or known false-event rates.
- Automated grid-refinement auditing of birth/death/split/merge candidates.
- AIMD/MLIP-scale sparse-frame selection and temporal statistics.
- Calibrated probabilistic confidence or universal event thresholds.
- Automated construction of a reaction mechanism or oxidation-state assignment.
- ElecTrace-generated electronic-structure physics, pseudopotential management, or
  convergence automation. The workflow layer can launch a user-reviewed VASP job;
  it does not make that job scientifically appropriate or converged.
- Quantum ESPRESSO or CP2K ingestion.

These are research goals in the [roadmap](roadmap.md), not implied features.

## What an event means

An ElecTrace event is a structured observation that selected descriptors changed
between ordered frames under recorded thresholds. For example, a report may combine
the appearance of a critical point, a change in ELF evidence, stronger ICOHP, and a
charge shift. Agreement makes the interpretation better supported; it does not make
any descriptor uniquely correct or establish causality.

In particular:

- A Bader or DDEC charge is partitioning-scheme dependent and is not automatically
  an oxidation state. ElecTrace preserves BaderKit's explicitly supplied
  `oxidation_states` values when present but does not derive or validate a unique
  formal oxidation-state assignment.
- A bond critical point or bond path is a QTAIM topological feature, not by itself a
  universal definition of a chemical bond.
- ELF basin topology depends on the underlying wavefunction/density, numerical grid,
  and analysis settings.
- COHP/COBI values depend on basis/projection choices and the energy integration
  convention.
- A NEB path is a discretized model path; event location is bounded by image spacing
  unless additional images or interpolation evidence are supplied.

## Numerical and methodological limitations

### Grid sensitivity

Small basins, non-nuclear attractors, and near-degenerate critical points may appear
or disappear with grid resolution. Important events require grid-refinement checks.
Persistence across adjacent frames can reduce isolated noise but cannot replace a
convergence study.

### Periodic geometry

Fractional coordinates remove trivial boundary crossings, and the core includes
deformation-aware metrics for changing cells. Those metrics do not make large cell
deformation, reconstruction, or atom permutation uniquely solvable. Near-equal
complete-assignment costs are an ambiguity, not a license to choose an attractive
story. ElecTrace records the selected and alternative assignments and suppresses
affected claims in permissive mode; the configured tolerance remains an experimental,
uncalibrated cost threshold.

### Path sampling

Change-point timing depends on the sampled images. The absence of a detected event
between widely spaced images is weak evidence. Conversely, repeated nearly identical
images can make persistence appear stronger than the underlying path justifies.
Events too near the final frame to satisfy the configured persistence window are
reported as right-censored rather than silently classified as confirmed or transient.

### Method heterogeneity

Comparing frames generated with different functionals, pseudopotentials, charge
states, spin treatments, smearing, grids, or projection settings can create apparent
events. ElecTrace records provenance where available but cannot make an inconsistent
series physically comparable.

### Confidence language

Until confidence models are calibrated against labeled benchmarks, terms such as
“low,” “medium,” and “high” are deterministic rule labels, not statistical
probabilities. Configured hysteresis and sustained-state persistence can suppress
transient labels, and `minimum_event_confidence` can suppress low-scoring events, but
neither operation calibrates confidence. Reports must expose the underlying evidence
and thresholds.

## Minimum scientific review

Before publishing a conclusion:

1. inspect the source calculations and verify path ordering;
2. confirm electronic-structure and plugin settings are consistent;
3. repeat important observations under tighter grid and DFT convergence;
4. vary event thresholds and matching tolerances;
5. inspect ambiguous mappings and missing/partial plugin results;
6. compare at least two independent descriptors when the conclusion permits;
7. report negative and conflicting evidence, not only supporting features;
8. archive the configuration, normalized records, hashes, and software versions.
