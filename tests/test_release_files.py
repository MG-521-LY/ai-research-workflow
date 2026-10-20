"""Publishing enumerates Git files, not a user's ignored papers or state."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ReleaseFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="release files ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / ".gitignore").write_text("outputs/\n*.pdf\n", encoding="utf-8")
        spec = importlib.util.spec_from_file_location("release_files", ROOT / "scripts/release_files.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_ignored_local_outputs_are_not_release_files(self):
        (self.root / "outputs").mkdir()
        (self.root / "outputs/private.pdf").write_bytes(b"private fixture")
        (self.root / "README.md").write_text("public fixture", encoding="utf-8")
        names = {p.relative_to(self.root).as_posix() for p in self.module.public_files(self.root)}
        self.assertEqual(names, {".gitignore", "README.md"})

    def test_force_added_pdf_is_included_so_the_gate_can_reject_it(self):
        (self.root / "bad.pdf").write_bytes(b"forced fixture")
        subprocess.run(["git", "-C", str(self.root), "add", "-f", "bad.pdf"], check=True)
        names = {p.name for p in self.module.public_files(self.root)}
        self.assertIn("bad.pdf", names)

    def test_spaces_and_unicode_filenames_survive_git_enumeration(self):
        name = "使用 说明.md"
        (self.root / name).write_text("fixture", encoding="utf-8")
        self.assertIn(name, {p.name for p in self.module.public_files(self.root)})


if __name__ == "__main__":
    unittest.main()
