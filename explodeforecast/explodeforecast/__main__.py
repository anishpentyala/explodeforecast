"""Source-only command line interface."""

import argparse
import json
import sys

from .core import AuditError, Limits, audit_bytes, loads


def _read(path, cap):
    with open(path, "rb") as stream:
        value = stream.read(cap + 1)
    if len(value) > cap:
        raise AuditError("input byte limit exceeded")
    return value


def main(argv=None, *, stdout=None, stderr=None):
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    parser = argparse.ArgumentParser(
        description="Count declared JSON array expansions without building product rows."
    )
    parser.add_argument("records", help="UTF-8 JSON array of objects")
    parser.add_argument("plan", help="UTF-8 explicit expansion-tree plan")
    parser.add_argument(
        "--max-rows",
        type=int,
        help="budget applied to every stage, exact boundary allowed",
    )
    parser.add_argument(
        "--examples",
        type=int,
        default=0,
        help="up to100 changed source indexes, default0",
    )
    parser.add_argument("--max-bytes", type=int, default=8 * 1024 * 1024)
    parser.add_argument("--max-visits", type=int, default=1000000)
    args = parser.parse_args(argv)
    try:
        limits = Limits(max_bytes=args.max_bytes, max_visits=args.max_visits)
        plan = loads(_read(args.plan, min(limits.max_bytes, 262144)).decode("utf-8-sig"))
        result = audit_bytes(
            _read(args.records, limits.max_bytes),
            plan,
            limits=limits,
            max_rows=args.max_rows,
            examples=args.examples,
        )
        body = json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n"
        written = out.write(body)
        if written is not None and written != len(body):
            raise OSError("short output write")
        out.flush()
        return 0 if result["within_budget"] else 1
    except (AuditError, OSError, UnicodeError, RecursionError, ValueError):
        try:
            err.write("explodeforecast: incomplete audit, invalid input/options or I/O failure\n")
            err.flush()
        except OSError:
            pass
        return 2


def entrypoint():
    status = main()
    if status == 2:
        # Suppress a second interpreter-shutdown flush on a broken stdout.
        try:
            sys.stdout = open(__import__("os").devnull, "w")
        except OSError:
            pass
    raise SystemExit(status)


if __name__ == "__main__":
    entrypoint()
