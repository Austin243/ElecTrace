# ElecTrace workflow reference

## Frame selectors

Use `all`, comma-separated indices, or Python-style slices: `0,3,7`, `1:8`,
`0:10:2`, or `0,2:9:2`. Selections preserve expression order and reject duplicates.

## Capabilities

| Goal | Plugin/step | Capability | Output |
| --- | --- | --- | --- |
| Bader populations | `bader` | `bader_charges` | `ACF.dat` |
| BaderKit populations | `baderkit` | `bader_charges` | `bader.json` |
| BadELF/NNA evidence | `baderkit` | `elf_basins` or `non_nuclear_attractors` | `badelf.json` |
| QTAIM/ELF critical points | `critic2` | `qtaim_critical_points` or `non_nuclear_attractors` | `critic2.features.json` |
| Integrated COHP/COBI | `lobster` | `cohp` or `cobi` | `ICOHPLIST.lobster` or `ICOBILIST.lobster` |
| DDEC charges | existing output or planning only | `ddec_charges` | DDEC charge output |
| Spin populations | manual commands, not a workflow step | none | `spin/ACF.dat` |

Use `electrace plan PLUGIN FRAME --capability CAPABILITY --json` to inspect a command
without execution. Add `--all-frames` for a path-wide plan. Use `electrace volume
ELFCAR --json` to inspect a VASP scalar grid.

For a collinear `ISPIN = 2` run, run `electrace volume CHGCAR --split-spin` in the
frame directory, then `bader CHGCAR_mag -ref CHGCAR_tot` inside `spin/`. The split
refuses one-grid (`ISPIN = 1`), truncated, and non-collinear files. Analysis attaches
`spin/ACF.dat` to Bader atom features as `spin`. Spin-resolved LOBSTER lists give
`icohp` or `icobi` as the sum of both channels plus `_up` and `_down` values.

Current boundaries: external programs and pseudopotentials are user-supplied;
critic2 integration reports critical-point topology rather than integrated ELF basin
populations; the spin split is not a workflow step and non-collinear spin is
unsupported; and workflow status reports files rather than scheduler or scientific
convergence.
