"""Run every local Python regression suite, including new integration modules."""
import os
from pathlib import Path
import unittest

root = Path(__file__).resolve().parent
os.chdir(root)
suite = unittest.defaultTestLoader.discover(str(root), pattern='test_*.py', top_level_dir=str(root))
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
