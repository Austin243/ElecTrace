# Changelog

All notable changes to this project will be documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and released versions will
follow [Semantic Versioning](https://semver.org/) after the initial development phase.

## [Unreleased]

### Changed

- Standardized opt-in machine output on the versioned `electrace.cli.v1` success and
  error envelope, including parse-time CLI failures and stable exit semantics; the
  Typer minimum is now 0.20, the oldest validated release for this command surface.
- Published the source under the BSD 3-Clause License with clean public package and
  citation metadata.
- Consolidated the distribution, Python package, command, configuration filename,
  schema identifiers, demo paths, result directories, and provenance fields under the
  single ElecTrace/`electrace` identity for version `0.1.0a2`.
- This is an intentional breaking pre-release namespace change. No compatibility
  aliases are provided because no earlier tag or release was published; development
  environments and generated artifacts should be reinstalled or migrated.
- Added explicit local, external, and merged descriptor-source policies with duplicate
  feature rejection, path-wide configured diagnostics, and deterministic batch command
  planning.
- Wired configured descriptor scales and event thresholds, sustained property-state
  persistence, hysteresis, validated high/medium confidence cutoffs, and a validated
  minimum event-confidence filter into matching and event classification.
- Require at least one complete, semantically compatible, tolerance-passing charge or
  volume channel before promoting split/merge hyperedges. Proximity-only, partial,
  incompatible, and failed-channel candidates now remain unpromoted with structured
  rejection diagnostics.
- Detect atom and feature ambiguities by verifying complete alternative assignments
  within a configurable cost gap. Strict analysis now fails before publication;
  permissive analysis records warnings and suppresses affected mapping, lineage,
  birth/death, property-change, and topology claims. Hyperedge-equivalent backbone
  ties are retained as resolved evidence rather than false ambiguity failures.
- Keep plugin planning and top-level analysis non-executing while adding a separate,
  explicit workflow boundary: local execution and Slurm submission now require
  `--yes`, and preparing Slurm scripts remains non-executing.
- Keep the default installation lightweight by using the bundled assignment solver;
  SciPy is now an optional `accelerate` extra, and unused ASE, mkdocstrings, and
  setuptools-stub dependencies have been removed from the package extras.
- Stream plain VASP volumetric grids, bound descriptor and workflow-record reads
  during I/O, require valid UTF-8 evidence, and publish generated text atomically
  without following destination symlinks.
- Reuse periodic-cell factorizations and indexed feature lookups across matching and
  event tracking, substantially reducing repeated geometry work without changing
  scientific thresholds, event semantics, or result schemas.
- Make workflow restaging transactional, validate serialized jobs against the hashed
  manifest before execution, and use bounded heap-based DAG scheduling and batch
  Slurm preflight to avoid partial or quadratic large-workflow behavior.
- Report only the tested CPython 3.11–3.13 range as supported in `electrace doctor`.

### Added

- Added strictly read-only `electrace workflow validate --json` planning for resolved
  frames, commands, inputs, outputs, dependencies, scheduler settings, and warnings.
- Added a compact coding-agent guide and installable ElecTrace skill for routing
  existing-output analysis and approval-gated local or Slurm workflows through the
  public CLI.
- Added a deterministic `electrace benchmark` command, versioned benchmark JSON
  schema, and 15 labeled synthetic cases spanning periodic wrapping, coordinate and
  cell perturbations, ordering, direct events, conservation-backed split/merge,
  negative controls, semantic incompatibility, and ambiguity handling. The report
  explicitly identifies itself as a regression suite rather than scientific
  validation.
- Added the versioned `electrace.workflow.v1` YAML contract, public JSON Schema and
  example, canonical index/slice frame selection, marker-protected source-isolated
  staging, dependency-aware local execution, reviewed Slurm script preparation and
  submission, filesystem status, validated descriptor collection, and analysis of
  the isolated collected series.
- Added `electrace volume` for strict, read-only summaries and periodic sampling of
  plain VASP scalar grids.
- Initial ElecTrace foundation with a focused 0.1 ordered-path workflow.
- Ordered VASP path models, plugin adapter boundary, and feature normalization.
- Periodic matching and evidence-oriented event-analysis foundations.
- Command-line workflows for environment diagnosis, demos, initialization, analysis,
  and result inspection.
- Scientific-scope, protocol, reproducibility, governance, and contributor
  documentation.
- Python 3.11–3.13 CI, repository templates, pre-commit hooks, and dependency updates.
- Documentation CI validation of the feature-schema example and the deterministic
  demo's generated analysis bundle.

[Unreleased]: https://github.com/Austin243/ElecTrace/commits/main
