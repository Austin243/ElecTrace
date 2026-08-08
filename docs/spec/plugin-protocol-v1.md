# Plugin protocol v1

Status: **pre-release contract for ElecTrace 0.1**

This specification defines the normalized JSON boundary between an ElecTrace adapter
and the correspondence engine. The normative schema identifier is
`electrace.features.v1`.

The key words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are to be interpreted as
requirements for interoperable implementations.

## Design goals

- Keep external scientific tools outside the core dependency graph.
- Preserve enough provenance to audit every normalized value.
- Prevent a frame-local parser identifier from becoming an accidental cross-frame
  identity.
- Use one periodic coordinate and atom-index convention.
- Permit tool-specific evidence without changing the core envelope.
- Make command execution an explicit policy decision, not a parser side effect.

## Adapter lifecycle

An adapter has two independent responsibilities:

1. **Plan:** inspect configuration and return a command plan.
2. **Parse:** read declared outputs and return a validated
   `electrace.features.v1` document.

Planning MUST NOT launch an executable or modify a source calculation. A command plan
contains:

| Field | Meaning |
| --- | --- |
| `plugin` | Registered adapter name. |
| `capability` | Requested scientific result family. |
| `argv` | Non-empty argument vector. It is never a shell command string. |
| `cwd` | Explicit working directory for an authorized executor. |
| `stdin` | Exact standard-input content or an explicit absence. |
| `stdout_path` | Optional explicit output-capture path; never a shell redirection. |
| `required_inputs` | Files that MUST exist before execution. |
| `expected_outputs` | Files the parser expects after successful execution. |
| `environment` | Explicit environment additions; secrets MUST NOT be embedded. |
| `notes` | Human-readable, non-authoritative plan warnings or instructions. |

The serialized audit form also includes `missing_inputs`, computed from the declared
requirements at inspection time. It is diagnostic state, not part of the executable
instruction.

`electrace plan PLUGIN FRAME` serializes one plan. With `--all-frames`, `FRAME` is an
ordered VASP path root and the CLI serializes a deterministic batch containing one
independent plan per discovered frame. Batch planning does not change the plugin
contract and still performs no execution. Its envelope contains `plugin`, `path`, and
a `plans` array; `plans` preserves discovered frame order, and each element pairs a
canonical `frame` record with its `command` plan.

The executor—not the adapter API—decides whether to run a plan. An executor SHOULD
resolve the executable, apply a sandbox appropriate to the data, capture versions and
exit status, verify the required inputs, and avoid overwriting source files. A parser
MUST NOT search arbitrary undeclared locations to make a failed plan appear
successful.

## Normalized document

The complete JSON Schema is
[`electrace.features.v1.schema.json`](https://github.com/Austin243/ElecTrace/blob/main/schemas/electrace.features.v1.schema.json).
A valid document has this shape:

```json
{
  "schema_version": "electrace.features.v1",
  "producer": {
    "name": "critic2-adapter",
    "version": "0.1.0"
  },
  "frame": {
    "index": 5,
    "label": "05"
  },
  "features": [
    {
      "id": "bcp-12",
      "kind": "bond_critical_point",
      "fractional_position": [0.125, 0.5, 0.875],
      "neighbor_atom_indices": [3, 8],
      "attributes": {
        "rho_e_per_angstrom3": 0.42
      }
    }
  ],
  "metadata": {}
}
```

Only `schema_version`, `producer`, and `features` are required at the root. `frame`
and `metadata` are optional. Producers SHOULD include `frame` and provenance metadata
for any record persisted outside a parent analysis manifest.

### `schema_version`

The value MUST be exactly `electrace.features.v1`. Readers MUST reject any other
identifier unless they implement an explicit migration. They MUST NOT guess based on
field similarity.

### `producer`

`producer.name` identifies the adapter or writer, not merely the upstream executable.
`producer.version` SHOULD be present when known. The upstream executable, its version,
and the structured argument vector belong in `metadata.provenance`.

### `frame`

`frame.index`, when present, is the zero-based position in the canonical ordered
path. `frame.label`, when present, is a display label such as an image directory name.
Neither establishes identity outside the parent analysis manifest. At least an index
or label SHOULD be included in records written to disk.

### `features`

Each feature MUST contain:

- `id`: a non-empty identifier unique within this document;
- `kind`: a non-empty feature kind normalized to lower case;
- `fractional_position`: exactly three finite fractional-coordinate values.

Writers SHOULD wrap periodic fractional coordinates into the half-open interval
`[0, 1)`. Readers accept finite unwrapped values because a legitimate upstream
representation may preserve image offsets.

Optional `neighbor_atom_indices` are unique, zero-based indices into the canonical
atom order. Optional `attributes` are a JSON object for descriptor-specific evidence.

`id` is frame-local. Only the correspondence engine may assign a persistent track
identifier spanning frames.

The recommended vocabulary is `atomic_basin`, `elf_basin`,
`non_nuclear_attractor`, `bond_critical_point`, `ring_critical_point`,
`cage_critical_point`, `bond_path`, `bond_descriptor`, `atomic_charge`, and `other`.
The core accepts additional normalized kinds for third-party tools. Writers SHOULD
reuse the recommended spelling and MUST document genuinely new kinds in `metadata`.

Optional standard scalars are:

- `charge`: a finite value in elementary-charge units;
- `volume`: a non-negative finite value in cubic ångström.

Because upstream tools expose populations and charges with different sign and
reference conventions, any document containing `charge` MUST record an unambiguous
`metadata.charge_convention`. Values with incompatible conventions MUST NOT be fused
or differenced as though they were equivalent.

Optional `voxel_indices` provides unique integer grid triplets for features with
voxel support. The grid shape, origin, ordering, and periodic convention MUST be
recorded in `attributes` or frame metadata; a voxel index has no portable meaning
without them.

Attribute keys SHOULD encode their quantity and unit, for example
`rho_e_per_angstrom3` or `icohp_ev`. A producer-specific key SHOULD be namespaced when
its semantics are not common, for example `critic2:laplacian`. Missing evidence is
omitted or represented as JSON `null` where the schema permits it; it MUST NOT be
encoded as zero, `NaN`, `Infinity`, or a string sentinel.

### `metadata`

`metadata` is the extension and provenance object. It MAY contain arbitrary valid
JSON, but producers SHOULD include:

```json
{
  "provenance": {
    "external_tool": {"name": "critic2", "version": "..."},
    "source_sha256": {"CONTCAR": "..."},
    "argv": ["critic2", "critic2.in"],
    "input_sha256": "...",
    "output_sha256": "..."
  },
  "charge_convention": "description of reference and sign",
  "warnings": []
}
```

Paths in portable records SHOULD be project-relative or paired with content hashes.
Credentials and sensitive host paths MUST NOT be recorded.

## JSON rules

- Encoding MUST be UTF-8.
- The root MUST be a single JSON object.
- Numbers MUST be finite; JSON extensions for `NaN` and infinity are forbidden.
- Duplicate object keys are forbidden.
- Array order is significant for `features` and MUST be deterministic.
- Producers SHOULD serialize with stable key ordering for reproducible diffs.
- The top-level and feature envelopes are closed. Tool-specific data SHOULD live in
  `attributes` or `metadata` rather than creating a near-synonym for a standard field.

## Validation and failure behavior

Writers MUST validate before publishing a normalized document. Readers MUST validate
before scientific use.

A malformed envelope, empty kind, duplicate feature ID, invalid neighbor index, or
non-finite value is an error. Extension kinds are accepted. A missing optional
scientific quantity is not an error, but downstream event evidence that requires it
remains missing.

Parser warnings belong in `metadata.warnings` and must survive into reports. A parser
MUST NOT fabricate an empty successful document after an external-tool or parse
failure. The orchestration layer records failed/partial runs separately and
suppresses dependent scientific claims.

## Compatibility

The core fields and their meanings are frozen for v1. New tool-specific attributes or
metadata are compatible when old readers can ignore them safely. Adding or changing
an envelope field, changing a unit/convention, or changing requiredness requires a new
schema identifier and migration documentation.

## Security notes

- Pass `argv` directly to a subprocess API with shell evaluation disabled.
- Resolve and validate `cwd`, inputs, outputs, and symlinks against the authorized
  project boundary.
- Do not trust frame labels, attribute keys, warnings, or upstream output in HTML.
- Impose size and resource limits on volumetric and JSON input.
- Do not allow a plugin document to select or execute an executable.

See the [security policy](https://github.com/Austin243/ElecTrace/blob/main/SECURITY.md)
for the reporting process and broader threat model.
