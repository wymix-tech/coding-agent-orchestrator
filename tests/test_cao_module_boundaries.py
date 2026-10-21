"""Protect source, Python API, import-order, and trusted-launcher contracts."""
import ast
import importlib
import inspect
from pathlib import Path
import subprocess
import sys
import unittest

from scripts import cao, cao_defaults, cao_runtime, cao_tool_policy

ROOT = Path(__file__).resolve().parents[1]


class ModuleBoundaryTests(unittest.TestCase):
    def test_facade_reexports_original_function_objects(self):
        functions = dict(inspect.getmembers(cao, inspect.isfunction))
        self.assertIn("main", functions)
        self.assertIn("load_work", functions)
        self.assertIn("completion_status", functions)
        for name, function in functions.items():
            with self.subTest(name=name):
                self.assertNotEqual(function.__module__, "scripts.cao")
                owner = importlib.import_module(function.__module__)
                self.assertIs(function, getattr(owner, function.__name__))
        self.assertIs(cao.SDD_CATALOG, cao_defaults.SDD_CATALOG)
        self.assertIs(cao.DEFAULT_CONFIG, cao_defaults.DEFAULT_CONFIG)
        self.assertIs(cao.verification_commands, cao_runtime.verification_scripts)

    def test_implementations_do_not_depend_on_compatibility_facade(self):
        facade = ast.parse((ROOT / "scripts/cao.py").read_text())
        self.assertFalse(any(isinstance(node, (ast.FunctionDef, ast.ClassDef)) for node in ast.walk(facade)))
        for path in (ROOT / "scripts").glob("*.py"):
            if path.name == "cao.py":
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, "scripts.cao", path.name)
                    if node.module == "scripts":
                        self.assertNotIn("cao", [alias.name for alias in node.names], path.name)
                elif isinstance(node, ast.Import):
                    self.assertNotIn("scripts.cao", [alias.name for alias in node.names], path.name)

    def test_each_responsibility_module_imports_first_in_a_fresh_process(self):
        for path in sorted((ROOT / "scripts").glob("cao_*.py")):
            with self.subTest(module=path.stem):
                result = subprocess.run(
                    [sys.executable, "-c", f"import scripts.{path.stem}"],
                    cwd=ROOT, text=True, capture_output=True, timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_only_entry_script_gets_control_command_exemption(self):
        self.assertTrue(cao_tool_policy.readonly_shell(f'{sys.executable} "{ROOT / "scripts/cao.py"}" status'))
        self.assertFalse(cao_tool_policy.readonly_shell(f'{sys.executable} "{ROOT / "scripts/cao_tool_policy.py"}" status'))


if __name__ == "__main__":
    unittest.main()
