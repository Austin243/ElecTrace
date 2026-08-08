# Governance

ElecTrace is maintainer-led research software. Governance is lightweight during the
pre-1.0 phase and is intended to become more formal if the contributor community
grows.

## Roles

- **Maintainers** set scope, merge changes, manage releases and security reports,
  and are accountable for scientific claims made by the project.
- **Contributors** propose code, tests, documentation, benchmarks, or scientific
  validation through reviewed pull requests.
- **Scientific reviewers** may review domain assumptions, fixtures, and
  interpretations without taking on general maintenance duties.

Repository permissions, not a hard-coded list in this document, are the source of
truth for current maintainers.

## Decision process

Routine changes are decided in pull-request review. Material changes require a short
design note in the pull request or `docs/` covering alternatives and migration:

- feature-matching or event semantics;
- a versioned JSON contract;
- support for a new external tool or file format;
- backward-incompatible CLI/configuration changes;
- changes to the scientific domain of validity;
- collection or publication of benchmark data.

Maintainers seek rough consensus. If consensus is not possible, a maintainer records
the decision, rationale, dissent, and conditions for revisiting it. Scientific
uncertainty is documented rather than decided by vote.

## Releases

- Pre-1.0 releases may change quickly but require changelog entries and migration
  notes for user-visible breaks.
- Plugin/data schemas use explicit identifiers and evolve independently of package
  versions.
- A release requires passing CI, a reviewed changelog, an offline demo, and an audit
  of documentation against implemented capabilities.
- Only maintainers publish tags or packages.

## Scientific integrity

Contributors must disclose provenance, manual curation, excluded data, and known
negative evidence. Benchmarks should separate algorithm development cases from
held-out validation cases. Results must not be described as calibrated probabilities
without an explicit calibration study.

Substantive corrections are documented in the changelog and affected reports or
releases are annotated when feasible.

## Conduct and conflicts

Participation is governed by the
[Code of Conduct](https://github.com/Austin243/ElecTrace/blob/main/CODE_OF_CONDUCT.md).
Contributors should disclose financial, institutional, or software-vendor conflicts
that could reasonably affect a scientific or integration decision.

## Security

Security reports follow the
[security policy](https://github.com/Austin243/ElecTrace/blob/main/SECURITY.md).
Maintainers may embargo a fix until affected users can update. Scientific
disagreements that do not expose a security or privacy risk belong in the normal
issue process.
