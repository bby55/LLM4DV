import json
import tempfile
import unittest
from pathlib import Path

from tools.ibex_full_rtl_coverage import (
    FSM_STATES,
    FSM_TRANSITIONS,
    choose_scenario,
    fsm_metrics,
    validate_fsm_trace,
)


class IbexFsmCoverageTests(unittest.TestCase):
    def test_denominators_match_rtl_enumeration(self):
        self.assertEqual(28, sum(len(states) for states in FSM_STATES.values()))
        self.assertEqual(41, sum(len(arcs) for arcs in FSM_TRANSITIONS.values()))

    def test_valid_trace_collects_states_and_transitions(self):
        rows = [
            {"cycle": 4, "controller": 0, "id_ex": 0, "load_store": 0,
             "multdiv": 0, "multiplier": 0},
            {"cycle": 5, "controller": 1, "id_ex": 1, "load_store": 0,
             "multdiv": 0, "multiplier": 0},
            {"cycle": 6, "controller": 4, "id_ex": 0, "load_store": 0,
             "multdiv": 0, "multiplier": 0},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fsm.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            result, observation = validate_fsm_trace(path)
        self.assertTrue(result["pass"])
        self.assertEqual({0, 1, 4}, observation["states"]["controller"])
        self.assertEqual({(0, 1), (1, 4)}, observation["transitions"]["controller"])
        self.assertEqual(
            3, fsm_metrics(observation)["machines"]["controller"]["states"]["covered"])

    def test_invalid_state_is_rejected(self):
        row = {"cycle": 4, "controller": 15, "id_ex": 0, "load_store": 0,
               "multdiv": 0, "multiplier": 0}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fsm.jsonl"
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            result, _ = validate_fsm_trace(path)
        self.assertFalse(result["pass"])

    def test_targeted_scenario_prioritizes_missed_controller_arcs(self):
        observation = {
            "machines": {
                name: {"states": {"missed": []}, "transitions": {"missed": []}}
                for name in FSM_STATES
            }
        }
        observation["machines"]["controller"]["transitions"]["missed"] = [
            "FIRST_FETCH->IRQ_TAKEN", "FLUSH->DBG_TAKEN_IF"
        ]
        self.assertEqual("irq_first_fetch", choose_scenario(1, observation))
        observation["machines"]["controller"]["transitions"]["missed"] = [
            "FLUSH->DBG_TAKEN_IF"
        ]
        self.assertEqual("debug_flush", choose_scenario(2, observation))
        self.assertEqual("debug", choose_scenario(4, observation))


if __name__ == "__main__":
    unittest.main()
