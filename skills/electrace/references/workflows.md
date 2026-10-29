# ElecTrace workflow reference

## Frame selectors

Use `all`, comma-separated indices, or Python-style slices: `0,3,7`, `1:8`,
`0:10:2`, or `0,2:9:2`. Selections preserve expression order and reject duplicates.

## Capabilities

| Goal | Plugin/step | Capability | Output |
| --- | --- | --- | --- |
| AECCAR0 + AECCAR2 Bader reference | `volume` step | `aeccar_sum` | `CHGCAR_sum` (not collected) |
| Spin split | `volume` step | `split_spin` | `CHGCAR_tot`, `CHGCAR_mag` (not collected) |
| Bader populations | `bader` | `bader_charges` | `ACF.dat` |
| Spin populations | `bader` | `spin_populations` | `spin/ACF.dat` |
| BaderKit populations | `baderkit` | `bader_charges` | `bader.json` |
| BadELF/NNA evidence | `baderkit` | `elf_basins` or `non_nuclear_attractors` | `badelf.json` |
| QTAIM/ELF critical points | `critic2` | `qtaim_critical_points` or `non_nuclear_attractors` | `critic2.features.json` |
| Integrated COHP/COBI | `lobster` | `cohp` or `cobi` | `ICOHPLIST.lobster` or `ICOBILIST.lobster` |
| DDEC charges | existing output or planning only | `ddec_charges` | DDEC charge output |

Use `electrace plan PLUGIN FRAME --capability CAPABILITY --json` to inspect a command
without execution. Add `--all-frames` for a path-wide plan. Use `electrace volume
ELFCAR --json` to inspect a VASP scalar grid.

For a collinear `ISPIN = 2` run, run `electrace volume CHGCAR --split-spin` in the
frame directory, then `bader CHGCAR_mag -ref CHGCAR_tot` inside `spin/`. With
`AECCAR0` and `AECCAR2`, `electrace volume AECCAR0 --add AECCAR2` writes `CHGCAR_sum`;
use it as `-ref` for both the charge and the spin run. A workflow `spin_populations`
step needs `reference_file`, and validation requires a `bader_charges` step with the
same basins. The split refuses one-grid (`ISPIN = 1`), truncated, and non-collinear
files, and the sum refuses files whose grid, lattice, species, or positions differ.
critic2 `seeds: periodic` (`seed_distance` 6 bohr, `seed_points` 20, `seed_triplets`
true) writes `critic2.in` at staging for the all-electron density. Analysis attaches
`spin/ACF.dat` to Bader atom features as `spin`. Spin-resolved LOBSTER lists give
`icohp` or `icobi` as the sum of both channels plus `_up` and `_down` values.
LOBSTER bond values come from atom-pair rows; orbital-resolved rows are skipped. A
bond to an atom's own periodic image lists that atom once, and `lattice_translation`
names the image; LOBSTER's mirrored row for the same bond (translation −T) is merged
into that feature. `lattice_translation_atom` names the atom the translation moves.

Analysis writes `bonds.csv`: one row per atom pair, periodic image, and frame for
features tied to exactly two atoms. Bader and BaderKit atom populations gain
`net_charge` (ZVAL − population) only when the frame directory's `POTCAR` or `OUTCAR`
gives a ZVAL for each `POSCAR` species block in order. `workflow analyze` collects
the `POTCAR` the VASP step used; a VASP 4 `POSCAR` without species names gets no net
charges.

Current boundaries: external programs and pseudopotentials are user-supplied;
critic2 integration reports critical-point topology rather than integrated ELF basin
populations; periodic critic2 seeds assume a VASP single point at the staged
structure; non-collinear spin is unsupported; and workflow status reports files
rather than scheduler or scientific convergence.
