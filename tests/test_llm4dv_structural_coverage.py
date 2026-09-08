from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.llm4dv_structural_coverage import StrideReference, metrics, parse_coverage


class StructuralCoverageTests(unittest.TestCase):
    def test_reference_reaches_both_states_and_stride_valid(self):
        model = StrideReference()
        outputs = [model.step(1, 0) for _ in range(4)]
        self.assertEqual({row[-1] for row in outputs}, {0, 1})
        self.assertEqual(outputs[-1][1], 1)

    def test_reference_holds_state_when_invalid(self):
        model = StrideReference()
        before = model.step(0, 123)
        after = model.step(0, 456)
        self.assertEqual(before, after)

    def test_native_coverage_parser_preserves_types(self):
        content = (
            "# SystemC::Coverage-3\n"
            "C '\x01t\x02line\x01f\x02stride_detector.sv\x01l\x0242' 1\n"
            "C '\x01t\x02branch\x01f\x02stride_detector.sv\x01l\x0243' 0\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "coverage.dat"
            path.write_text(content, encoding="utf-8")
            result = metrics(parse_coverage(path))
        self.assertEqual(result["line"], {"covered": 1, "total": 1, "pct": 100.0})
        self.assertEqual(result["branch"], {"covered": 0, "total": 1, "pct": 0.0})


if __name__ == "__main__":
    unittest.main()
