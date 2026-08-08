# Pre-release namespace migration

ElecTrace `0.1.0a2` establishes one product and namespace across the repository. The
project had no earlier tag or published release, so this is a clean pre-release break:
there are no compatibility aliases for development-only names or serialized
identifiers.

## Canonical surface

| Surface | ElecTrace `0.1.0a2` value |
| --- | --- |
| Distribution | `electrace` |
| Python imports | `electrace` |
| Configuration API | `electrace.config.ElecTraceConfig` |
| Command and module entry point | `electrace`; `python -m electrace` |
| Project configuration | `electrace.toml` |
| Example configuration | `examples/electrace.example.toml` |
| Normalized feature contract | `electrace.features.v1` |
| Feature schema | `schemas/electrace.features.v1.schema.json` |
| Analysis schema | `schemas/analysis-v0.1.schema.json`; version `0.1` |
| Bundle manifest contract | `electrace.bundle.v1` |
| HDF5 schema label | `electrace.analysis` |
| Default result directory | `electrace-results/` |
| Default demo directory | `.electrace-demo/` |
| Version field from `doctor --json` | `data.electrace_version` in `electrace.cli.v1` |
| Built-in demo producer | `electrace-demo` |

Existing TOML table names and previously supported keys are unchanged. New checkouts
also expose `inputs.descriptor_source` to select local, external, or merged descriptor
discovery explicitly; newly generated configurations select `auto`. The canonical
filename, generated defaults, program identifiers, and namespaces changed.

## Machine-readable CLI output in 0.1.0a4

Explicit `--json` output now uses the `electrace.cli.v1` envelope on every operational
subcommand. Consumers of the earlier pre-release shapes must read the former
`doctor --json` and `volume --json` payloads below `data`. `plan` and `workflow status`
retain their original raw JSON when `--json` is omitted; adding the flag requests the
new envelope. Help and version output remain human-readable and should be requested
without `--json`.

## Migrate a development checkout

Use a fresh virtual environment so an older editable installation cannot shadow the
renamed package:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
electrace --version
electrace doctor
```

Rename the project configuration to `electrace.toml`, update its `[outputs].directory`
to `electrace-results`, and update scripts to invoke `electrace`. The configuration
contents otherwise retain the same tables and keys.

Normalized descriptor JSON must declare `electrace.features.v1` exactly. Validate
migrated records against `schemas/electrace.features.v1.schema.json`; the reader
intentionally rejects every other schema identifier rather than guessing.

Regenerate result bundles when source inputs remain available. If an existing
development bundle must be retained, keep it as an immutable historical artifact and
do not mix its files with an `electrace.bundle.v1` manifest. ElecTrace does not provide
an automatic artifact-migration command in this pre-release phase.
