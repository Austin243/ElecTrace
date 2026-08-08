# Synthetic benchmark

`electrace benchmark` runs a deterministic, offline regression suite through the
same atom matcher, feature matcher, and transition event detector used by
`electrace analyze`. It provides machine-readable evidence that controlled software
behaviors have not changed unexpectedly.

```bash
electrace benchmark --output electrace-benchmark.json
```

The command returns a non-zero status if any labeled case fails. It refuses to
replace an existing report unless `--force` is supplied. The output conforms to the
[`benchmark-v0.1` JSON Schema](https://github.com/Austin243/ElecTrace/blob/main/schemas/benchmark-v0.1.schema.json).

!!! warning "Regression benchmark, not scientific validation"

    The cases are constructed inputs. A perfect score shows agreement with explicit
    synthetic expectations; it does not estimate accuracy on experimental data,
    independently computed basins, or a physical domain. Event confidence values
    remain deterministic evidence scores rather than calibrated probabilities.

## Versioned contract

Every report records three independent identities:

- `schema_version` identifies the JSON shape;
- `suite.version` identifies the case definitions and expected labels;
- `software_version` identifies the ElecTrace revision that produced the result.

The report intentionally has no timestamp, hostname, or random identifier, so two
runs with the same software and suite are byte-for-byte reproducible. Change the
suite version whenever a case, threshold, or expected label changes. Change the
schema version only when the report contract changes.

## Current coverage

The v0.1 suite contains 15 two-frame cases:

| Behavior | Controlled perturbations |
| --- | --- |
| Periodic matching | Atom and feature wrapping across a cell face |
| Robustness | Coordinate jitter, anisotropic cell deformation, reversed feature order |
| Direct events | Birth, death, and threshold-crossing ICOHP change |
| Hyperedges | Volume-conserving split and merge |
| Negative controls | Missing conservation evidence and failed volume conservation |
| Ambiguity | Exact and near-optimal assignment ties, plus a tie resolved by a conserving hyperedge |
| Semantic gates | Explicitly incompatible charge conventions |

Each case records the expected and observed event identities, atom mapping, feature
mapping, ambiguity decisions, final match relations, and structured hyperedge
rejections. Event scoring uses the exact multiset of `(type, source_ids,
target_ids)` labels. It reports true positives, false positives, false negatives,
precision, recall, and F1. Mapping and ambiguity scores are exact assertion counts.

The built-in baseline is expected to have zero failures. A new failure should be
handled in one of three ways:

1. fix an unintended implementation regression;
2. document and review an intentional scientific-behavior change, then update the
   expected label and suite version;
3. add a more precise case when the old construction was under-specified.

Do not weaken or remove a negative control solely to recover a perfect score.

## What should come next

The synthetic suite is the first validation layer. The next layer must use
redistributable, independently generated electronic-structure fixtures with labeled
expectations and grid-refinement studies. Those fixtures should report performance by
feature family and perturbation regime, not combine every domain into one headline
score. Until that exists, ElecTrace should continue to describe basin lineage as
experimental.

ElecTrace currently has no GPU compute backend. Running this command on a GPU node is
useful as an installation and environment compatibility check, but it does not
exercise GPU kernels or demonstrate GPU acceleration.
