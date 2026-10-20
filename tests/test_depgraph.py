import unittest

from changeguard.depgraph import build_graph
from changeguard.testmap import TestIndex, is_source, is_test
from tests.helpers import make_repo


class DepGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = make_repo({
            "app/__init__.py": "",
            "app/core.py": "def f(): return 1\n",
            "app/api.py": "from app import core\nfrom . import util\n",
            "app/util.py": "from .core import f\n",
            "tests/test_api.py": "import app.api\n",
            "web/lib.ts": "export const a = 1;\n",
            "web/index.ts": "import { a } from './lib';\nexport * from './lib.js';\n",
            "web/index.test.ts": "import './index';\nconst x = require('./lib');\n",
            "web/broken.py": "def (:\n",
        })
        cls.g = build_graph(cls.repo, "HEAD")

    def test_python_edges_absolute_and_relative(self):
        self.assertIn("app/core.py", self.g.imports["app/api.py"])
        self.assertIn("app/util.py", self.g.imports["app/api.py"])
        self.assertIn("app/core.py", self.g.imports["app/util.py"])

    def test_transitive_dependents_with_distance(self):
        dep = self.g.dependents(["app/core.py"], 4)
        self.assertEqual(dep["app/util.py"], 1)
        self.assertEqual(dep["app/api.py"], 1)
        self.assertEqual(dep["tests/test_api.py"], 2)
        self.assertNotIn("app/core.py", dep)

    def test_typescript_edges(self):
        self.assertIn("web/lib.ts", self.g.imports["web/index.ts"])
        self.assertIn("web/lib.ts", self.g.imports["web/index.test.ts"])
        self.assertIn("web/index.ts", self.g.imports["web/index.test.ts"])

    def test_syntax_error_file_does_not_crash(self):
        self.assertEqual(self.g.imports.get("web/broken.py", set()), set())

    def test_test_detection_and_name_matching(self):
        self.assertTrue(is_test("web/index.test.ts") and is_test("tests/test_api.py"))
        self.assertTrue(is_source("app/core.py") and not is_source("README.md"))
        idx = TestIndex(self.g.files)
        self.assertIn("web/index.test.ts", idx.by_name("web/index.ts"))
        self.assertIn("tests/test_api.py", idx.tests_for("app/core.py", self.g))


if __name__ == "__main__":
    unittest.main()
