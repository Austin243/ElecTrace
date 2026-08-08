# Roadmap

The roadmap is capability-gated. Dates are intentionally omitted: scientific
validation, fixtures, and reproducibility criteria matter more than calendar labels.

## ElecTrace 0.1 — ordered VASP paths

Goal: a useful, auditable foundation for NEB and static pressure series.

- [x] deterministic VASP image discovery and validation;
- [x] canonical periodic frame and atom models;
- [x] inspectable command plans for BaderKit, critic2, LOBSTER, and Chargemol;
- [x] native/normalized evidence parsers and `electrace.features.v1` validation;
- [x] periodic atom/feature correspondence with complete-assignment ambiguity
  diagnostics and strict/permissive failure policy;
- [x] experimental deformation-aware feature costs and voxel overlap;
- [x] birth/death candidates and conservation-gated split/merge hyperedges;
- [x] descriptor events, persistent track records, JSON/CSV, and an HTML report;
- [x] `doctor`, `benchmark`, `demo`, `init`, `plan`, `analyze`, and `inspect` CLI
  workflows;
- [x] Python 3.11–3.13 CI, typing, tests, and reproducibility documentation.

Exit criteria: a clean checkout can run the offline demo; at least one redistributable
ordered-path fixture has expected normalized records; malformed and ambiguous inputs
fail safely; repeat runs are deterministic; scientific limitations appear in the
report.

## ElecTrace 0.2 — validated electronic-feature lineage

Goal: establish where the experimental lineage algorithms are scientifically
reliable beyond atom-centered scalar series.

- [x] versioned synthetic lineage regression harness with exact event, mapping,
  ambiguity, conservation, and negative-control expectations;
- variable-cell stress tests, including large strain and reconstruction;
- curated basin and non-nuclear-attractor lineage benchmarks;
- grid-shape/origin interoperability and voxel-overlap convergence;
- false-event characterization for one-to-many and many-to-one assignments;
- persistence, hysteresis, and automated grid-refinement evidence;
- feature lifetimes, trajectories, and uncertainty flags;
- pressure-series visualizations;
- exploratory Quantum ESPRESSO and CP2K import contracts.

Exit criteria: curated synthetic cases and independent scientific fixtures cover
birth/death/split/merge events; false event rates under grid refinement are reported;
ambiguous lineage remains inspectable.

## ElecTrace 1.0 — sparse trajectory analysis

Goal: apply validated correspondence to selected AIMD and MLIP frames.

- sparse frame selection with explicit selection bias;
- pocket lifetimes, diffusion, splitting, and recombination statistics;
- automated DFT-frame audit of MLIP trajectories;
- calibrated uncertainty on supported benchmark domains;
- pressure–temperature onset maps;
- interactive electronic-autopsy reports;
- export of candidate endpoints or path segments for downstream NEB work.

Exit criteria: versioned public benchmarks, stable data contracts, migration tooling,
performance characterization, and a documented domain of validity.

## Non-goals

ElecTrace will not become a DFT engine, replace upstream electronic-topology tools,
or claim a universal definition of bonding. Integration should remain modular and
scientific conclusions should remain evidence based.
