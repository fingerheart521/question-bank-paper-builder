import importlib.util
import builtins
import json
import shutil
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("qbank_pipeline", ROOT / "scripts" / "qbank_pipeline.py")
pipeline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pipeline)


def question(qid, number, eligible=True, difficulty=2, source="示例卷"):
    record = {
        "id": "pending",
        "display_id": f"示例-{number}",
        "number": number,
        "type": "选择题",
        "subject": "示例学科",
        "knowledge_points": ["示例知识点"],
        "difficulty": difficulty,
        "difficulty_label": "简单" if difficulty <= 2 else "中等",
        "difficulty_source": "manual",
        "text": f"第 {number} 题题干",
        "options": ["A. 1", "B. 2"],
        "answer": "A",
        "explanation": "因为 A 正确。",
        "source": {"file": "input/example.pdf", "page": number, "set": source, "question": number},
        "match": {"method": "test", "confidence": 1, "candidates": [], "conflict": False, "evidence": [{"file": "input/example.pdf", "page": number}]},
        "assets": [],
        "status": {"question": "verified" if eligible else "needs_review", "answer": "verified" if eligible else "ambiguous", "explanation": "verified" if eligible else "ambiguous", "source": "verified", "eligible_for_random_paper": eligible, "eligibility_reasons": [] if eligible else ["review"]}
    }
    record["id"] = pipeline.stable_question_id(record)
    return record


class PipelineTests(unittest.TestCase):
    def config(self, count=1, papers=1):
        return {"run_id": "test", "paper_count": papers, "random_seed": 7, "requirements": {"questions": count, "by_type": {"选择题": count}, "max_source_repetition": count, "allow_cross_paper_reuse": False}}

    def test_reproducible_and_audited(self):
        questions = [question(f"q-{i}", i, difficulty=2 if i < 3 else 3) for i in range(1, 7)]
        config = self.config(count=2, papers=2)
        first, audit_a = pipeline.compose(questions, config)
        second, audit_b = pipeline.compose(questions, config)
        self.assertEqual([[q["id"] for q in p] for p in first], [[q["id"] for q in p] for p in second])
        self.assertEqual(audit_a["randomization"]["candidate_pool_hash"], audit_b["randomization"]["candidate_pool_hash"])
        self.assertIn("deviation", audit_a["difficulty_distribution"])
        self.assertEqual(audit_a["status"], "passed")

    def test_review_question_cannot_enter_pool(self):
        questions = [question("q-review", 1, eligible=False)]
        with self.assertRaises(ValueError):
            pipeline.compose(questions, self.config())

    def test_capacity_error_is_explicit(self):
        questions = [question("q-1", 1), question("q-2", 2)]
        with self.assertRaisesRegex(ValueError, "capacity insufficient"):
            pipeline.compose(questions, self.config(count=2, papers=2))

    def test_alternative_candidate_updates_source_count(self):
        questions = [
            question("q-1", 1, source="卷一"),
            question("q-2", 2, source="卷一"),
            question("q-3", 3, source="卷二"),
        ]
        config = self.config(count=2)
        config["requirements"]["max_source_repetition"] = 1
        papers, audit = pipeline.compose(questions, config)
        self.assertEqual({q["source"]["set"] for q in papers[0]}, {"卷一", "卷二"})
        self.assertEqual(audit["status"], "passed")

    def test_html_has_separate_outputs_and_answer_page(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            paper = [question("q-1", 1)]
            pipeline.render_html(paper, 1, output / "combined.html", {"mathjax_local_script": ""}, "combined")
            pipeline.render_html(paper, 1, output / "questions.html", {"mathjax_local_script": ""}, "questions")
            pipeline.render_html(paper, 1, output / "answers.html", {"mathjax_local_script": ""}, "answers")
            combined = (output / "combined.html").read_text(encoding="utf-8")
            questions = (output / "questions.html").read_text(encoding="utf-8")
            answers = (output / "answers.html").read_text(encoding="utf-8")
            self.assertIn("break-before:page", combined)
            self.assertIn('data-has-formula="false"', combined)
            self.assertIn("答案与解析", combined)
            self.assertNotIn("答案与解析", questions)
            self.assertNotIn("第 1 题题干", answers)

    def test_formula_requires_local_mathjax_resource(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "mathjax_local_script"):
                pipeline.render_html(
                    [{**question("q-1", 1), "text": r"计算 $x^2$"}],
                    1, Path(folder) / "paper.html", {"mathjax_auto_download": False}, "combined"
                )

    def test_formula_accepts_configured_mathjax_url(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "paper.html"
            pipeline.render_html(
                [{**question("q-1", 1), "text": r"计算 $x^2$"}],
                1, output,
                {"mathjax_url": "https://example.test/mathjax/tex-svg.js", "mathjax_allow_network": True},
                "combined",
            )
            self.assertIn("https://example.test/mathjax/tex-svg.js", output.read_text(encoding="utf-8"))

    def test_formula_auto_download_uses_pinned_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime = root / "tex-svg.js"
            runtime.write_text("// test MathJax", encoding="utf-8")
            output = root / "output" / "run" / "paper.html"
            with mock.patch.object(pipeline, "ensure_mathjax", return_value=runtime) as download:
                pipeline.render_html(
                    [{**question("q-1", 1), "text": r"计算 $x^2$"}],
                    1, output, {"mathjax_auto_download": True}, "combined"
                )
            download.assert_called_once()
            rendered = output.read_text(encoding="utf-8")
            self.assertIn(runtime.as_uri(), rendered)
            self.assertIn('data-has-formula="true"', rendered)

    def test_hard_audit_failure_is_reported(self):
        questions = [question("ignored", 1)]
        config = self.config(count=1)
        config["requirements"]["min_subject_coverage"] = {"不存在的学科": 1}
        papers, audit = pipeline.compose(questions, config)
        self.assertEqual(len(papers), 1)
        self.assertEqual(audit["status"], "failed")
        self.assertTrue(any(item["level"] == "ERROR" for item in audit["issues"]))

    def test_eligibility_requires_match_quality_and_content(self):
        bad = question("ignored", 1)
        bad["answer"] = ""
        bad["match"]["confidence"] = 0.4
        with self.assertRaisesRegex(ValueError, "answer/explanation is empty"):
            pipeline.compose([bad], self.config())

    def test_stable_id_is_validated(self):
        bad = question("ignored", 1)
        bad["id"] = "q-0000000000000000"
        with self.assertRaisesRegex(ValueError, "id is not stable"):
            pipeline.compose([bad], self.config())

    def test_question_bank_schema_rejects_malformed_question(self):
        malformed = {"questions": [{"id": "q-0000000000000000"}]}
        with self.assertRaises(Exception):
            pipeline.schema_validate(malformed, "question_bank.schema.json")

    def test_fallback_rejects_invalid_manifest_hash(self):
        manifest = {
            "run_id": "x", "project_root": "x", "input_files": [{
                "path": "input/a.pdf", "sha256": "bad", "size": 1, "type": "pdf",
                "pages": None, "pages_status": "tool_unavailable", "role": "试卷",
                "included": True, "reason": "test"
            }],
            "script_version": "x", "question_signature_version": "question_signature_v1",
            "prompt_version": "x", "prompt_hash": "x", "models": [], "runtime": {},
            "config": {}, "status": "running"
        }
        with self.assertRaisesRegex(ValueError, "sha256"):
            pipeline.fallback_schema_validate(manifest, "manifest.schema.json")

    def test_fallback_rejects_noneligible_bad_nested_types(self):
        record = question("ignored", 1)
        record["answer"] = None
        record["explanation"] = 123
        with self.assertRaisesRegex(ValueError, "question.answer"):
            pipeline.fallback_schema_validate(record, "question.schema.json")

    def test_fallback_rejects_non_integer_difficulty(self):
        for value in ("2", True, 2.0):
            record = question("ignored", 1)
            record["difficulty"] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "difficulty"):
                pipeline.fallback_schema_validate(record, "question.schema.json")

    def test_fallback_validates_optional_difficulty_label(self):
        record = question("ignored", 1)
        record["difficulty_label"] = "未知"
        pipeline.fallback_schema_validate(record, "question.schema.json")
        record["difficulty_label"] = "中等偏难"
        with self.assertRaisesRegex(ValueError, "difficulty_label"):
            pipeline.fallback_schema_validate(record, "question.schema.json")

    def test_fallback_rejects_config_types_and_nested_requirements(self):
        config = self.config()
        config.update({"project_root": ".", "question_bank": "input/question_bank.json"})
        config["project_root"] = 123
        with self.assertRaisesRegex(ValueError, "config.project_root"):
            pipeline.fallback_schema_validate(config, "config.schema.json")
        config = self.config()
        config.update({"project_root": ".", "question_bank": "input/question_bank.json"})
        config["requirements"]["by_type"] = {"选择题": "1"}
        with self.assertRaisesRegex(ValueError, "config.requirements.by_type.选择题"):
            pipeline.fallback_schema_validate(config, "config.schema.json")

    def test_fallback_rejects_manifest_and_audit_nested_structure(self):
        manifest = {
            "run_id": "x", "project_root": "x", "input_files": [],
            "script_version": "x", "question_signature_version": "question_signature_v1",
            "prompt_version": "x", "prompt_hash": "x", "models": [], "runtime": {},
            "config": {}, "status": "bogus"
        }
        with self.assertRaisesRegex(ValueError, "manifest.status"):
            pipeline.fallback_schema_validate(manifest, "manifest.schema.json")
        audit = {"run_id": "x", "status": "passed", "counts": {},
                 "difficulty_distribution": {"raw_all": {}},
                 "randomization": {}, "issues": []}
        with self.assertRaisesRegex(ValueError, "difficulty_distribution"):
            pipeline.fallback_schema_validate(audit, "audit.schema.json")

    def test_validate_contract_uses_same_fallback_without_jsonschema(self):
        contract_spec = importlib.util.spec_from_file_location(
            "validate_contract", ROOT / "scripts" / "validate_contract.py"
        )
        contract = importlib.util.module_from_spec(contract_spec)
        contract_spec.loader.exec_module(contract)
        record = question("ignored", 1)
        record["difficulty"] = "2"
        real_import = builtins.__import__

        def import_without_jsonschema(name, *args, **kwargs):
            if name == "jsonschema":
                raise ImportError("jsonschema intentionally unavailable")
            return real_import(name, *args, **kwargs)

        original_import = builtins.__import__
        builtins.__import__ = import_without_jsonschema
        try:
            with self.assertRaisesRegex(ValueError, "difficulty"):
                contract.schema_validate(record, "question.schema.json")
        finally:
            builtins.__import__ = original_import

    def test_same_run_id_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "input").mkdir()
            bank = {"questions": [question("ignored", 1)]}
            (root / "input" / "question_bank.json").write_text(json.dumps(bank, ensure_ascii=False), encoding="utf-8")
            config = self.config(count=1)
            config.update({"project_root": str(root), "question_bank": "input/question_bank.json", "run_id": "fixed"})
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            pipeline.run(config_path)
            with self.assertRaisesRegex(ValueError, "run_id already exists"):
                pipeline.run(config_path)

    def test_input_directory_cannot_overlap_output_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "input").mkdir()
            config = self.config(count=1)
            config.update({
                "project_root": str(root),
                "input_dir": ".",
                "question_bank": "input/question_bank.json",
            })
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "input_dir overlaps"):
                pipeline.run(config_path)

    def test_failed_composition_does_not_write_success_artifacts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "input").mkdir()
            bank = {"questions": [question("ignored", 1)]}
            (root / "input" / "question_bank.json").write_text(json.dumps(bank, ensure_ascii=False), encoding="utf-8")
            config = self.config(count=1)
            config.update({"project_root": str(root), "question_bank": "input/question_bank.json", "run_id": "failed"})
            config["requirements"]["min_subject_coverage"] = {"不存在的学科": 1}
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "composition audit failed"):
                pipeline.run(config_path)
            output = root / "output" / "failed"
            self.assertFalse((output / "papers.json").exists())
            self.assertFalse(list(output.glob("*.html")))
            self.assertTrue((root / "archive" / "failed" / "manifest.failed.json").exists())


if __name__ == "__main__":
    unittest.main()
