import subprocess
import sys
import unittest
from src.pr_review_router.cli import main

class TestCLI(unittest.TestCase):
    def test_help(self):
        result = subprocess.run([sys.executable, '-m', 'src.pr_review_router.cli', '--help'], capture_output=True, text=True)
        self.assertIn('usage:', result.stdout)
        self.assertEqual(result.returncode, 0)

    def test_version(self):
        result = subprocess.run([sys.executable, '-m', 'src.pr_review_router.cli', '--version'], capture_output=True, text=True)
        self.assertIn('version', result.stdout)
        self.assertEqual(result.returncode, 0)

if __name__ == '__main__':
    unittest.main()