# explodeforecast

Count a declared tree of JSON array expansions before building the product rows.

A root record with three orders and four tags can become twelve rows. Expanding
items inside each order is different: the item counts sum across orders before
they multiply by the tags. Empty arrays can remove a whole source record or only
one branch. `explodeforecast` reports those effects at every declared stage.

Offline, standard-library Python. Source-only, no registry release or service.
This is an explicit counting model, not a Spark, pandas, Polars or SQL emulator.

## Try it

Python 3.10 or newer:

```sh
python -m pip install .
explodeforecast examples/orders.json examples/plan.json --max-rows 8 --examples 3
```

Or `python -m explodeforecast examples/orders.json examples/plan.json` from this
checkout. The example has two source records. Its stage counts are 3 orders,
3 items and 9 item/tag combinations. One source disappears at the item stage.
A budget of 8 fails even though the inputs are small.

## Model and plan

Input is one UTF-8 JSON array of objects. A UTF-8 BOM is accepted. Empty input,
non-object root records, repeated object keys and nonfinite JSON literals are
errors. JSON numbers are irrelevant to cardinality and never converted to floats.

```json
{
  "schema": "explodeforecast-plan-v1",
  "expansions": [
    {
      "name": "orders",
      "path": ["orders"],
      "absent": "drop",
      "empty": "drop",
      "children": [
        {"name": "items", "path": ["items"], "absent": "error", "empty": "drop", "children": []}
      ]
    },
    {"name": "tags", "path": ["tags"], "absent": "drop", "empty": "drop", "children": []}
  ]
}
```

- Each expansion selects an array. `path` is a list of literal object keys,
  relative to the root record or the selected parent element. No dot notation,
  wildcards, numeric array indexes, filters or expression evaluation. An empty
  path selects the context itself, useful for arrays nested directly in arrays.
- A root's siblings multiply independently. A node's children all use that same
  selected element as their context, and multiply within it. Contributions from
  the node's different elements sum. No cross-parent Cartesian product occurs.
- Stages execute in depth-first preorder, in the plan's declared order. Intermediate
  counts include the expansions encountered so far, not future expansions.
- Missing and null targets share the explicit `absent` policy: `drop` emits no
  branch, `keep` emits one placeholder, `error` refuses the entire audit. Empty
  arrays use their separate `empty` policy, `drop` or `keep`.
- A kept placeholder has no fields. Descendant paths therefore see an absent
  target and apply their own policy. A later child can still drop it.
- Any non-null, non-array target is an error. A path crossing a non-null scalar
  or array before its final key is an error. Array elements may be any JSON value;
  selecting fields from a scalar element is an error, not automatic coercion.
- Every reachable sibling branch is validated, even if a prior independent
  sibling produces zero rows. Descendants of a dropped/empty array have no
  selected element and are not evaluated. This can be stricter than executing
  a particular query engine's short-circuit behavior.
- Duplicate array elements count separately. Values, IDs and keys are not
  deduplicated, normalized or joined. No source-ID field is required.

## Output and gates

`explodeforecast-report-v1` includes exact Python-integer counts:

- Overall input records, final output rows, and sources with no final output.
- Each stage's input/output rows, surviving source count, newly lost source
  count and largest row contribution from one source. Losing several branches
  while another survives is not a lost source.
- A `--max-rows` budget applies to **every intermediate stage**, not only the
  final result. An exact boundary is allowed. Without a budget, `within_budget`
  is true, which is not a claim that the expansion is safe or useful.
- `--examples 0..100` optionally returns the first source indexes whose final
  row contribution differs from one, with their complete stage-count series. Zero-based indexes identify input positions,
  not a severity ranking. Default 0. No original payload values are returned.
- CLI byte input gets a SHA-256 receipt and byte length. This identifies supplied
  bytes, not their provenance or completeness. The Python object API has no byte
  receipt and no byte-cap enforcement, because the caller already supplied objects.

Exit 0: complete audit, within the optional budget. Exit 1: complete audit,
at least one stage exceeds the budget. Exit 2: invalid input/options, a work limit,
or I/O failure. Invalid input emits no report JSON. Output failure can leave a JSON
prefix; require a complete parse and successful delivery before using a report.

```python
from explodeforecast import audit, Limits
report = audit(records, plan, max_rows=100000,
               limits=Limits(max_records=1000), examples=0)
```

Reports contain plan stage names, counts, settings and input hashes. Optional
source indexes reveal which input positions change. This is not anonymization.
Keep source files and reports in an appropriate place. Error text avoids source
values and paths; the invoking shell command can still expose filenames.

## Compare two declared plans

A total can stay the same while different source records disappear or multiply.
Compare both plans on the **same complete input**, matched only by input position:

```sh
explodeforecast examples/orders.json examples/plan.json --compare-plan examples/by-tags.json --examples 3
```

```python
from explodeforecast import compare
report = compare(records, base_plan, candidate_plan, max_rows=100000, examples=3)
```

`explodeforecast-comparison-v1` contains both complete audit reports plus:

- Counts of source positions with increased, decreased and unchanged final row
  counts. `same_source_counts` means only that these counts match per position.
- Newly lost sources (positive to zero) and newly surviving sources (zero to
  positive). This compares final source counts, not each plan's intermediate losses.
- `added_rows` sums positive per-source count differences. `removed_rows` sums
  negative differences in magnitude. Their difference equals `delta_rows`.
  These are **count differences, not identified added/deleted output rows**.
- First up to `--examples` changed input positions with base/candidate final counts
  and signed delta. Default 0, maximum100; summaries always cover every source.
- `within_budget` requires **both** plans to satisfy the same optional row budget
  at every intermediate stage. Each nested report has its own budget verdict.

For example, base contributions `[2, 0, 1]` and candidate `[0, 2, 1]` both total3,
but comparison reports one increased, one decreased and one unchanged source,
one newly lost and one newly surviving, `added_rows=2`, `removed_rows=2`,
`delta_rows=0`, and `same_source_counts=false`.

Plan names, shapes and stage counts may differ. This does not compare expanded
values, output-row identity, order or schema, and **does not prove equivalent
transformations**, even when every source count matches. It does not align
separate datasets by ID. Both plans must validate completely; an invalid side
returns exit2 with no JSON report. Plan names appear in the nested reports.

`compare_bytes(data, base_plan, candidate_plan, **options)` adds the same exact
input-byte receipt as `audit_bytes`. The byte input is decoded once. Each plan
gets its own full structure/evaluation visit allowance, explicitly recorded as
`limits_scope="per_plan"`. Thus comparison can use twice a single audit's work
allowance, plus O(N) count comparison, and retains two O(N) final-count lists
for N input records. The usual record, byte, stage and depth limits still apply.
No expanded rows are built. Existing single-plan calls and exit codes are unchanged.

## Bounds and cost

Defaults: 8 MiB record bytes, 10,000 source records, 32 stages, input nesting depth
64 and 1,000,000 structure/evaluation visits. CLI plan bytes are capped at the
smaller of `--max-bytes` and 256 KiB. API limits accept only positive integers;
safety ceilings are 64 stages and depth128. Each path has at most32 keys, each
at most256 characters. Stage names are unique printable ASCII tokens of at most64
characters. Unknown object fields are rejected.

These are acceptance/work counters, **not hard RAM or runtime limits**. JSON is
fully read and decoded before the structure-depth check. Decoded objects, a
validation stack and temporary lists take memory. The API may receive already
large/shared/cyclic objects; cycles eventually hit the depth or visit limit.
The byte cap cannot prevent all parser allocation or recursion failures.

Counting traverses actual selected elements and never constructs their Cartesian
product. It still revisits dependencies for each stage. With S stages and V
reachable element/path visits for the complete plan, evaluation is at most
O(S V), plus input validation. Per-source caches hold references to selected
arrays, not payload copies. Integer arithmetic cost grows with the bit length of
counts. Work limits can refuse a large plan even when its mathematical count
would fit. No partial count is presented as a completed audit.

## Boundaries and existing tools

This is established sum/product counting, not a new algorithm. Use actual engine
execution or tests when you need its exact coercion, null, zip or outer behavior.
This model has no zip mode. It does not inspect query plans, estimate bytes,
choose an execution plan, predict runtime/RAM, infer paths, sample input, detect
missing source records, model filtering or prove downstream aggregation correctness.

- [JSONL Algebra](https://github.com/queelius/jsonl-algebra) executes explode and
  broader JSONL transformations.
- [aux4/json](https://hub.aux4.io/r/public/packages/aux4/json) describes structure
  and array cardinalities.
- [dkit](https://github.com/syang0531/dkit) converts and transforms multiple formats.
- [pandas explode](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.explode.html)
  and [Polars explode](https://docs.pola.rs/api/python/stable/reference/dataframe/api/polars.DataFrame.explode.html)
  have their own multi-column rules. Do not assume this plan means the same thing.
- [Spark arrays_zip](https://spark.apache.org/docs/latest/api/python/reference/pyspark.sql/api/pyspark.sql.functions.arrays_zip.html)
  combines corresponding array elements, a different operation from independent
  expansion.

The contribution here is a small explicit offline preflight format, stage-level
source-loss attribution, intermediate row budgets and bounded private evidence.
Searches did not establish that no similar tool exists.

## Development

```sh
python -m unittest discover -s tests -v
ruff check explodeforecast tests
ruff format --check explodeforecast tests
python -m build
```

Tests compare stage counts and source attribution with a separate row-materializing
oracle on small deterministic synthetic inputs. These are model checks, not
production pipeline validation. See VALIDATION.md for the runs actually performed.
