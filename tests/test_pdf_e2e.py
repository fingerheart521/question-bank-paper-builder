import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


export_pdf = load_module("export_pdf_e2e", ROOT / "scripts" / "export_pdf.py")
pipeline = load_module("qbank_pipeline_e2e", ROOT / "scripts" / "qbank_pipeline.py")


class PdfBrowserEndToEndTests(unittest.TestCase):
    def available_browsers(self):
        errors = export_pdf.runtime_dependency_errors()
        if errors:
            self.skipTest("; ".join(errors))
        browsers = export_pdf.discover_browsers()
        if not browsers:
            self.skipTest("no Chromium-compatible browser")
        return browsers

    def test_real_browsers_export_generated_html_without_formula(self):
        for browser in self.available_browsers():
            with self.subTest(browser=browser.name), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                html_path = root / "paper.html"
                question = {
                    "text": "普通无公式题目", "options": [], "answer": "A", "explanation": "普通解析。",
                    "source": {"set": "测试卷", "question": 1, "page": 1},
                }
                pipeline.render_html([question], 1, html_path, {}, "combined")
                self.assertFalse(export_pdf.html_has_formula(html_path))
                result = export_pdf.export_one(
                    browser, html_path, root / "paper.pdf", 0, root / "profile", {}, max_attempts=1
                )
                self.assertEqual(result["status"], "passed")
                self.assertEqual(result["formula_nodes"], 0)

    def test_real_browsers_export_formula_with_local_mathjax(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            try:
                mathjax = pipeline.ensure_mathjax(root)
            except RuntimeError as exc:
                self.skipTest(str(exc))
            for browser in self.available_browsers():
                with self.subTest(browser=browser.name):
                    browser_root = root / browser.stem
                    html_path = browser_root / "paper.html"
                    question = {
                        "text": r"计算 $x^2+\frac{1}{2}$", "options": [], "answer": "A", "explanation": "公式解析。",
                        "source": {"set": "公式测试卷", "question": 1, "page": 1},
                    }
                    pipeline.render_html(
                        [question], 1, html_path, {"mathjax_local_script": str(mathjax)}, "combined"
                    )
                    self.assertTrue(export_pdf.html_has_formula(html_path))
                    result = export_pdf.export_one(
                        browser, html_path, browser_root / "paper.pdf", 0,
                        browser_root / "profile", {}, max_attempts=1
                    )
                    self.assertEqual(result["status"], "passed")
                    self.assertGreater(result["formula_nodes"], 0)

    def test_real_browsers_export_complex_formula_with_local_mathjax(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            try:
                mathjax = pipeline.ensure_mathjax(root)
            except RuntimeError as exc:
                self.skipTest(str(exc))
            formula = r"$\begin{cases} x^2+y^2=1 \\ x-y=0 \end{cases}$\\[ A=\begin{pmatrix}1&2\\3&4\end{pmatrix} \]"
            for browser in self.available_browsers():
                with self.subTest(browser=browser.name):
                    browser_root = root / (browser.stem + "-complex")
                    html_path = browser_root / "paper.html"
                    pipeline.render_html(
                        [{"text": formula, "options": [], "answer": "A", "explanation": "复杂公式解析。", "source": {"set": "复杂公式卷", "question": 1, "page": 1}}],
                        1, html_path, {"mathjax_local_script": str(mathjax)}, "combined"
                    )
                    result = export_pdf.export_one(browser, html_path, browser_root / "paper.pdf", 0, browser_root / "profile", {}, max_attempts=1)
                    self.assertEqual(result["status"], "passed")
                    self.assertGreater(result["formula_nodes"], 0)


if __name__ == "__main__":
    unittest.main()
