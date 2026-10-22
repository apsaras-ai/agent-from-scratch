import tempfile
import unittest
from pathlib import Path
from mini_agent.repository_index import RepositoryIndex

class RepoIndexTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory()
  self.root=Path(self.t.name)
  (self.root/"app.py").write_text(
    "import os\nfrom .helpers import item\nclass Service:\n"
    "    def run(self):\n        return item()\n",encoding="utf-8")
  (self.root/"helpers.py").write_text(
    "def item():\n    return 42\n",encoding="utf-8")
  self.index=RepositoryIndex(self.root)
  self.index.build()
 def tearDown(self): self.t.cleanup()
 def test_ast_symbols_imports(self):
  info=self.index.files["app.py"]
  self.assertIn("os",info.imports)
  self.assertIn(".helpers",info.imports)
  self.assertEqual([s.qualname for s in info.symbols],["Service","Service.run"])
 def test_search_and_map(self):
  hits=self.index.search("return item")
  self.assertEqual((hits[0].path,hits[0].line),("app.py",5))
  self.assertIn("Service.run",self.index.repository_map())
 def test_invalid_and_hidden(self):
  (self.root/".secret.py").write_text("SECRET=1",encoding="utf-8")
  (self.root/"bad.py").write_text("def broken(",encoding="utf-8")
  self.index.build()
  self.assertNotIn(".secret.py",self.index.files)
  self.assertNotIn("bad.py",self.index.files)
  with self.assertRaises(ValueError): self.index.search("")
 def test_no_code_execution(self):
  (self.root/"side_effect.py").write_text(
    "raise RuntimeError('must not run')\n",encoding="utf-8")
  self.index.build()
  self.assertIn("side_effect.py",self.index.files)

if __name__=="__main__":
 unittest.main()
