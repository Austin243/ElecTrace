# ElecTrace documentation

ElecTrace is one research-software project with a deliberately focused 0.1 release
and a broader scientific roadmap. This documentation distinguishes implemented
capabilities from planned or scientifically unvalidated work.

## Start here

1. [Quick start](quickstart.md) — install, run the deterministic demo, and initialize
   an ordered VASP path.
2. [External calculation workflows](workflows.md) — stage, explicitly execute or
   submit, collect, and analyze a VASP→descriptor workflow.
3. [Scientific scope and limitations](scientific-scope.md) — understand what a
   ElecTrace event does and does not mean.
4. [Synthetic benchmark](benchmarking.md) — run and interpret the versioned offline
   regression cases without mistaking them for physical validation.
5. [Reproducibility](reproducibility.md) — preserve enough information to audit and
   repeat an analysis.
6. [Pre-release namespace migration](migration.md) — update development environments,
   scripts, configurations, and serialized artifacts for `0.1.0a2`.

## Design and integration

- [Architecture](architecture.md)
- [Plugin protocol v1](spec/plugin-protocol-v1.md)
- [`electrace.features.v1` JSON Schema](https://github.com/Austin243/ElecTrace/blob/main/schemas/electrace.features.v1.schema.json)
- [`electrace.workflow.v1` JSON Schema](https://github.com/Austin243/ElecTrace/blob/main/schemas/electrace.workflow.v1.schema.json)
- [Public workflow YAML example](https://github.com/Austin243/ElecTrace/blob/main/examples/electrace.workflow.yaml)
- [`electrace.benchmark.v0.1` JSON Schema](https://github.com/Austin243/ElecTrace/blob/main/schemas/benchmark-v0.1.schema.json)
- [Example normalized feature document](examples/electrace.features.v1.example.json)

## Project direction

- [Roadmap](roadmap.md)
- [Governance](governance.md)
- [Prior-art context and acknowledgements](sources.md)
- [Contributing guide](https://github.com/Austin243/ElecTrace/blob/main/CONTRIBUTING.md)
- [Changelog](https://github.com/Austin243/ElecTrace/blob/main/CHANGELOG.md)

## Versioning policy

Code follows semantic versioning after the first tagged release. Data and plugin
contracts are versioned independently through their `schema_version` field. A reader
must reject an unknown schema identifier rather than guessing. Extensions belong in
the designated `attributes` and `metadata` maps; changing the closed envelope or the
meaning or type of an existing field requires a new contract identifier.
