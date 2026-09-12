import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "context_footprint_check", ROOT / "scripts" / "context_footprint_check.py"
)
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MOD)


class ContextFootprintTests(unittest.TestCase):
    def test_skill_stays_within_context_budget(self):
        report, errors = MOD.validate(ROOT, 110, 7500, 1000)
        self.assertEqual(errors, [])
        self.assertLessEqual(report["skill"]["lines"], 110)
        self.assertLessEqual(report["skill"]["bytes"], 7500)
        self.assertLessEqual(report["skill"]["words"], 1000)

    def test_skill_declares_lazy_reference_loading(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Never preload all references", text)
        self.assertIn("Do not recursively follow every link", text)

    def test_skill_remains_version_neutral(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("version:", text.lower())
        self.assertIsNone(re.search(r"\bV(?:[1-9]\d*)(?:\.\d+)*\b", text))

    def test_reference_corpus_is_lazy_not_inlined(self):
        report = MOD.metrics(ROOT)
        self.assertGreater(report["references"]["bytes"], report["skill"]["bytes"] * 5)


if __name__ == "__main__":
    unittest.main()
