"""Count a declared tree of JSON array expansions without building product rows."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass


class AuditError(ValueError):
    """The complete audit could not be produced."""


@dataclass(frozen=True)
class Limits:
    max_bytes: int = 8 * 1024 * 1024
    max_records: int = 10000
    max_stages: int = 32
    max_visits: int = 1000000
    max_depth: int = 64

    def __post_init__(self):
        for value in asdict(self).values():
            if type(value) is not int or value < 1:
                raise AuditError("limits must be positive integers")
        if self.max_depth > 128 or self.max_stages > 64:
            raise AuditError("depth/stage safety ceiling exceeded")


_MISSING = object()


class _Number(str):
    """Opaque valid JSON numeric literal, never an object context."""


def loads(text):
    """Parse JSON with no duplicate keys or nonfinite numeric literals."""

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise AuditError("duplicate JSON object key")
            result[key] = value
        return result

    def number(value):
        # Counts depend on structure, never numeric magnitude. Preserve literals
        # exactly and avoid float infinity for otherwise legal large exponents.
        return _Number(value)

    def constant(value):
        raise AuditError("nonfinite JSON literal")

    try:
        return json.loads(
            text,
            object_pairs_hook=pairs,
            parse_int=number,
            parse_float=number,
            parse_constant=constant,
        )
    except (ValueError, RecursionError, UnicodeError) as exc:
        raise AuditError("invalid JSON") from exc


def _shape(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        raise AuditError("unexpected object fields")


def _compile(plan, limits):
    _shape(plan, ("schema", "expansions"))
    if plan["schema"] != "explodeforecast-plan-v1":
        raise AuditError("unsupported plan schema")
    flat = []
    seen = set()

    def walk(rows, parent, depth):
        if type(rows) is not list or (parent is None and not rows):
            raise AuditError("expansions must be a list, nonempty at the root")
        if depth > limits.max_depth:
            raise AuditError("plan depth limit exceeded")
        for row in rows:
            _shape(row, ("name", "path", "absent", "empty", "children"))
            name, path = row["name"], row["path"]
            if (
                type(name) is not str
                or not 1 <= len(name) <= 64
                or any(ord(c) < 33 or ord(c) > 126 for c in name)
                or name in seen
            ):
                raise AuditError("stage names must be unique printable ASCII tokens")
            if (
                type(path) is not list
                or len(path) > 32
                or any(type(x) is not str or len(x) > 256 for x in path)
            ):
                raise AuditError("path must be a bounded list of object keys")
            if type(row["absent"]) is not str or row["absent"] not in (
                "drop",
                "keep",
                "error",
            ):
                raise AuditError("unsupported absent policy")
            if type(row["empty"]) is not str or row["empty"] not in ("drop", "keep"):
                raise AuditError("unsupported empty policy")
            seen.add(name)
            index = len(flat)
            flat.append(
                {
                    "name": name,
                    "path": path,
                    "absent": row["absent"],
                    "empty": row["empty"],
                    "parent": parent,
                    "children": [],
                }
            )
            if len(flat) > limits.max_stages:
                raise AuditError("stage limit exceeded")
            if parent is not None:
                flat[parent]["children"].append(index)
            walk(row["children"], index, depth + 1)

    walk(plan["expansions"], None, 1)
    return flat


def _validate_data(records, limits):
    if type(records) is not list or not 1 <= len(records) <= limits.max_records:
        raise AuditError("records must be a nonempty bounded list")
    if any(type(x) is not dict for x in records):
        raise AuditError("every record must be an object")
    # Inspect iterative structure, including otherwise unused fields. Shared
    # subobjects are allowed; cycles are rejected by the depth ceiling.
    stack = [(records, 0)]
    visited = 0
    while stack:
        value, depth = stack.pop()
        visited += 1
        if visited > limits.max_visits:
            raise AuditError("input structure visit limit exceeded")
        if depth > limits.max_depth:
            raise AuditError("input depth limit exceeded")
        if type(value) is dict:
            if any(type(k) is not str for k in value):
                raise AuditError("JSON object keys must be strings")
            stack.extend((x, depth + 1) for x in value.values())
        elif type(value) is list:
            stack.extend((x, depth + 1) for x in value)
        elif value is not None and type(value) not in (str, int, float, bool, _Number):
            raise AuditError("unsupported JSON value")
        elif type(value) is float and (value != value or abs(value) == float("inf")):
            raise AuditError("nonfinite value")
    return visited


def audit(records, plan, *, limits=None, max_rows=None, examples=0):
    """Audit complete supplied records under explicit dependent-product semantics.

    Trees are visited preorder. Siblings multiply. Children expand each element
    of their parent independently; their contributions sum. No expanded rows or
    payload copies are built. This is not a dataframe/SQL execution emulator.
    """
    limits = Limits() if limits is None else limits
    if type(limits) is not Limits:
        raise AuditError("limits must be Limits")
    if max_rows is not None and (type(max_rows) is not int or max_rows < 0):
        raise AuditError("max_rows must be a nonnegative integer")
    if type(examples) is not int or not 0 <= examples <= 100:
        raise AuditError("examples must be from 0 through 100")
    flat = _compile(plan, limits)
    visits = _validate_data(records, limits)
    roots = [i for i, row in enumerate(flat) if row["parent"] is None]
    totals = [0] * len(flat)
    surviving = [0] * len(flat)
    newly_lost = [0] * len(flat)
    largest = [0] * len(flat)
    witnesses = []

    def tick(amount=1):
        nonlocal visits
        visits += amount
        if visits > limits.max_visits:
            raise AuditError("evaluation visit limit exceeded")

    for record_index, record in enumerate(records):
        cache = {}

        def elements(index, context):
            key = (index, id(context))
            if key in cache:
                return cache[key]
            node = flat[index]
            value = context
            for part in node["path"]:
                tick()
                if value is _MISSING or value is None:
                    value = _MISSING
                    break
                if type(value) is not dict:
                    raise AuditError("object path crosses a non-object")
                value = value.get(part, _MISSING)
            if value is _MISSING or value is None:
                if node["absent"] == "error":
                    raise AuditError("missing or null expansion target")
                result = (_MISSING,) if node["absent"] == "keep" else ()
            elif type(value) is not list:
                raise AuditError("expansion target is not an array")
            elif not value:
                result = (_MISSING,) if node["empty"] == "keep" else ()
            else:
                result = value
            cache[key] = result
            return result

        def count(index, context, stop):
            tick()
            total = 0
            for element in elements(index, context):
                tick()
                contribution = 1
                # Deliberately validate every reachable sibling even if another
                # sibling produces zero; malformed branches cannot hide behind it.
                for child in flat[index]["children"]:
                    if child <= stop:
                        contribution *= count(child, element, stop)
                total += contribution
            return total

        previous = 1
        counts = []
        for stop in range(len(flat)):
            current = 1
            for root in roots:
                if root <= stop:
                    current *= count(root, record, stop)
            totals[stop] += current
            surviving[stop] += current > 0
            newly_lost[stop] += previous > 0 and current == 0
            largest[stop] = max(largest[stop], current)
            counts.append(current)
            previous = current
        if len(witnesses) < examples and counts[-1] != 1:
            witnesses.append({"record_index": record_index, "rows_after_stage": counts})

    stages = []
    previous_total = len(records)
    for i, node in enumerate(flat):
        stages.append(
            {
                "name": node["name"],
                "input_rows": previous_total,
                "output_rows": totals[i],
                "surviving_sources": surviving[i],
                "newly_lost_sources": newly_lost[i],
                "max_rows_from_one_source": largest[i],
                "exceeds_max_rows": max_rows is not None and totals[i] > max_rows,
            }
        )
        previous_total = totals[i]
    return {
        "schema": "explodeforecast-report-v1",
        "records": len(records),
        "output_rows": totals[-1],
        "lost_sources": len(records) - surviving[-1],
        "within_budget": max_rows is None or all(x <= max_rows for x in totals),
        "stages": stages,
        "examples": witnesses,
        "settings": {
            "max_rows": max_rows,
            "examples": examples,
            "limits": asdict(limits),
        },
        "visits": visits,
    }


def audit_bytes(data, plan, **options):
    """Audit UTF-8 JSON bytes and include an exact input-byte receipt."""
    limits = options.get("limits") or Limits()
    if type(limits) is not Limits or type(data) is not bytes:
        raise AuditError("expected bytes and Limits")
    if len(data) > limits.max_bytes:
        raise AuditError("input byte limit exceeded")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError as exc:
        raise AuditError("input is not UTF-8") from exc
    result = audit(loads(text), plan, **options)
    result["input"] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    return result
