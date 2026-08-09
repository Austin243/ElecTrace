# Contributing to ElecTrace

Focused fixes, parser fixtures, tests, and documentation improvements are welcome.
Open an issue before introducing a new data model, external-tool integration, or
matching algorithm. Report security issues through the process in
[SECURITY.md](SECURITY.md).

## Development

ElecTrace supports CPython 3.11–3.13.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
ruff check .
ruff format --check .
mypy src
pytest
```

Keep pull requests focused. Include tests for changed behavior, update user-facing
documentation when needed, and mention compatibility or scientific consequences.
Matching, event, and confidence changes need both a positive case and a meaningful
counterexample.

CI must not require network access, licensed software, a cluster, or a DFT run.
Use the smallest redistributable fixtures needed to test external-tool adapters, and
never commit credentials, pseudopotentials, proprietary executables, or unpublished
scientific inputs without authorization.

The `electrace.features.v1` format is a public contract. Changes require an updated
schema, example, reader/writer tests, and either a compatible migration or a new
schema identifier.

By contributing, you agree that your work is licensed under the repository's
[BSD 3-Clause License](LICENSE) and that you have the right to submit it. All
contributors must follow the [Code of Conduct](CODE_OF_CONDUCT.md).
