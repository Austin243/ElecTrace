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

After outputs exist, run `electrace workflow analyze MANIFEST --json`. Surface
warnings and missing evidence. Never treat Bader/DDEC values as definitive oxidation
states, ICOHP as a transferable bond energy, or a filesystem-complete job as proof of
scientific convergence.

The installable skill is in [`skills/electrace`](https://github.com/Austin243/ElecTrace/tree/main/skills/electrace).
