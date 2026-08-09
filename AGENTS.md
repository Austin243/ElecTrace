# ElecTrace contributor guide for coding agents

ElecTrace tracks atoms and electronic features across ordered periodic VASP paths. It
can stage and explicitly launch user-supplied DFT and analysis executables, but it does
not implement those scientific codes and must not present a matching result as unique
chemical truth.

## Before changing code

- Read `README.md`, `docs/workflows.md`, and `docs/scientific-scope.md`.
- Preserve the row-vector convention `cartesian = fractional @ cell`.
- Treat frame-local feature identifiers as local only; persistent identity belongs
  to the matching engine.
- Preserve missing evidence as missing. Never silently replace an unavailable
  charge, volume, overlap, or descriptor with zero.
- Keep plugin planning non-executing. External tools may run only through the separate,
  auditable workflow boundary with explicit user authorization. Never redistribute
  BaderKit, critic2, LOBSTER, Chargemol, VASP, pseudopotentials, or unpublished
  structures.

## Development commands

```bash
python -m pip install -e ".[dev,all,docs]"
ruff check .
ruff format --check .
mypy src/electrace
pytest --cov=electrace --cov-report=term-missing
mkdocs build --strict
python -m build
electrace demo --output .electrace-demo
```

The deterministic demo is a regression fixture, not scientific validation.

## Layout

- `src/electrace/models.py`: serializable scientific domain models.
- `src/electrace/geometry.py`: periodic and deformation-aware geometry.
- `src/electrace/matching.py`: atom/feature assignment and split/merge candidates.
- `src/electrace/events.py`: event evidence and consecutive-frame persistence.
- `src/electrace/io/`: VASP discovery, parsing, descriptor normalization.
- `src/electrace/plugins/`: capability checks and non-executing command plans.
- `src/electrace/workflow/`: manifests, staging, explicit execution, and collection.
- `src/electrace/pipeline.py`: end-to-end analysis and artifact bundle.
- `src/electrace/report.py`: offline self-contained HTML report.
- `schemas/`: versioned machine-readable contracts.

## Scientific change requirements

Any change to matching costs, thresholds, conservation logic, confidence, or event
semantics must include:

1. focused unit tests;
2. an adversarial or boundary case;
3. an explanation of the scientific assumption;
4. unchanged or intentionally versioned output schemas;
5. a changelog entry when behavior visible to users changes.

Prefer explicit uncertainty and structured warnings over confident guesses.
