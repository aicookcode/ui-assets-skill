"""Releases from a flattened checkout exclude local data and survive relocation."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import package_skill


class PackageTests(unittest.TestCase):
    def test_root_checkout_packages_only_installable_resources(self):
        with tempfile.TemporaryDirectory() as folder:
            checkout = Path(folder) / "github-download-main"
            checkout.mkdir()
            actual = Path(package_skill.__file__).resolve().parents[1]
            # Package real resources, plus local files that must never be shipped.
            import shutil
            for name in ("SKILL.md", "requirements.txt", "README.md", "agents", "references", "scripts"):
                source = actual / name
                target = checkout / name
                if source.is_dir():
                    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
                else:
                    shutil.copyfile(source, target)
            for name in (".git/config", ".venv/local.txt", "tests/local.py", "output/old.zip",
                         "docs/private.md", "scripts/provider.json", "scripts/__pycache__/local.py"):
                p = checkout / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("private-local-fixture", encoding="utf-8")
            output = checkout / "output/packages/release.zip"
            with patch.object(package_skill, "__file__", str(checkout / "scripts/package_skill.py")):
                package_skill.package(output)
                package_skill.package(output)  # Must not recursively include an earlier release.
            with ZipFile(output) as z:
                names = z.namelist()
                self.assertTrue(all(n.startswith("ui-assets-skill/") for n in names))
                for excluded in (".git/", ".venv/", "tests/", "output/", "docs/", "provider.json", "__pycache__"):
                    self.assertFalse(any(excluded in n for n in names), excluded)
                self.assertIsNone(z.testzip())
                installed = Path(folder) / "installed"
                z.extractall(installed)
            script = installed / "ui-assets-skill/scripts/studio.py"
            run = subprocess.run([sys.executable, "-B", str(script), "--help"], cwd=folder,
                                 capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(run.returncode, 0, run.stderr)


if __name__ == "__main__":
    unittest.main()
