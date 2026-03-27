#!/usr/bin/env python3
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
TESTS_DIR = ROOT / "tests"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from replay_scenarios import SCENARIOS, evaluate_scenario  # noqa: E402


def main():
    failures = 0
    print("Dialogue Replay Report")
    print("======================")
    for scenario in SCENARIOS:
        result = evaluate_scenario(scenario)
        plan = result["plan"]
        status = "PASS" if not result["failures"] else "FAIL"
        print(f"\n[{status}] {scenario.name}")
        print(f"  Description: {scenario.description}")
        print(f"  Intent: {result['intent']}")
        print(f"  Action: {plan.get('action')}")
        print(f"  Reason: {plan.get('reason')}")
        if plan.get("message"):
            print(f"  Message: {plan.get('message')}")
        else:
            print("  Message: <wait>")
        if result["failures"]:
            failures += 1
            print("  Checks:")
            for failure in result["failures"]:
                print(f"  - {failure}")
    print("\nSummary")
    print(f"  Scenarios: {len(SCENARIOS)}")
    print(f"  Failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
