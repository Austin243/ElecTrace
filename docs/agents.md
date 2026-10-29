# Coding-agent reference

Install ElecTrace from GitHub, inspect `electrace --help`, and prefer its CLI over
reimplementing VASP discovery, descriptor parsing, periodic matching, or workflow
staging.

For existing outputs:

```bash
electrace init electrace.toml --images /absolute/path/to/frames
electrace doctor --config electrace.toml --json
electrace analyze electrace.toml --json
```

For missing calculations:

```bash
electrace workflow init electrace-workflow.yaml --images /absolute/path/to/frames
electrace workflow validate electrace-workflow.yaml --json
electrace workflow stage electrace-workflow.yaml --json
```

Staging and validation do not execute scientific programs. Add `--yes` to
`workflow run` or `workflow submit` only after the user authorizes the resolved
commands. Prepare Slurm scripts without `--yes`, review them, then submit with
`--yes --force` so the reviewed generated scripts may be replaced.

For spin-polarized runs, `electrace volume CHGCAR --split-spin` prepares the Bader
spin-population run described in [Using ElecTrace](workflows.md#spin-polarized-runs),
and `electrace volume AECCAR0 --add AECCAR2` writes the all-electron Bader reference.
A workflow runs both per frame with `volume` steps, Bader `spin_populations`, and a
critic2 step with `seeds: periodic`, as in
[All-electron density analysis](workflows.md#all-electron-density-analysis).
Never assume an `ISPIN` value; missing spin populations stay missing. Likewise never
assume a ZVAL: Bader net charges come only from each frame's `POTCAR` or `OUTCAR`, as
described in [Net atomic charges](workflows.md#net-atomic-charges).

After outputs exist, run `electrace workflow analyze MANIFEST --json`. Surface
warnings and missing evidence. Never treat Bader/DDEC values as definitive oxidation
states, ICOHP as a transferable bond energy, or a filesystem-complete job as proof of
scientific convergence.

The installable skill is in [`skills/electrace`](https://github.com/Austin243/ElecTrace/tree/main/skills/electrace).
