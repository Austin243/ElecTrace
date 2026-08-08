# Contributing to ElecTrace

Thank you for helping build ElecTrace. The project welcomes software engineering,
documentation, test fixtures, benchmarks, and careful scientific validation.

## Before you start

- Search existing issues and pull requests.
- Use a feature request for a small change or open a design issue before a new data
  model, external-tool integration, or matching/event algorithm.
- Use the scientific-validation template for a disputed interpretation, convergence
  failure, or method-dependent discrepancy.
- Report security-sensitive findings privately as described in [SECURITY.md](SECURITY.md).
- Confirm that you may redistribute every fixture or output you contribute.

## Development setup

ElecTrace supports CPython 3.11–3.13.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
pre-commit install
```

Run the local checks before opening a pull request:

```bash
ruff check .
ruff format --check .
mypy src
pytest -ra --strict-config --strict-markers
pre-commit run --all-files
```

Use `ruff format .` and `ruff check --fix .` for safe automated formatting and lint
fixes. Do not weaken a check simply to make a pull request green; document a narrow
exception when the code genuinely requires one.

## Pull-request expectations

Keep changes focused. A pull request should explain the problem, approach, scientific
impact, validation, and any compatibility or reproducibility consequences.

Every user-visible change should include:

- tests for expected behavior and meaningful failures;
- documentation and a changelog entry;
- deterministic fixtures small enough for the repository;
- migration notes for CLI, configuration, or schema changes;
- explicit disclosure of manual curation, thresholds, and unsupported cases.

Code review is not a substitute for scientific validation. Changes to feature
matching, event semantics, confidence labels, or physical interpretation need a
domain-aware reviewer and evidence across both positive and negative cases.

## Testing external adapters

CI must not require network access, licensed software, a cluster, or a DFT run.
Adapter tests should use inspectable command plans and the smallest legally
redistributable parser fixtures. Tests must verify at least:

- argument boundaries and path handling;
- required and expected file declarations;
- executable/version provenance;
- malformed, missing, partial, and non-finite output behavior;
- conversion to the versioned normalized schema.

Never commit credentials, licensed pseudopotentials, proprietary executables, or
unpublished scientific inputs without documented authorization.

## Data and schema changes

`electrace.features.v1` is a contract, not an incidental serialization. Contract
changes require:

1. a design rationale and compatibility analysis;
2. updated JSON Schema, examples, and protocol documentation;
3. reader and writer tests;
4. a migration path or a new schema identifier;
5. a changelog entry.

Unknown major versions must be rejected. Do not reinterpret an existing field in
place.

## Commit and review hygiene

- Write imperative, specific commit subjects.
- Avoid drive-by formatting or unrelated generated files.
- Keep raw generated reports out of source control unless they are intentional test
  snapshots.
- Link the issue or scientific question being addressed.
- Respond to review with code or a documented rationale; resolve threads only after
  the concern is addressed.

All contributors must follow the [Code of Conduct](CODE_OF_CONDUCT.md). By submitting
a contribution, you confirm that you have the right to submit it and agree that it
will be licensed under the repository's [BSD 3-Clause License](LICENSE).
