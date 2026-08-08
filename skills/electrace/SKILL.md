---
name: electrace
description: Track Bader, BadELF, ELF/QTAIM, LOBSTER ICOHP/ICOBI, or DDEC evidence across ordered VASP NEB images and structure series with ElecTrace. Use for selecting frames, analyzing existing outputs, inspecting ELFCAR/CHGCAR grids, staging local or Slurm calculations, collecting results, and reporting changes across a path. Do not use as a scientific-convergence or formal-oxidation-state oracle.
---

# Use ElecTrace

Use the installed `electrace` CLI as the deterministic execution layer. Do not recreate
its parsers, periodic matching, workflow staging, or report generation in ad hoc code.

## Start safely

1. Work in the user's existing environment or create a project-local virtual environment.
2. Check the installed interface before constructing commands:

   ```bash
   electrace --version
   electrace --help
   electrace doctor --json
   ```

3. If the command is missing, offer this public-source installation and install only in
   an environment the user has authorized:

   ```bash
   python -m pip install "git+https://github.com/Austin243/ElecTrace.git"
   ```

4. Determine whether supported descriptor outputs already exist or must be calculated.
5. Inspect first. Use planning and staging before any external execution.

Read [references/workflows.md](references/workflows.md) when selecting capabilities,
constructing a workflow manifest, or checking supported scientific boundaries.

## Analyze existing evidence

Use this route when the ordered frames already contain supported Bader/BaderKit,
critic2, LOBSTER, Chargemol, or normalized ElecTrace descriptor files.

```bash
electrace init electrace.toml --images /absolute/path/to/frames --json
# Review electrace.toml and configure descriptor_source/descriptor_dir.
electrace doctor --config electrace.toml --json
electrace analyze electrace.toml --json
electrace inspect electrace-results --json
```

Treat the generated `analysis.json` as the canonical result. Treat CSV and HTML as
views. Report warnings, missing evidence, ambiguities, frame coverage, and provenance
alongside event summaries. Do not silently switch from strict to permissive analysis.

Top-level analysis uses all frames discovered by its TOML configuration. Use a workflow
manifest when the user asks for an explicit subset or every nth frame.

## Prepare missing calculations

Use this route when VASP or descriptor calculations must be staged across selected
frames.

```bash
electrace workflow init electrace-workflow.yaml --images /absolute/path/to/frames --json
# Review executables, templates, dependencies, resources, scheduler, and frames.
electrace workflow validate electrace-workflow.yaml --json
electrace workflow stage electrace-workflow.yaml --json
electrace workflow status electrace-workflow.yaml --json
```

Validation resolves the complete plan without writing anything. Staging then writes an
isolated, marker-protected workspace but runs no external program. Inspect the resolved
commands, generated job records, and inputs before execution.

For local execution, add `--yes` only when the user explicitly authorizes all staged
commands:

```bash
electrace workflow run electrace-workflow.yaml --yes --max-parallel 2 --json
```

For Slurm, prepare scripts without submitting, review them, then submit only with
explicit authorization:

```bash
electrace workflow submit electrace-workflow.yaml --job-name electrace --json
# Review scripts and scheduler directives.
electrace workflow submit electrace-workflow.yaml --job-name electrace --yes --force --json
```

If authorized submission exits `1`, inspect `error.details.partial_submissions` and
`record_path`, then read `slurm/submissions.json`. Do not blindly resubmit scripts whose
job IDs were already accepted. ElecTrace blocks a new submission while that journal
exists, including with `--force`; remove or archive it only after reconciliation.

After expected outputs exist:

```bash
electrace workflow status electrace-workflow.yaml --json
electrace workflow analyze electrace-workflow.yaml --json
```

Do not assume filesystem completion proves electronic, projection, grid, or scientific
convergence. Inspect external-tool outputs and surface failures.

## Plan and inspect without execution

- Use `electrace plan PLUGIN FRAME --capability CAPABILITY --json` for one command plan.
- Add `--all-frames` to plan across a discovered path.
- Use `electrace volume ELFCAR --json` to summarize a plain VASP scalar grid.
- Use `electrace volume ELFCAR --sample X,Y,Z --json` for periodic fractional sampling.
- Use `electrace workflow validate MANIFEST --json` for a strictly read-only resolved plan.
- Use `electrace workflow status MANIFEST --json` for machine-readable filesystem status.

With `--json`, consume the single `electrace.cli.v1` envelope. Check `ok` and the process
exit code before reading `data`; on failure, read `error.code` and `error.message`. Treat
`warnings` and `artifacts` as first-class outputs rather than scraping terminal prose.

Planning never executes a tool. `workflow submit` without `--yes` writes Slurm scripts
but does not call `sbatch`.

## Preserve scientific and execution boundaries

- Treat Bader and DDEC values as charge/electron-population evidence, not definitive
  formal oxidation states.
- Treat ICOHP/ICOBI and topology events as method-dependent evidence, not unique bond
  energies, mechanisms, or identities.
- Never invent or redistribute a `POTCAR`, executable, credential, or private input.
- Never add `--yes`, `--force`, or `--permissive` merely to make a command succeed.
- Never invent a `chargemol` workflow step; ElecTrace can plan Chargemol and ingest
  existing DDEC results, but `electrace.workflow.v1` does not stage it.
- Do not combine BaderKit Bader and BadELF atomic records for the same frame unless the
  duplicate normalized atom-ID conflict has been resolved deliberately.
- Preserve negative and missing evidence. Stop on schema, provenance, semantic-family,
  or ambiguity errors instead of fabricating a correspondence.
