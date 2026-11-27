import copy
import io
import itertools
import json
import random
import tempfile
import unittest
from pathlib import Path

from explodeforecast import AuditError, Limits, audit, audit_bytes, loads
from explodeforecast.__main__ import main


def stage(name, path, *, children=None, absent="drop", empty="drop"):
    return dict(name=name, path=path, absent=absent, empty=empty, children=children or [])


def plan(*rows):
    return dict(schema="explodeforecast-plan-v1", expansions=list(rows))


def oracle(records, specification):
    # Independent execution oracle. It really builds rows as dictionaries of
    # stage contexts, so it is only suitable for the small synthetic fixtures.
    steps = []

    def flatten(rows, parent):
        for row in rows:
            steps.append((row, parent))
            flatten(row["children"], row["name"])

    flatten(specification["expansions"], None)
    rows = [{"source": i, "root": record, "contexts": {}} for i, record in enumerate(records)]
    output = []
    for step, parent in steps:
        new = []
        for row in rows:
            value = row["root"] if parent is None else row["contexts"][parent]
            for key in step["path"]:
                value = value.get(key) if isinstance(value, dict) else None
            if value is None:
                values = [None] if step["absent"] == "keep" else []
            elif not value:
                values = [None] if step["empty"] == "keep" else []
            else:
                values = value
            for element in values:
                new.append(
                    dict(
                        source=row["source"],
                        root=row["root"],
                        contexts={**row["contexts"], step["name"]: element},
                    )
                )
        before = set(row["source"] for row in rows)
        after = set(row["source"] for row in new)
        counts = [sum(row["source"] == i for row in new) for i in range(len(records))]
        output.append((len(new), len(after), len(before - after), max(counts)))
        rows = new
    return output


class CoreTests(unittest.TestCase):
    def test_sibling_product(self):
        report = audit([dict(a=[1, 2], b=[3, 4, 5])], plan(stage("a", ["a"]), stage("b", ["b"])))
        self.assertEqual([x["output_rows"] for x in report["stages"]], [2, 6])

    def test_nested_sum_then_sibling_product(self):
        records = [dict(orders=[dict(items=[1, 2]), dict(items=[3])], tags=[1, 2, 3])]
        p = plan(
            stage("orders", ["orders"], children=[stage("items", ["items"])]),
            stage("tags", ["tags"]),
        )
        report = audit(records, p, examples=1)
        self.assertEqual(report["examples"][0]["rows_after_stage"], [2, 3, 9])
        self.assertEqual(report["output_rows"], 9)

    def test_parent_and_sibling_context(self):
        records = [dict(a=[dict(x=[1, 2], y=[0]), dict(x=[1], y=[0, 1, 2])])]
        p = plan(stage("a", ["a"], children=[stage("x", ["x"]), stage("y", ["y"])]))
        self.assertEqual(audit(records, p)["output_rows"], 5)

    def test_loss_is_source_not_occurrence(self):
        records = [dict(a=[dict(b=[]), dict(b=[1, 2])]), dict(a=[dict(b=[])])]
        p = plan(stage("a", ["a"], children=[stage("b", ["b"])]))
        report = audit(records, p)
        self.assertEqual(report["stages"][1]["newly_lost_sources"], 1)
        self.assertEqual(report["output_rows"], 2)
        self.assertEqual(report["lost_sources"], 1)

    def test_policies(self):
        for absent, empty in itertools.product(["keep", "drop"], repeat=2):
            with self.subTest(absent=absent, empty=empty):
                p = plan(stage("a", ["a"], absent=absent, empty=empty))
                r = audit([{}, {"a": None}, {"a": []}], p)
                self.assertEqual(r["output_rows"], 2 * (absent == "keep") + (empty == "keep"))

    def test_placeholder_children(self):
        p = plan(stage("a", ["a"], empty="keep", children=[stage("b", ["b"], absent="keep")]))
        self.assertEqual(audit([dict(a=[])], p)["output_rows"], 1)
        p["expansions"][0]["children"][0]["absent"] = "drop"
        self.assertEqual(audit([dict(a=[])], p)["output_rows"], 0)

    def test_empty_key_and_array_elements(self):
        p = plan(stage("a", [""], children=[stage("b", [])]))
        self.assertEqual(audit([{"": [[1, 2], [3]]}], p)["output_rows"], 3)

    def test_exact_budget_and_intermediate_peak(self):
        p = plan(stage("a", ["a"], children=[stage("b", ["b"])]))
        records = [dict(a=[dict(b=[]), dict(b=[])])]
        self.assertTrue(audit(records, p, max_rows=2)["within_budget"])
        self.assertFalse(audit(records, p, max_rows=2)["stages"][0]["exceeds_max_rows"])
        self.assertFalse(audit(records, p, max_rows=1)["within_budget"])
        self.assertEqual(audit(records, p)["output_rows"], 0)

    def test_large_product_no_materialization(self):
        p = plan(*(stage(str(i), ["a"]) for i in range(32)))
        r = audit([dict(a=list(range(10)))], p)
        self.assertEqual(r["output_rows"], 10**32)
        self.assertLess(r["visits"], 10000)

    def test_no_mutation(self):
        data = [dict(a=[dict(b=[1, 2])])]
        p = plan(stage("a", ["a"], children=[stage("b", ["b"])]))
        original = copy.deepcopy((data, p))
        audit(data, p)
        self.assertEqual((data, p), original)

    def test_receipt_and_numeric_literals(self):
        raw = b'[{"a":[1e9999,123456789012345678901234567890]}]'
        r = audit_bytes(raw, plan(stage("a", ["a"])))
        self.assertEqual(r["output_rows"], 2)
        self.assertEqual(r["input"]["bytes"], len(raw))
        self.assertEqual(str(loads("1e9999")), "1e9999")
        self.assertEqual(
            str(loads("1e9999999999999999999999999999")), "1e9999999999999999999999999999"
        )

    def test_complete_validation_even_zero_sibling(self):
        with self.assertRaises(AuditError):
            audit([dict(a=[], b="wrong")], plan(stage("a", ["a"]), stage("b", ["b"])))

    def test_random_small_oracle(self):
        rng = random.Random(51293)
        for iteration in range(500):
            records = []
            for _ in range(rng.randint(1, 5)):
                records.append(
                    dict(
                        a=[
                            dict(
                                b=list(range(rng.randrange(4))),
                                c=list(range(rng.randrange(3))),
                            )
                            for _ in range(rng.randrange(4))
                        ],
                        d=list(range(rng.randrange(4))),
                    )
                )
            p = plan(
                stage(
                    "a",
                    ["a"],
                    children=[
                        stage("b", ["b"], empty=rng.choice(["drop", "keep"])),
                        stage("c", ["c"], empty=rng.choice(["drop", "keep"])),
                    ],
                    empty=rng.choice(["drop", "keep"]),
                ),
                stage("d", ["d"], empty=rng.choice(["drop", "keep"])),
            )
            expected = oracle(records, p)
            result = audit(records, p)
            actual = [
                (
                    s["output_rows"],
                    s["surviving_sources"],
                    s["newly_lost_sources"],
                    s["max_rows_from_one_source"],
                )
                for s in result["stages"]
            ]
            self.assertEqual(actual, expected, iteration)


class RejectionTests(unittest.TestCase):
    def test_bad_json(self):
        for value in ['{"a":1,"a":2}', "NaN", "Infinity", "[", "[" * 2000]:
            with self.subTest(value=value[:30]), self.assertRaises(AuditError):
                loads(value)

    def test_bad_records(self):
        for data in [
            None,
            {},
            [],
            [1],
            [{"a": float("nan")}],
            [{"a": object()}],
            [{1: []}],
        ]:
            with self.subTest(data=str(data)), self.assertRaises(AuditError):
                audit(data, plan(stage("a", ["a"])))

    def test_bad_options(self):
        for opts in [
            dict(max_rows=True),
            dict(max_rows=-1),
            dict(examples=True),
            dict(examples=101),
            dict(limits={}),
        ]:
            with self.subTest(opts=opts), self.assertRaises(AuditError):
                audit([dict(a=[])], plan(stage("a", ["a"])), **opts)

    def test_bad_plan(self):
        good = plan(stage("a", ["a"]))
        bad = [
            {},
            plan(),
            plan(stage("a", [1])),
            plan(stage("a", ["a"], absent="x")),
            plan(stage("a", ["a"], empty="x")),
            plan(stage("a", ["a"]), stage("a", ["b"])),
            plan(stage("has space", [])),
            plan(stage("a", [], children={})),
            dict(schema=[], expansions=[]),
        ]
        for p in bad:
            with self.subTest(plan=p), self.assertRaises(AuditError):
                audit([dict(a=[])], p)
        self.assertEqual(audit([dict(a=[])], good)["output_rows"], 0)

    def test_limits(self):
        for kw in [
            dict(max_bytes=0),
            dict(max_depth=129),
            dict(max_stages=65),
            dict(max_visits=True),
        ]:
            with self.subTest(kw=kw), self.assertRaises(AuditError):
                Limits(**kw)
        for limits in [
            Limits(max_records=1),
            Limits(max_visits=2),
            Limits(max_depth=1),
        ]:
            with self.assertRaises(AuditError):
                audit([dict(a=[1]), dict(a=[2])], plan(stage("a", ["a"])), limits=limits)
        with self.assertRaises(AuditError):
            audit_bytes(b'[{"a":[]}]', plan(stage("a", ["a"])), limits=Limits(max_bytes=1))

    def test_bad_targets(self):
        for data, path in [
            ([dict(a=1)], ["a"]),
            ([dict(a=[])], ["a", "b"]),
            ([dict(a={})], ["a"]),
        ]:
            with self.subTest(data=data), self.assertRaises(AuditError):
                audit(data, plan(stage("a", path)))
        with self.assertRaises(AuditError):
            audit([{}], plan(stage("a", ["a"], absent="error")))

    def test_stage_work_and_depth_caps(self):
        with self.assertRaises(AuditError):
            audit([dict(a=[1, 2])], plan(stage("a", ["a"])), limits=Limits(max_visits=7))
        with self.assertRaises(AuditError):
            audit(
                [dict(a=[])],
                plan(stage("a", ["a"]), stage("b", ["a"])),
                limits=Limits(max_stages=1),
            )
        p = plan(stage("a", ["a"], children=[stage("b", [], children=[stage("c", [])])]))
        with self.assertRaises(AuditError):
            audit([dict(a=[])], p, limits=Limits(max_depth=2))

    def test_numeric_literals_are_not_keys_or_names(self):
        for raw in ['[{"a": 1e999999999999999999999999999999}]', '[{"a": 123}]']:
            with self.assertRaises(AuditError):
                audit_bytes(raw.encode(), plan(stage("a", ["a", "x"])))
        p = loads(
            '{"schema":"explodeforecast-plan-v1","expansions":[{"name":1,"path":[],"absent":"drop","empty":"drop","children":[]}]}'
        )
        with self.assertRaises(AuditError):
            audit([{}], p)

    def test_cycles_bounded(self):
        data = {}
        data["self"] = data
        with self.assertRaises(AuditError):
            audit([data], plan(stage("a", [])))

    def test_utf8_and_bom(self):
        p = plan(stage("a", ["a"]))
        with self.assertRaises(AuditError):
            audit_bytes(b"\xff", p)
        self.assertEqual(audit_bytes(b'\xef\xbb\xbf[{"a":[1]}]', p)["output_rows"], 1)


class CliTests(unittest.TestCase):
    def run_cli(self, data, p, *args, out=None):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "data.json").write_text(json.dumps(data))
            (root / "plan.json").write_text(json.dumps(p))
            stdout = io.StringIO() if out is None else out
            stderr = io.StringIO()
            code = main(
                [str(root / "data.json"), str(root / "plan.json"), *args],
                stdout=stdout,
                stderr=stderr,
            )
            return code, stdout, stderr

    def test_exits(self):
        p = plan(stage("a", ["a"]))
        self.assertEqual(self.run_cli([dict(a=[1, 2])], p)[0], 0)
        self.assertEqual(self.run_cli([dict(a=[1, 2])], p, "--max-rows", "1")[0], 1)
        code, out, err = self.run_cli([dict(a=1)], p)
        self.assertEqual(code, 2)
        self.assertEqual(out.getvalue(), "")
        self.assertTrue(err.getvalue())

    def test_output_failures(self):
        class Broken(io.StringIO):
            def write(self, value):
                raise BrokenPipeError()

        class Short(io.StringIO):
            def write(self, value):
                return len(value) - 1

        class Flush(io.StringIO):
            def flush(self):
                raise OSError()

        for output in [Broken(), Short(), Flush()]:
            with self.subTest(output=type(output)):
                self.assertEqual(
                    self.run_cli([dict(a=[1])], plan(stage("a", ["a"])), out=output)[0],
                    2,
                )

    def test_read_error(self):
        out = io.StringIO()
        self.assertEqual(
            main(["/no/such/data", "/no/such/plan"], stdout=out, stderr=io.StringIO()),
            2,
        )
        self.assertEqual(out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
