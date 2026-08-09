# Changelog

## Unreleased

### Added

- Public BSD-3-Clause packaging under the ElecTrace/`electrace` name.
- Ordered VASP path discovery, periodic atom and feature matching, event evidence,
  provenance, and JSON/CSV/HTML reports.
- Normalization for canonical feature JSON and primary BaderKit, critic2, LOBSTER,
  Bader, and Chargemol outputs.
- YAML workflows for selected frames, source-isolated staging, explicit local or
  Slurm execution, output collection, and analysis.
- Read-only `electrace workflow validate --json`, a compact coding-agent guide, and
  an installable ElecTrace skill.
- Streaming plain VASP grid summaries and periodic sampling through `electrace volume`.

### Changed

- Machine-readable command results use the versioned `electrace.cli.v1` envelope when
  `--json` is supported and requested.
- External execution requires explicit `--yes`; plugin planning remains non-executing.
- Split/merge candidates require compatible charge or volume conservation evidence.
- Periodic geometry and assignment reuse reduce repeated matching work; SciPy remains
  an optional accelerator.
- The lean pre-release surface omits legacy parser aliases, the synthetic benchmark,
  HDF5-wrapped JSON, exhaustive alternative-assignment enumeration, and topology-
  specific hysteresis.
- Supported and tested Python versions are 3.11–3.13.

[Unreleased]: https://github.com/Austin243/ElecTrace/commits/main
