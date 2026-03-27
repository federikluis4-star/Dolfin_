import unittest
from pathlib import Path
import sys


TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from replay_scenarios import SCENARIOS, evaluate_scenario


class TranscriptReplayTests(unittest.TestCase):
    def test_curated_replay_scenarios(self):
        failures = []
        for scenario in SCENARIOS:
            result = evaluate_scenario(scenario)
            if result["failures"]:
                failures.append(
                    f"{scenario.name}: " + ", ".join(result["failures"]) + f" | message={result['plan'].get('message')!r}"
                )
        if failures:
            self.fail("\n".join(failures))


if __name__ == "__main__":
    unittest.main()
