# Private validation, November 26, 2026

- 26 unittest methods pass on CPython3.10.12,3.11.18,3.13.18,3.14.10,3.15.2.
- Same26 pass from `/tmp` against an installed wheel in a separate3.13 environment.
- 500 deterministic small generated datasets compare all stage row counts,
  surviving/newly-lost source counts and largest source contributions against a
  separate oracle that actually materializes the synthetic expanded rows.
- Nested sums, sibling products, repeated values, exact row-budget boundaries,
  intermediate peaks, empty/missing/null policies and kept-placeholder children
  have focused tests. Invalid branches behind zero-producing siblings refuse.
- Strict JSON duplicate keys, extreme numeric exponents, UTF-8/BOM, invalid path
  types, plan/record/work/depth caps and cyclic API inputs have rejection tests.
- Five source mutants killed: child product changed to sum; element sum to max;
  budget boundary changed to >=; newly-lost count changed to all zero sources;
  absent keep/drop inverted. The first boundary run survived, prompting an
  explicit per-stage flag assertion. All five then failed as expected.
- CLI normal/budget/error exit0/1/2 and short writes, broken pipes, flush errors
  tested. An actual Linux `/dev/full` redirect exits2.
- Ruff0.17.6 check and format check pass. Wheel and sdist build succeed.
- Synthetic32 independent arrays of100 elements produce exact10**64 rows without
  product materialization:53,463 visits,~0.018s,~51KiB traced allocation peak in
  one local run. This is not a general speed or memory guarantee.

No hosted checks, external users, production use or registry publication yet.
The oracle fixtures establish only the declared model, not engine compatibility.
