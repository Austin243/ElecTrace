# HCN to HNC native-output example

This is a small real-data example for ElecTrace's existing-results path. It
contains seven converged static VASP frames, native Bader `ACF.dat` files, and
native critic2 `CPREPORT` JSON. The hydrogen atom follows a hand-defined bent
path from H–C≡N toward C≡N–H while the C–N distance remains fixed.

The scan is designed to exercise the software, not to establish a reaction
barrier. It is not an optimized NEB, transition-state calculation, or benchmark.

## Run it

Install ElecTrace from the repository root, then run the analysis from this
directory so the relative paths in `electrace.toml` resolve correctly:

```bash
python -m pip install .
cd examples/hcn-hnc
electrace analyze electrace.toml
electrace inspect electrace-results
```

For an agent or script, append `--json` to either command. With ElecTrace
0.1.0a5, a successful run of the checked configuration reports:

- 7 ordered frames and no analysis warnings;
- 35 feature observations belonging to 5 tracks;
- 20 events: 19 descriptor changes (17 confirmed, 2 right-censored) and one
  confirmed candidate connectivity change;
- `analysis.json`, `events.csv`, `feature_tracks.csv`, `track_summary.csv`,
  `report.html`, and `manifest.json` in `electrace-results/`.

The generated result bundle is intentionally ignored by Git because it records
run-local paths, timestamps, hashes, and platform provenance.

## What to look for

Read warnings first, then inspect the tracks before assigning chemical meaning
to events. The input atom order is C=0, N=1, H=2. In
`feature_tracks.csv`, track `T00005` is the H-related bond critical point:

| Frames | `neighbor_atom_indices` | Topological connection |
| --- | --- | --- |
| 00–02 | `[0, 2]` | C–H |
| 03–06 | `[1, 2]` | N–H |

ElecTrace keeps that critical point on one track while its critic2 field and
Laplacian values change and Bader populations redistribute. The other critical
point remains connected to C and N. Event `E00004` flags the reassignment
between frames 02 and 03 as a candidate `connectivity_change`
(C[0]–H[2] → N[1]–H[2]). Its confidence is low (0.19) because H moves 0.83 Å
between those frames, which weakens the atom correspondence; the event evidence
lists atom identity, motion, and shared-neighbor geometry separately for review.
`ACF.dat` `CHARGE` values are integrated electron populations, not signed net
atomic charges.

The event thresholds in `electrace.toml` are illustrative and system-specific.
Here, event persistence means the matched feature lineage remains observable; it
does not prove that a changed descriptor stays constant or that a particular
chemical interpretation is unique.

See [generation/PROVENANCE.md](generation/PROVENANCE.md) for program versions,
calculation settings, input hashes, and the redistribution boundary.
