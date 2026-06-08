"""Print an Istanbul-style coverage summary from the current .coverage data."""

import ast
import json
import os
import subprocess  # nosec B404
import sys
import tempfile
from pathlib import Path


def _count_functions(source_root: Path) -> tuple[int, int]:
    """Return (total_functions, covered_functions) using AST + line coverage data."""
    try:
        with open(os.path.join(tempfile.gettempdir(), "_cov_summary.json")) as f:
            data = json.load(f)
    except FileNotFoundError:
        return 0, 0

    files = data.get("files", {})
    total = 0
    covered = 0

    for rel_path, file_data in files.items():
        abs_path = source_root / rel_path
        if not abs_path.exists():
            abs_path = Path(rel_path)
        if not abs_path.exists():
            continue
        try:
            tree = ast.parse(abs_path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue

        executed = set(file_data.get("executed_lines", []))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                total += 1
                if node.lineno in executed:
                    covered += 1

    return total, covered


def main() -> None:
    tmp = os.path.join(tempfile.gettempdir(), "_cov_summary.json")
    result = subprocess.run(  # nosec B603
        [sys.executable, "-m", "coverage", "json", "-o", tmp, "-q"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(result.stderr.strip(), file=sys.stderr)
        sys.exit(result.returncode)

    with open(tmp) as f:
        data = json.load(f)

    totals = data["totals"]
    stmts = totals.get("num_statements", 0)
    missing = totals.get("missing_lines", 0)
    branches = totals.get("num_branches", 0)
    covered_branches = totals.get("covered_branches", 0)
    covered_stmts = stmts - missing

    source_root = Path.cwd()
    total_fns, covered_fns = _count_functions(source_root)

    width = 80
    bar = "=" * width

    def pct(a: int, b: int) -> str:
        return f"{a / b * 100:.2f}%" if b else "N/A"

    def row(label: str, covered: int, total: int) -> str:
        p = pct(covered, total)
        return f"{label:<14} : {p:>8} ( {covered}/{total} )"

    print(bar)
    print("Coverage summary".center(width))
    print(bar)
    print(row("Statements", covered_stmts, stmts))
    print(row("Branches", covered_branches, branches))
    if total_fns:
        print(row("Functions", covered_fns, total_fns))
    print(row("Lines", covered_stmts, stmts))
    print(bar)


if __name__ == "__main__":
    main()
