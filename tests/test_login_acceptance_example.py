"""Execute the repaired deletion assertion with and without the original fault."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(os.name == 'nt' or not shutil.which('bash'), 'POSIX sample runner')
class LoginAcceptanceExample(unittest.TestCase):
    def test_ignored_deletion_cannot_pass_using_the_old_password(self):
        script = (ROOT / 'examples/login-service-acceptance.sh').read_text()
        block = script.split('# AC-4 运行中删除用户', 1)[1].split('# AC-6 ', 1)[0]
        block = '# AC-4 运行中删除用户' + block
        harness = '''ALICE_PASSWORD=old
ALICE_NEW_PASSWORD=current
BOB_PASSWORD=bob
ALICE_HASH=hash
deleted=no
login_code() {
  if [ "$1" = bob ]; then echo 200;
  elif [ "$deleted" = yes ]; then echo 401;
  elif [ "$2" = current ]; then echo 200;
  else echo 401; fi
}
write_config() { if [ "$FAULT" = no ]; then deleted=yes; fi; }
wait_for_login() { [ "$(login_code "$1" "$2")" = "$3" ]; }
record() { echo "$1 $2"; }
'''
        for fault, expected in [('yes', 'failed'), ('no', 'passed')]:
            result = subprocess.run(['bash', '-c', harness + block], text=True, capture_output=True,
                                    env={**os.environ, 'FAULT': fault}, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'AC-4 ' + expected)

    def test_missing_jar_does_not_overwrite_real_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'scripts/verification/acceptance.sh'
            script.parent.mkdir(parents=True)
            shutil.copy2(ROOT / 'examples/login-service-acceptance.sh', script)
            config = root / 'config/users.yaml'
            config.parent.mkdir()
            config.write_text('valuable original configuration\n')
            result = subprocess.run(['bash', str(script)], text=True, capture_output=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(config.read_text(), 'valuable original configuration\n')
