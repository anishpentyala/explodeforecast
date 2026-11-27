import copy
import hashlib
import io
import json
import random
import tempfile
import unittest
from pathlib import Path

from explodeforecast import AuditError, Limits, audit, compare, compare_bytes
from explodeforecast.__main__ import main
from test_audit import oracle, plan, stage


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.base = plan(stage("a", ["a"]))
        self.candidate = plan(stage("b", ["b"]))

    def test_equal_totals_hide_source_reversal(self):
        data = [dict(a=[1, 2], b=[]), dict(a=[], b=[3, 4]), dict(a=[1], b=[9])]
        r = compare(data, self.base, self.candidate, examples=2)
        self.assertEqual(r["delta_rows"], 0)
        self.assertFalse(r["same_source_counts"])
        self.assertEqual(
            (r["sources_increased"], r["sources_decreased"], r["sources_unchanged"]), (1, 1, 1)
        )
        self.assertEqual((r["newly_lost_sources"], r["newly_surviving_sources"]), (1, 1))
        self.assertEqual((r["added_rows"], r["removed_rows"]), (2, 2))
        self.assertEqual(
            r["examples"],
            [
                dict(record_index=0, base_rows=2, candidate_rows=0, delta=-2),
                dict(record_index=1, base_rows=0, candidate_rows=2, delta=2),
            ],
        )

    def test_equal_counts_do_not_claim_equal_values(self):
        r = compare([dict(a=[1], b=["different"])], self.base, self.candidate)
        self.assertTrue(r["same_source_counts"])
        self.assertEqual(r["sources_unchanged"], 1)
        self.assertNotIn("equivalent", r)
        self.assertEqual(r["examples"], [])

    def test_nested_independent_plans_and_no_mutation(self):
        data = [dict(a=[dict(x=[1, 2]), dict(x=[3])], b=[4, 5])]
        candidate = plan(
            stage("parent", ["a"], children=[stage("child", ["x"])]), stage("tags", ["b"])
        )
        original = copy.deepcopy((data, self.base, candidate))
        r = compare(data, self.base, candidate)
        self.assertEqual((r["base"]["output_rows"], r["candidate"]["output_rows"]), (2, 6))
        self.assertEqual((data, self.base, candidate), original)

    def test_transient_budget_both_sides(self):
        data = [dict(a=[1, 2, 3], b=[])]
        candidate = plan(stage("a", ["a"]), stage("b", ["b"]))
        r = compare(data, self.base, candidate, max_rows=2)
        self.assertFalse(r["within_budget"])
        self.assertFalse(r["candidate"]["within_budget"])
        self.assertEqual(r["candidate"]["output_rows"], 0)
        self.assertTrue(compare(data, self.base, self.base, max_rows=3)["within_budget"])

    def test_examples_cap_default_and_source_order(self):
        data = [dict(a=[1], b=[1, 2]) for _ in range(120)]
        r = compare(data, self.base, self.candidate, examples=100)
        self.assertEqual(len(r["examples"]), 100)
        self.assertEqual(r["examples"][-1]["record_index"], 99)
        self.assertEqual(r["sources_increased"], 120)
        self.assertEqual(compare(data, self.base, self.candidate)["examples"], [])

    def test_errors_either_plan_and_limit(self):
        for b, c in [(self.base, {}), ({}, self.candidate)]:
            with self.assertRaises(AuditError):
                compare([dict(a=[1], b=[1])], b, c)
        for examples in [-1, 101, True, 1.5]:
            with self.assertRaises(AuditError):
                compare([dict(a=[1], b=[1])], self.base, self.candidate, examples=examples)
        with self.assertRaises(AuditError):
            compare([dict(a=[1], b=[1])], self.base, self.candidate, limits=Limits(max_visits=1))
        with self.assertRaises(AuditError):
            compare([dict(a=[], b=3)], self.base, self.candidate)

    def test_per_plan_limit_not_sum(self):
        data = [dict(a=[1, 2], b=[3])]
        n = audit(data, self.base)["visits"]
        r = compare(data, self.base, self.base, limits=Limits(max_visits=n))
        self.assertEqual(r["settings"]["limits_scope"], "per_plan")
        self.assertEqual(r["base"]["visits"], n)
        self.assertEqual(r["candidate"]["visits"], n)

    def test_large_count_exact(self):
        p = plan(*(stage(str(i), ["a"]) for i in range(32)))
        r = compare([dict(a=list(range(10)), b=[])], p, self.candidate)
        self.assertEqual(r["removed_rows"], 10**32)
        self.assertEqual(r["delta_rows"], -(10**32))

    def test_receipt_and_invalid_bytes(self):
        raw = b'\xef\xbb\xbf[{"a":[1e9999],"b":[]}]'
        r = compare_bytes(raw, self.base, self.candidate)
        self.assertEqual(r["input"], dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
        for value in [b"\xff", b'[{"a":[],"a":[]}]', b"[]", b'[{"a":[NaN]}]']:
            with self.assertRaises(AuditError):
                compare_bytes(value, self.base, self.candidate)
        with self.assertRaises(AuditError):
            compare_bytes(raw, self.base, self.candidate, limits=Limits(max_bytes=1))

    def test_materializing_random_oracle(self):
        rng = random.Random(2711)
        for _ in range(250):
            data = [
                dict(
                    a=[dict(x=list(range(rng.randrange(4)))) for _ in range(rng.randrange(4))],
                    b=list(range(rng.randrange(4))),
                )
                for _ in range(rng.randint(1, 5))
            ]
            b = plan(
                stage("a", ["a"], children=[stage("x", ["x"], empty=rng.choice(["keep", "drop"]))])
            )
            c = plan(stage("b", ["b"], empty=rng.choice(["keep", "drop"])))
            before = [oracle([row], b)[-1][0] for row in data]
            after = [oracle([row], c)[-1][0] for row in data]
            r = compare(data, b, c, examples=100)
            self.assertEqual(r["sources_increased"], sum(y > x for x, y in zip(before, after)))
            self.assertEqual(r["sources_decreased"], sum(y < x for x, y in zip(before, after)))
            self.assertEqual(r["sources_unchanged"], sum(y == x for x, y in zip(before, after)))
            self.assertEqual(r["added_rows"] - r["removed_rows"], r["delta_rows"])
            self.assertEqual(
                r["newly_lost_sources"], sum(x > 0 and y == 0 for x, y in zip(before, after))
            )
            self.assertEqual(
                r["newly_surviving_sources"], sum(x == 0 and y > 0 for x, y in zip(before, after))
            )
            self.assertEqual(
                [(x["record_index"], x["base_rows"], x["candidate_rows"]) for x in r["examples"]],
                [(i, x, y) for i, (x, y) in enumerate(zip(before, after)) if x != y],
            )

    def cli(self, data, candidate=None, flags=()):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name, value in [
                ("data", data),
                ("base", self.base),
                ("candidate", self.candidate if candidate is None else candidate),
            ]:
                (root / name).write_text(json.dumps(value))
            out, err = io.StringIO(), io.StringIO()
            code = main(
                [
                    str(root / "data"),
                    str(root / "base"),
                    "--compare-plan",
                    str(root / "candidate"),
                    *flags,
                ],
                stdout=out,
                stderr=err,
            )
            return code, out.getvalue(), err.getvalue()

    def test_cli_success_and_budget(self):
        code, body, err = self.cli(
            [dict(a=[1, 2], b=[])], flags=["--max-rows", "1", "--examples", "1"]
        )
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(body)["removed_rows"], 2)
        self.assertEqual(err, "")
        self.assertEqual(self.cli([dict(a=[1], b=[2])])[0], 0)

    def test_cli_candidate_error_no_partial_report(self):
        code, body, err = self.cli([dict(a=[1], b=[2])], candidate={})
        self.assertEqual(code, 2)
        self.assertEqual(body, "")
        self.assertIn("incomplete audit", err)
