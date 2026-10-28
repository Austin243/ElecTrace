# Generation provenance

This directory records how the redistributable HCN-to-HNC example was made. The
seven geometries are original, hand-defined inputs: C and N remain 1.16 Å apart
while H follows a bent path from the carbon side to the nitrogen side in a 16 Å
cubic cell. They are ordered static calculations, not an optimized NEB.

## Programs and calculation

- VASP 5.4.1 CUDA GPU port, built for CUDA 11.6 and NVIDIA `sm_80`
- NVIDIA A100-SXM4-80GB GPU
- PBE PAW potentials, 520 eV plane-wave cutoff, Γ-only sampling
- Static self-consistent calculations with `EDIFF = 1e-7`, `LCHARG`, `LAECHG`,
  and `LELF`; VASP selected a 120 × 120 × 120 fine FFT grid
- Bader 1.04 (2020-02-08), run on the all-electron reference density
- critic2 1.2.2, source commit
  `9731d532c6407d35c75bbce5af449211470437a7`, run on the summed
  `AECCAR0 + AECCAR2` density

All seven VASP calculations reached the requested electronic tolerance. Bader
reported 10.0000 integrated electrons in every frame. Each critic2 run ended
with zero warnings and found the persistent C–N bond critical point plus the
H-related bond critical point that changes from C–H to N–H connectivity.

The committed `OSZICAR`, `ACF.dat`, and `critic2.features.json` files are the
native outputs used by ElecTrace. The compact inputs in this directory are
included for reproducibility. Licensed VASP executables and PAW datasets, and
large intermediate files such as `CHGCAR`, `AECCAR*`, `ELFCAR`, `WAVECAR`, and
`OUTCAR`, are deliberately excluded.

## Input identities

SHA-256 hashes are provided so users with licensed VASP datasets can identify
the exact PAW inputs without redistributing them:

| Input | Identity | SHA-256 |
| --- | --- | --- |
| C PAW | `PAW_PBE C 08Apr2002` | `e324f39f282d3fd6b99a7bff390f4b2441edd3dd3eac607d1f1c01376f3d0dc0` |
| N PAW | `PAW_PBE N 08Apr2002` | `c3b51ab8258f836d3c8d29beefa95be51fc95b5d0ab7aca6cba204207a69162a` |
| H PAW | `PAW_PBE H 15Jun2001` | `c7c1440066f80b3689496d8a45f8df6af183c40e59b514734edddfaa6840a3be` |

Committed generation-input hashes:

| File | SHA-256 |
| --- | --- |
| `INCAR` | `3ee6a1a3ecf5849ba5511d2a8283cdbaa78834a1a6752a92ecfd8bd397887245` |
| `KPOINTS` | `7a129e060ce525eafcc4ac8e632cee2a64426e017633e545583220ddf073d6ab` |
| `critic2.in` | `55492d4116263ca7938d3e376d8f4f4b2447f368accb20713d37d8b4d7a1ab31` |

The scientific outputs are included as an illustrative software example. Their
presence does not imply endorsement by VASP, the Bader program, or critic2.
