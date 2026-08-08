# Use ElecTrace with coding agents

ElecTrace gives coding agents a reusable command-line workflow for Bader charge trends
across NEB frames, ELF/QTAIM path analysis, and LOBSTER ICOHP/ICOBI changes across an
ordered VASP structure series. It replaces one-off frame discovery, Slurm generation,
output parsing, periodic correspondence, and report code with a tested interface.

## Install the CLI

ElecTrace supports CPython 3.11–3.13. Install the current public source in a virtual
environment:

```bash
python -m pip install "git+https://github.com/Austin243/ElecTrace.git"
electrace --version
electrace doctor --json
```

The built-in demo requires no external scientific program:

```bash
electrace demo --output .electrace-demo --json
electrace inspect .electrace-demo/electrace-results --json
```

## Install the Codex skill

The repository ships an
[`electrace` skill](https://github.com/Austin243/ElecTrace/tree/main/skills/electrace)
that teaches Codex when to analyze existing descriptor outputs, when to prepare a
calculation workflow, which commands are non-executing, and which actions require
explicit user authorization.

Ask Codex:

```text
Use $skill-installer to install the ElecTrace skill from
https://github.com/Austin243/ElecTrace/tree/main/skills/electrace
```

The skill becomes available on the next Codex turn. Invoke it explicitly with
`$electrace`, or let its task description trigger when working with Bader, BadELF,
ELF/QTAIM, LOBSTER, DDEC, VASP NEB frames, or ordered periodic structure series.

## Agent workflow

An agent should start with the installed help and a read-only environment check:

```bash
electrace --help
electrace doctor --json
```

Every operational subcommand accepts `--json`. Successful and handled-error responses
use one compact, unstyled `electrace.cli.v1` document with the fields `schema_version`,
`ok`, `command`, `data` or `error`, `warnings`, and `artifacts`. Help and version output
remain human-readable; request them without `--json`. Exit code `0` means the operation
succeeded, `1` means a completed benchmark or external execution reported failure, and
`2` means the request or a prerequisite was invalid. The public contract is the
[`electrace.cli.v1` schema](https://github.com/Austin243/ElecTrace/blob/main/schemas/electrace.cli.v1.schema.json).
Resolved workflow plans in `data` follow
[`electrace.workflow-validation.v1`](https://github.com/Austin243/ElecTrace/blob/main/schemas/electrace.workflow-validation.v1.schema.json).

If supported descriptor results already exist, it should create and review
`electrace.toml`, run `electrace analyze --json`, and consume `analysis.json` as the
canonical result. If calculations are missing, it should create and review
`electrace-workflow.yaml`, run
`electrace workflow validate electrace-workflow.yaml --json`, then stage the selected
frames and inspect workflow status with `--json`.
Local execution and Slurm submission remain explicit actions; the skill does not add
`--yes` unless the user authorizes the reviewed job scope.

See the [workflow guide](workflows.md) for complete manifest options and the
[scientific scope](scientific-scope.md) for interpretation boundaries.
