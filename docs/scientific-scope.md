# Scientific scope and limitations

ElecTrace organizes evidence across an ordered path. It does not replace electronic-
structure theory, convergence testing, or scientific judgment.

## Current scope

ElecTrace supports VASP-style NEB and static-series directories with consistent atom
content. It records frame provenance, matches atoms and normalized electronic
features under periodic boundary conditions, proposes birth/death and conservation-
backed split/merge events, tracks descriptor changes, and produces an auditable
report. Inputs can include BaderKit, critic2, LOBSTER, Bader, Chargemol/DDEC, and
canonical ElecTrace feature records, as well as per-atom spin populations from
collinear spin-polarized VASP runs and spin-resolved LOBSTER lists.

The separate workflow layer can stage and explicitly launch user-configured VASP and
analysis programs locally or through Slurm. It does not create pseudopotentials,
choose electronic-structure settings, or establish convergence.

The current release does not claim:

- validated feature identity through reconstruction or large cell changes;
- calibrated event probabilities, false-event rates, or universal thresholds;
- automatic grid-refinement or electronic-convergence audits;
- a unique oxidation state, bond definition, reaction mechanism, or causal event;
- AIMD-scale temporal statistics;
- non-collinear magnetization analysis; or
- Quantum ESPRESSO or CP2K ingestion.

## Interpreting results

An event means selected, method-dependent descriptors changed between sampled frames
under recorded thresholds. Agreement among charge, ELF/QTAIM, and ICOHP/ICOBI
evidence can strengthen an interpretation but does not make it unique.

For a matched bond critical point with two known atom neighbors in each frame,
ElecTrace also flags a change in those neighbors after matching atom identities.
This is a candidate reassignment between the sampled frames, not proof of a bond
forming or breaking. Missing or unmatched neighbors do not establish a change.
Neighbor indices alone do not distinguish periodic images.
The event's persistence status follows the matched feature lineage; it does not
establish that the new neighbor assignment persists in subsequent frames.
For matched bond critical points, shared-neighbor-relative motion is reported
separately as a strain-factored minimum-image displacement. It is correlated with
the chosen atom match and does not alter the assignment or establish continuity;
large motion between sparse frames may alias across a periodic cell.

With `atom_identity = "index"`, atom identity comes from the shared atom order of
the input files, as in NEB images, rather than from motion. ElecTrace checks only
that the element sequences agree, so it cannot detect a permutation of same-element
atoms. Displacements remain in the evidence but do not lower atom-match or
candidate-connectivity confidence.

The per-track summary compares each track's first and last frames and reports the
spread and largest step of each quantity. It applies no threshold, creates no
events, and inherits the frame sampling and feature matching; a track that starts or
ends mid-path covers only that segment.

A bond row records that a feature ties two atoms in a sampled frame; it is not a
bond test. LOBSTER lists every pair within its configured distance range, so a
LOBSTER row can continue where no critical point exists. A row that begins or ends
places a feature's appearance or loss only to within the frame spacing. critic2
records carry no lattice translation, so their distance is to the nearest image; in a
cell narrower than about twice a bond, a critical point joining a farther image, or
an atom's own image, is not represented correctly. Following an image from frame to
frame assumes each atom moves less than half a cell vector per step.

`focus_atoms` changes what is reported, not what is matched. Features without
recorded atoms, such as non-nuclear attractors or ring and cage critical points
without attractor atoms, belong to no focus and are left out, so check an unfocused
run before concluding that nothing changed elsewhere. The focus uses each frame's own
atom indices; with geometric identity, ElecTrace warns when a focus atom is matched to
a different index.

`focus_atoms = "auto"` selects atoms by distance cutoffs, adjacent-frame spin steps,
and bond-critical-point reassignments in the sampled frames. A cutoff is a geometric
criterion, not a bond order: a long or weak contact can cross it back and forth, and
a pair without a cutoff is never compared. Read each atom's rules and the recorded
cutoffs, and compare with an unfocused run, before concluding that an atom took no
part.

The noise floor, reported only with a focus, describes how much spectator values move
between sampled frames. It is not a significance test or a calibrated uncertainty.
It inherits the frame spacing, the partition, and the matching, and it is computed
separately for each producer. With few spectator atoms the upper percentiles rest on
a few steps, so read them with `steps`. An event several times above the 99th
percentile is unusual for a spectator but not proof of chemistry, and a change near
the floor can still be real.

`match_details = false` also changes only what is written. The per-pair match
evidence it leaves out is what a review of a surprising mapping needs, so rerun with
the default before reviewing one.

Barrier labels compare each event's step with the highest-energy sampled frame. For an
NEB this is the highest image, not the saddle point; for a static scan it is the scan
maximum. A spanning event changes on a step that touches that frame, which the frame
spacing cannot resolve further. No energy tolerance is applied, so a peak that differs
from its neighbors only by numerical noise still counts; read the rises recorded in
`barrier`. A rise of zero means an endpoint is the highest frame and the profile has
no interior maximum.

- Bader and DDEC charges depend on the partitioning scheme and are not automatically
  formal oxidation states.
- A net charge is the pseudopotential's ZVAL minus the Bader population, so it
  inherits the partition and the valence configuration. The atoms' net charges sum to
  zero only when every valence electron lies in an atomic basin; electride sites in
  BadELF, for example, hold electrons without a nucleus.
- A bond critical point or bond path is a QTAIM feature, not a universal bond test.
- ELF topology depends on the wavefunction or density, grid, and analysis settings.
- COHP/COBI depends on basis, projection, and integration choices; ICOHP is not a
  universal bond energy. A spin-resolved total is the sum of LOBSTER's two channels.
- Spin populations integrate the magnetization (spin up minus spin down) within
  total-density Bader basins, so they depend on the partition like Bader charges.
  They keep the sign VASP wrote. A frame can converge to the opposite global sign or
  another spin state; ElecTrace neither flips signs nor chooses states, so compare
  each frame's `atomic_spin_sum` before reading a sign change as chemistry.
- Event location is limited by the spacing of the supplied frames.

## Numerical limitations

Small basins and near-degenerate critical points may appear or disappear with grid
resolution. Consecutive-frame persistence filters isolated labels but cannot replace
a convergence study. For property changes, persistence tracks the matched lineage,
not whether the numerical change remains above its threshold in every later frame.

`[events.critical_point_min_field]` hides births, deaths, and connectivity changes of
weak critical points from the report; it does not decide which of them are real. A
cutoff that suits weak intermolecular contacts also hides the ring critical points of
covalent rings.

The critic2 Morse sum tests a search's completeness only in one direction. A sum that
differs from 0 (1 for a molecule) shows that points are missing but not which ones,
and `DISCARD` can remove them deliberately, as around a molecule in vacuum. A sum of
0 does not show that none is missing: two missed points with opposite signs in the
sum, such as a bond and a ring critical point, leave it unchanged.

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
