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

## Paired-plan comparison, 2026-11-27

Private validation on base8ae4725:

- 38 unittest tests passed on Python3.10,3.13,3.14,3.15, including all26 preexisting
  tests and12 comparison tests.
- 250 deterministic paired-plan fixtures compare per-source counts with the
  separate row-materializing oracle. Equal-total source reversals, nested plans,
  source zero transitions, intermediate budget failures and exact large integers
  have explicit checks.
- Ruff lint and format-check passed. Wheel/sdist build passed. A wheel installed
  into a separate Python3.13 environment passed API and actual CLI budget-exit
  checks from outside the source checkout.
- Four private mutants were rejected: total-only equality, reversed loss test,
  candidate-only budget verdict and omitted witnesses.
- No expanded-value equality, engine compatibility or production-use validation
  is claimed. Both plans receive independent visit allowances, not a shared cap.
- Hosted CI and rendered README are publication checks, not completed privately.
