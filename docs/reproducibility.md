# Reproducibility

An ElecTrace result is reproducible only when another researcher can reconstruct the
ordered frames, external-tool outputs, normalized feature records, configuration,
and software environment that produced it.

## Archive with every analysis

### Source identity

- ordered frame index and label;
- selected `POSCAR` or `CONTCAR` path and SHA-256 digest;
- cell, periodic boundary convention, atom symbols, and atom order;
- hashes of every external-tool input consumed;
- repository commit or release tag.

### Electronic-structure provenance

- code and version;
- exchange-correlation functional;
- pseudopotential/PAW dataset identifiers and permitted checksums;
- plane-wave and augmentation cutoffs;
- k-point sampling, smearing, charge, and spin settings;
- SCF and ionic convergence thresholds;
- NEB spring/optimizer settings and image energies;
- density/ELF grid dimensions and any interpolation.

Do not redistribute licensed pseudopotentials. Record identifiers and hashes only
when redistribution is prohibited.

### Adapter provenance

- adapter name and ElecTrace version;
- external executable name, resolved path, and version;
- full argument vector (redacted only for secrets, which should not be present);
- working-directory policy and standard-input digest;
- required input and expected output paths;
- raw output hashes, parser warnings, and normalized schema version.

### Analysis provenance

- complete configuration file;
- all default values materialized or the exact code revision defining them;
- matching costs, descriptor scales, event and confidence thresholds, hysteresis,
  sustained-state persistence, and tie-breaking rules;
- deterministic random seed, if a future algorithm requires one;
- warnings, suppressed claims, and manual overrides.

## Recommended bundle layout

```text
analysis-bundle/
├── README.txt
├── electrace.toml
├── manifest.json
├── environment/
│   ├── python.txt
│   ├── packages.txt
│   └── external-tools.json
├── frames/
│   └── frame-index.json
├── normalized/
│   └── *.features.json
├── results/
│   ├── events.json
│   └── events.csv
└── report/
    └── index.html
```

Raw DFT data can be referenced by content hash and repository/archive identifier when
it is too large or cannot legally be redistributed.

## Convergence ladder

For each central scientific claim, rerun a small targeted ladder:

1. baseline calculation and plugin settings;
2. finer density/ELF grid;
3. tighter electronic convergence;
4. denser path images around the proposed event;
5. perturbed matching and event thresholds;
6. an independent descriptor or analysis method where meaningful.

Record outcomes even when the event disappears. A result that survives this ladder
has stronger numerical support; it is still method dependent.

## Determinism check

Run the same configuration twice in clean output directories. Machine-readable
outputs should be byte-identical after excluding explicitly volatile provenance such
as wall-clock timestamps and absolute scratch paths. If they are not, identify and
record the source of nondeterminism.

## Data sharing checklist

- Remove credentials, usernames, private hostnames, and proprietary path fragments.
- Check licenses before sharing external-tool outputs or pseudopotentials.
- Prefer open, documented fixtures for tests and tutorials.
- Publish cryptographic hashes for data hosted separately.
- Include `CITATION.cff`, the ElecTrace version, and citations for every external
  scientific tool used.
