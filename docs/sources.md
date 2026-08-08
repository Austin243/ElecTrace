# Prior-art context and acknowledgements

ElecTrace is intended to compose with—not replace—the electronic-structure and
trajectory-analysis ecosystem. This page records the sources that shaped the initial
concept survey in August 2026. It is context, not an exhaustive or legally conclusive
prior-art review; unpublished and domain-specific workflows may overlap.

## Electronic-structure analysis

- [BaderKit](https://sweav02.github.io/baderkit/) provides workflows around Bader,
  ELF, critical-point, and electride analysis.
- [critic2](https://aoterodelaroza.github.io/critic2/) analyzes scalar fields in
  molecules and crystals, including QTAIM-related topology.
- [LobsterPy](https://github.com/JaGeo/LobsterPy) automates analysis and visualization
  of LOBSTER bonding outputs such as COHP/COBI descriptors.
- [baderVis](https://github.com/koerper/baderVis) motivated comparison with existing
  Bader-charge visualization along VASP NEB paths.

ElecTrace adapters do not include these programs. Users are responsible for their
installation, citation, licensing, and scientifically appropriate configuration.

## Trajectories and pathways

- [ReacNetGenerator](https://docs.deepmodeling.com/projects/reacnetgenerator/en/latest/)
  and [ChemTraYzer2](https://www.scm.com/doc/Workflows/ChemTraYzer2/ChemTraYzer2.html)
  provide important reference points for deriving reaction information from
  coordinate or bond-order trajectories.
- [ASE NEB/AutoNEB](https://docs.ase-lib.org/ase/neb.html) and
  [TSASE SSNEB](https://theory.cm.utexas.edu/tsase/ssneb.html) provide pathway
  optimization context. ElecTrace analyzes an ordered path; it does not optimize one.

## Motivating application

The initial concept was motivated in part by a high-pressure electride study whose
methodology analyzes selected AIMD snapshots and aggregates changing non-nuclear
features. See the [study preprint](https://arxiv.org/html/2510.01583v1). The proposed
long-term ElecTrace contribution is to test whether persistent pocket identities,
lifetimes, diffusion, splitting, and recombination can be recovered instead of
reducing the ensemble to an average.

That application remains a research target. Its mention is not validation of the
software and does not imply endorsement by the study authors.

## Citation practice

A paper or dataset produced with ElecTrace should cite:

1. the archived ElecTrace release and its `CITATION.cff` metadata;
2. the electronic-structure code and methodology;
3. every external analysis tool actually used;
4. underlying methods for Bader/DDEC, ELF/QTAIM, COHP/COBI, or other descriptors as
   required by those tools' documentation;
5. the archived input/output dataset or its content-addressed manifest.

Please report missing related software or attribution through a documentation issue.
