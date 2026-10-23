"""Registered project launchers remain usable from another CAO installation."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.cao_tool_policy import _readonly_tokens, tool_is_mutating


class RegisteredLaunchers(unittest.TestCase):
    def test_registered_launcher_is_accepted_but_basename_is_not(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            package = Path(__file__).resolve().parents[1]
            settings = repo / '.pi/settings.json'
            settings.parent.mkdir()
            settings.write_text(json.dumps({'packages': [str(package)]}))
            with patch('scripts.cao_tool_policy.package_root_dir', return_value=repo / 'other-copy'):
                self.assertTrue(_readonly_tokens([str(package / 'cao'), 'bootstrap'], repo))
                self.assertFalse(_readonly_tokens([str(repo / 'cao'), 'bootstrap'], repo))

    def test_redirect_or_compound_command_stays_mutating(self):
        for command in ('cao bootstrap > product.py', 'cao status && touch product.py',
                        'cao status $(touch product.py)'):
            with self.subTest(command=command):
                self.assertTrue(tool_is_mutating({'tool_name': 'bash', 'tool_input': {'command': command}}))
