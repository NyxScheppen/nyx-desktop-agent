"""Audit policy references and reject new module-level policy literals.

Run from any directory with ``python -m scripts.check_policy``. This checks
declarative data and references, not algorithm constants or test fixtures.
"""

import ast
import re
from pathlib import Path
from typing import Any

from nyx import enums
from nyx.expression.mutter import MutterCategory
from nyx.policy import PolicyError, policy, validate_policy_catalog

ROOT = Path(__file__).resolve().parents[1]

# These define file formats, SQL projections or transport contracts rather than
# tunable behavior. New exceptions require a documented reason here.
STRUCTURAL_NAMES = frozenset(
    {
        "SECONDS_PER_DAY",
        "SECONDS_PER_HOUR",
        "CHUNKER_VERSION",
        "_TERMINAL_DELIVERY_STATES",
        "_PRESENCE_FIELDS",
        "_ASSET_KEYS",
        "_RESTORABLE_HISTORY_EVENTS",
        "_SOURCE_TOPIC_PREFIXES",
        "_SOURCE_CONTENT_CATEGORIES",
    }
)


def audit() -> list[str]:
    """Validate every statically declared policy lookup and JSON reference."""
    failures: list[str] = []
    namespace: dict[str, Any] = {
        "re": re,
        "Path": Path,
        "MutterCategory": MutterCategory,
        **{
            name: getattr(enums, name)
            for name in dir(enums)
            if not name.startswith("_")
        },
    }
    count = 0
    sources = [*(ROOT / "nyx").rglob("*.py"), ROOT / "dev.py"]
    for path in sources:
        if path.name == "policy.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if (
                not isinstance(node, ast.Call)
                or not isinstance(node.func, ast.Name)
                or node.func.id != "policy"
            ):
                continue
            try:
                system, key = (ast.literal_eval(arg) for arg in node.args[:2])
                expected = eval(
                    compile(ast.Expression(node.args[2]), str(path), "eval"), namespace
                )
                policy(system, key, expected)
                count += 1
            except (PolicyError, ValueError, TypeError, NameError, IndexError) as error:
                failures.append(f"{path.relative_to(ROOT)}:{node.lineno}: {error}")
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not all(isinstance(target, ast.Name) for target in targets):
                continue
            names = [target.id for target in targets if isinstance(target, ast.Name)]
            if not any(name.isupper() for name in names):
                continue
            if any(
                name in STRUCTURAL_NAMES
                or any(part in name for part in ("_COLS", "_SELECT", "MIGRATIONS"))
                for name in names
            ):
                continue
            try:
                literal = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
            if isinstance(literal, (str, int, float, list, tuple, set, dict)):
                failures.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}: "
                    f"{', '.join(names)} must read its owning system policy"
                )
    try:
        validate_policy_catalog()
    except PolicyError as error:
        failures.append(str(error))
    print(f"Policy audit: {count} Python references, {len(failures)} errors")
    return failures


def main() -> None:
    failures = audit()
    for failure in failures:
        print(failure)
    raise SystemExit(bool(failures))


if __name__ == "__main__":
    main()
