"""Checking a rule pack without running the application.

Biology needs its own feedback loop. Without one, every iteration of a rule
queues behind an engineer, and the rules stop getting written.

    uv run python -m app.domain.rules check
    uv run python -m app.domain.rules schema rules/rule.schema.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.domain.rules.engine import evaluate
from app.domain.rules.loader import DEFAULT_RULES_DIR, example_state, load_rules
from app.domain.rules.models import Rule


def check_examples(rule: Rule) -> list[str]:
    """Run a rule against its own examples. Returns the failures."""
    failures = []
    for example in rule.examples:
        findings = evaluate(rule, example_state(example))
        fired = bool(findings)
        if fired != example.triggers:
            expected = "to trigger" if example.triggers else "not to trigger"
            got = findings[0].message if findings else "nothing reported"
            failures.append(f"{example.name!r}: expected {expected}, got {got}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.domain.rules", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="validate rules and run their examples")
    check.add_argument("directory", nargs="?", type=Path, default=DEFAULT_RULES_DIR)

    schema = sub.add_parser("schema", help="write the JSON Schema for editors")
    schema.add_argument(
        "output", nargs="?", type=Path, default=DEFAULT_RULES_DIR / "rule.schema.json"
    )

    args = parser.parse_args(argv)

    if args.command == "schema":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(Rule.model_json_schema(), indent=2) + "\n")
        print(f"wrote {args.output}")
        print(
            "Put this at the top of every rule file for editor autocompletion:\n"
            f"  # yaml-language-server: $schema=./{args.output.name}"
        )
        return 0

    ruleset = load_rules(args.directory)
    for problem in ruleset.errors:
        print(f"  REJECTED  {problem}")

    failed = 0
    for rule in ruleset.rules:
        failures = check_examples(rule)
        if failures:
            failed += 1
            print(f"  FAILED    {rule.id}")
            for failure in failures:
                print(f"              {failure}")
        else:
            examples = len(rule.examples)
            print(
                f"  ok        {rule.id:26s} {rule.severity:8s}"
                f" {examples} example{'' if examples == 1 else 's'}"
            )

    total = len(ruleset.rules)
    print(
        f"\n{total} rule{'' if total == 1 else 's'} loaded from {args.directory}, "
        f"{failed} failing, {len(ruleset.errors)} rejected"
    )
    return 1 if failed or ruleset.errors else 0
