import importlib.util
import io
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("mathjax_runtime", ROOT / "scripts" / "mathjax_runtime.py")
mathjax_runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mathjax_runtime)


class MathJaxRuntimeTests(unittest.TestCase):
    def test_safe_extract_rejects_symbolic_and_hard_links(self):
        for entry_type in (tarfile.SYMTYPE, tarfile.LNKTYPE):
            with self.subTest(entry_type=entry_type), tempfile.TemporaryDirectory() as folder:
                archive_path = Path(folder) / "unsafe.tar"
                with tarfile.open(archive_path, "w") as archive:
                    regular = tarfile.TarInfo("package/tex-svg.js")
                    content = b"test"
                    regular.size = len(content)
                    archive.addfile(regular, io.BytesIO(content))
                    link = tarfile.TarInfo("package/link.js")
                    link.type = entry_type
                    link.linkname = "tex-svg.js"
                    archive.addfile(link)
                with tarfile.open(archive_path, "r") as archive:
                    with self.assertRaisesRegex(RuntimeError, "link or special entry"):
                        mathjax_runtime._safe_extract(archive, Path(folder) / "output")


if __name__ == "__main__":
    unittest.main()
