import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.fft_structural_coverage import metrics, parse_lcov, source_manifest


class FftStructuralCoverageTests(unittest.TestCase):
    def test_lcov_parser_filters_non_rtl_files_and_counts_lines_and_branches(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_root = root / "rtl"
            source_root.mkdir()
            rtl = source_root / "rtl.vhd"
            tb = root / "tb.vhd"
            rtl.write_text("", encoding="utf-8")
            tb.write_text("", encoding="utf-8")
            lcov = "\n".join(
                [
                    "SF:%s" % rtl,
                    "DA:10,2",
                    "DA:11,0",
                    "BRDA:10,0,0,1",
                    "BRDA:10,0,1,-",
                    "end_of_record",
                    "SF:%s" % tb,
                    "DA:1,99",
                    "end_of_record",
                ]
            )
            points = parse_lcov(lcov, source_root)
            result = metrics(points)
        self.assertEqual(result["line"], {"covered": 1, "total": 2, "pct": 50.0})
        self.assertEqual(result["branch"], {"covered": 1, "total": 2, "pct": 50.0})
        self.assertIsNone(result["toggle"]["pct"])

    def test_manifest_records_hash_and_line_count(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.vhd"
            path.write_text("a\nb\n", encoding="utf-8")
            manifest = source_manifest([path])
        self.assertEqual(manifest["files"], 1)
        self.assertEqual(manifest["physical_lines"], 2)
        self.assertEqual(len(manifest["entries"][0]["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
