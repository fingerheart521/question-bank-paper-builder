import importlib.util
import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("export_pdf", ROOT / "scripts" / "export_pdf.py")
export_pdf = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(export_pdf)


class PdfExporterTests(unittest.TestCase):
    def test_export_retries_with_new_port_after_startup_failure(self):
        calls = []

        def fake_attempt(chrome, html_path, output, port, attempt_profile, pdf_config=None, audit_path=None):
            calls.append((port, attempt_profile))
            if len(calls) < 3:
                raise RuntimeError("Chromium DevTools endpoint did not start")
            return {"status": "passed"}

        with mock.patch.object(export_pdf, "_export_one_attempt", side_effect=fake_attempt), \
             mock.patch.object(export_pdf, "free_port", side_effect=[41001, 41002, 41003]), \
             mock.patch.object(export_pdf.time, "sleep"):
            result = export_pdf.export_one(
                Path("chrome"), Path("paper.html"), Path("paper.pdf"), 0, Path("profile"), max_attempts=3
            )

        self.assertEqual(result["status"], "passed")
        self.assertEqual([port for port, _ in calls], [41001, 41002, 41003])
        self.assertEqual([profile.name for _, profile in calls], ["profile-1", "profile-2", "profile-3"])

    def test_html_expectations_detect_modes_and_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "paper_01.html"
            path.write_text(
                '<article class="question">1</article><article class="question">2</article>'
                '<article class="answer">a</article><article class="answer">b</article>',
                encoding="utf-8",
            )
            self.assertEqual(export_pdf.html_expectations(path), {"mode": "combined", "questions": 2, "answers": 2})

    def test_formula_detection_ignores_scripts_and_uses_visible_content(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "paper.html"
            path.write_text(
                '<script>window.MathJax={tex:{inlineMath:[["$","$"]]}}</script>'
                '<style>.x::before{content:"$fake$"}</style>'
                '<main data-has-formula="false">ordinary text</main>',
                encoding="utf-8",
            )
            self.assertFalse(export_pdf.html_has_formula(path))
            path.write_text('<html><head><title>$title$</title></head><body>ordinary text</body></html>', encoding="utf-8")
            self.assertFalse(export_pdf.html_has_formula(path))
            path.write_text('<main>计算 $x^2$</main>', encoding="utf-8")
            self.assertTrue(export_pdf.html_has_formula(path))
            path.write_text('<main>价格 $12.50$，数量 $3$</main>', encoding="utf-8")
            self.assertFalse(export_pdf.html_has_formula(path))

    def test_empty_html_dir_fails_and_writes_audit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / "pdf"
            old_argv = sys.argv
            sys.argv = [
                "export_pdf.py", "--html-dir", str(root / "html"), "--output-dir", str(output),
                "--chromium", str(root / "missing-chromium.exe"),
            ]
            try:
                with self.assertRaisesRegex(RuntimeError, "no HTML files found"):
                    export_pdf.main()
            finally:
                sys.argv = old_argv
            audit = json.loads((output / "pdf_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["status"], "failed")

    def test_pdf_disabled_skips_without_browser(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            html_dir = root / "html"
            html_dir.mkdir()
            (html_dir / "paper.html").write_text("<html></html>", encoding="utf-8")
            config = root / "config.json"
            config.write_text(json.dumps({"pdf": {"enabled": False}}), encoding="utf-8")
            output = root / "pdf"
            old_argv = sys.argv
            sys.argv = [
                "export_pdf.py", "--html-dir", str(html_dir), "--output-dir", str(output),
                "--chromium", str(root / "missing-chromium.exe"), "--config", str(config),
            ]
            try:
                export_pdf.main()
            finally:
                sys.argv = old_argv
            audit = json.loads((output / "pdf_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["status"], "skipped")

    def test_missing_browser_writes_failed_audit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            html_dir = root / "html"
            html_dir.mkdir()
            (html_dir / "paper.html").write_text("<html><body>1.</body></html>", encoding="utf-8")
            output = root / "pdf"
            old_argv = sys.argv
            sys.argv = ["export_pdf.py", "--html-dir", str(html_dir), "--output-dir", str(output)]
            try:
                with mock.patch.object(export_pdf, "runtime_dependency_errors", return_value=[]), \
                     mock.patch.object(export_pdf, "discover_browser", side_effect=RuntimeError("No browser")):
                    with self.assertRaisesRegex(RuntimeError, "No browser"):
                        export_pdf.main()
            finally:
                sys.argv = old_argv
            audit = json.loads((output / "pdf_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["status"], "failed")
            self.assertIn("No browser", audit["issues"][0]["message"])

    def test_missing_runtime_dependency_writes_failed_audit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            html_dir = root / "html"
            html_dir.mkdir()
            (html_dir / "paper.html").write_text("<html><body>1.</body></html>", encoding="utf-8")
            output = root / "pdf"
            old_argv = sys.argv
            sys.argv = ["export_pdf.py", "--html-dir", str(html_dir), "--output-dir", str(output)]
            try:
                with mock.patch.object(export_pdf, "runtime_dependency_errors", return_value=["required command is unavailable: pdfinfo"]):
                    with self.assertRaisesRegex(RuntimeError, "pdfinfo"):
                        export_pdf.main()
            finally:
                sys.argv = old_argv
            audit = json.loads((output / "pdf_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["status"], "failed")
            self.assertIn("pdfinfo", audit["issues"][0]["message"])

    def test_file_export_failure_is_not_duplicated_in_audit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            html_dir = root / "html"
            html_dir.mkdir()
            (html_dir / "paper.html").write_text("<html><body>1.</body></html>", encoding="utf-8")
            output = root / "pdf"
            old_argv = sys.argv
            sys.argv = ["export_pdf.py", "--html-dir", str(html_dir), "--output-dir", str(output)]
            try:
                with mock.patch.object(export_pdf, "runtime_dependency_errors", return_value=[]), \
                     mock.patch.object(export_pdf, "discover_browser", return_value=Path("chrome")), \
                     mock.patch.object(export_pdf, "export_one", side_effect=RuntimeError("render failed")):
                    with self.assertRaisesRegex(RuntimeError, "render failed"):
                        export_pdf.main()
            finally:
                sys.argv = old_argv
            audit = json.loads((output / "pdf_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["status"], "failed")
            self.assertEqual([issue["message"] for issue in audit["issues"]], ["render failed"])


if __name__ == "__main__":
    unittest.main()
