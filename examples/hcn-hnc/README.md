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

- 7 ordered frames and two warnings:
  - critic2's Morse sum n − b + r − c is +1 in every frame, not the 0 of a complete
    periodic search, because `generation/critic2.in` discards points where the
    density is below 1e-5 a.u., which removes the vacuum points around the molecule;
  - the frames have no `POTCAR` or `OUTCAR`, so the Bader charges stay electron
    populations;
- 35 feature observations belonging to 5 tracks;
- 20 events: 19 descriptor changes (17 confirmed, 2 right-censored) and one
  confirmed candidate connectivity change;
- 14 bond rows for 3 bonds;
- `analysis.json`, `bonds.csv`, `events.csv`, `feature_tracks.csv`,
  `track_summary.csv`, `report.html`, and `manifest.json` in `electrace-results/`.

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
atomic charges. ElecTrace adds net charges only when a frame's `POTCAR` or `OUTCAR`
gives ZVAL, and this example ships neither.

`bonds.csv` lists the same critical points by atom pair instead of by track:

| Bond | Atoms | Frames |
| --- | --- | --- |
| `B00001` | C0–N1 | 00–06 |
| `B00002` | C0–H2 | 00–02 |
| `B00003` | N1–H2 | 03–06 |

`B00002` ends at frame 02 and `B00003` begins at frame 03. Both rows come from track
`T00005`, but they are different bonds and are never joined. Each row carries the
interatomic distance and critic2's field and Laplacian for that frame.

With `electrace[plot]` installed, draw the three bonds along the scan:

```bash
electrace figure electrace-results --bond C0-H2 --bond N1-H2 --bond C0-N1 --output hcn-hnc
```

The C–H density ends at frame 02 and the N–H density begins at frame 03, while both
distances continue through the scan. The dots between frames 02 and 03 mark `E00004`,
the reassignment, and `E00009`, the density change on the same step. The figure has
no charge or spin panel, because the example has neither net charges nor spin
populations, and a warning says so.

The event thresholds in `electrace.toml` are illustrative and system-specific.
Here, event persistence means the matched feature lineage remains observable; it
does not prove that a changed descriptor stays constant or that a particular
chemical interpretation is unique.

See [generation/PROVENANCE.md](generation/PROVENANCE.md) for program versions,
calculation settings, input hashes, and the redistribution boundary.
