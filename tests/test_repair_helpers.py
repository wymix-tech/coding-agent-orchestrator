import unittest

from repair_helpers import RepairCase


class HelpersTests(RepairCase):
    def test_real_test_suite_runs(self):
        wid = self.start()
        rc, out = self.run_cli("verify", "--work", wid)
        self.assertEqual(rc, 0, out)
        result = out["verification"]["results"][0]
        self.assertEqual(result["status"], "passed")
        self.assertIn("Ran 1 test", result["stderr_tail"])


if __name__ == "__main__":
    unittest.main()
