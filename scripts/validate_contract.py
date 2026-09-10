"""Validate the project contract without requiring third-party packages.

When jsonschema is installed, the selected schema is used first. Otherwise the
shared fallback interprets the package's JSON Schema constraints.
"""
import argparse
import hashlib
import json
import unicodedata
import sys
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "references" / "schemas"
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from qbank_pipeline import schema_validate as shared_schema_validate
from qbank_pipeline import stable_question_id


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def schema_validate(instance, schema_name):
    shared_schema_validate(instance, schema_name)


def require(obj, fields, label):
    missing = [field for field in fields if field not in obj]
    if missing:
        raise ValueError(f"{label}: missing fields: {', '.join(missing)}")


def ensure_allowed(obj, fields, label):
    unknown = sorted(set(obj) - set(fields))
    if unknown:
        raise ValueError(f"{label}: unknown fields: {', '.join(unknown)}")


def validate_question_records(data):
    questions = data.get("questions", data if isinstance(data, list) else [])
    if not isinstance(questions, list) or not questions:
        raise ValueError("question bank must contain a non-empty questions array")
    required = ["id", "display_id", "number", "type", "subject", "knowledge_points",
                "difficulty", "difficulty_source", "text", "options", "answer",
                "explanation", "source", "match", "assets", "status"]
    eligible = 0
    for index, question in enumerate(questions, 1):
        if not isinstance(question, dict):
            raise ValueError(f"question[{index}] must be an object")
        require(question, required, f"question[{index}]")
        ensure_allowed(question, required + ["difficulty_label", "extensions"], f"question[{index}]")
        expected_id = stable_question_id(question)
        if question.get("id") != expected_id:
            raise ValueError(f"question[{index}].id is not stable: expected {expected_id}")
        schema_validate(question, "question.schema.json")
        status = question["status"]
        ensure_allowed(status, ["question", "answer", "explanation", "source", "eligible_for_random_paper", "eligibility_reasons", "extensions"], f"question[{index}].status")
        ensure_allowed(question["source"], ["file", "page", "set", "question", "extensions"], f"question[{index}].source")
        ensure_allowed(question["match"], ["method", "confidence", "candidates", "conflict", "evidence", "extensions"], f"question[{index}].match")
        require(status, ["question", "answer", "explanation", "source",
                         "eligible_for_random_paper", "eligibility_reasons"],
                f"question[{index}].status")
        if status["eligible_for_random_paper"]:
            eligible += 1
            if status["question"] != "verified" or status["answer"] != "verified":
                raise ValueError(f"question[{index}] is eligible but not fully verified")
            if status["explanation"] != "verified" or status["source"] != "verified":
                raise ValueError(f"question[{index}] is eligible but explanation/source is not verified")
            if "[unclear]" in question["text"] or "[unclear]" in question["answer"]:
                raise ValueError(f"question[{index}] is eligible but contains [unclear]")
            if not str(question.get("answer", "")).strip() or not str(question.get("explanation", "")).strip():
                raise ValueError(f"question[{index}] is eligible but answer/explanation is empty")
            if question.get("match", {}).get("conflict") is True:
                raise ValueError(f"question[{index}] is eligible but match.conflict is true")
            if float(question.get("match", {}).get("confidence", 0)) < 0.9:
                raise ValueError(f"question[{index}] is eligible but match.confidence is below 0.9")
    return len(questions), eligible


def validate_manifest(data):
    require(data, ["run_id", "project_root", "input_files", "script_version",
                   "question_signature_version", "prompt_version", "prompt_hash", "models", "runtime", "config", "status"],
            "manifest")
    ensure_allowed(data, ["run_id", "project_root", "input_files", "script_version", "question_signature_version", "prompt_version", "prompt_hash", "models", "runtime", "config", "status", "warnings", "failure", "extensions"], "manifest")
    schema_validate(data, "manifest.schema.json")
    for index, item in enumerate(data["input_files"], 1):
        require(item, ["path", "sha256", "size", "type", "pages", "pages_status", "role", "included", "reason"],
                f"manifest.input_files[{index}]")
        ensure_allowed(item, ["path", "sha256", "size", "type", "pages", "pages_status", "role", "included", "reason", "extensions"], f"manifest.input_files[{index}]")


def validate_audit(data):
    require(data, ["run_id", "status", "counts", "difficulty_distribution", "randomization", "issues"], "audit")
    require(data["difficulty_distribution"], ["raw_all", "eligible_pool", "paper_target", "paper_actual", "deviation"], "audit.difficulty_distribution")
    require(data["randomization"], ["random_seed", "selection_algorithm", "constraints", "candidate_pool_hash", "fallback_used"], "audit.randomization")
    ensure_allowed(data, ["run_id", "status", "counts", "difficulty_distribution", "randomization", "issues", "extensions"], "audit")
    schema_validate(data, "audit.schema.json")


def validate_config(data):
    require(data, ["project_root", "question_bank", "paper_count", "random_seed", "requirements"], "config")
    ensure_allowed(data, ["project_root", "input_dir", "work_dir", "output_dir", "archive_dir", "question_bank", "run_id", "paper_count", "random_seed", "requirements", "mathjax_local_script", "mathjax_url", "mathjax_allow_network", "mathjax_auto_download", "mathjax_version", "prompt_version", "prompt_hash", "models", "runtime", "pdf", "extensions"], "config")
    if not isinstance(data["requirements"], dict) or not isinstance(data["requirements"].get("by_type"), dict):
        raise ValueError("config.requirements.by_type must be an object")
    ensure_allowed(data["requirements"], ["questions", "by_type", "max_source_repetition", "allow_cross_paper_reuse", "min_subject_coverage", "max_difficulty_deviation", "extensions"], "config.requirements")
    schema_validate(data, "config.schema.json")


def validate_question_bank(data):
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        raise ValueError("question bank must contain a questions array")
    validate_question_records(data)
    schema_validate(data, "question_bank.schema.json")


def validate_question_bank_questions(data):
    questions = data["questions"]
    if not questions:
        raise ValueError("question bank must contain a non-empty questions array")
    for index, question in enumerate(questions, 1):
        require(question, ["id", "display_id", "number", "type", "subject", "knowledge_points", "difficulty", "difficulty_source", "text", "options", "answer", "explanation", "source", "match", "assets", "status"], f"question[{index}]")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=["question", "question_bank", "config", "manifest", "audit"])
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    data = load(args.path)
    if args.kind == "question":
        count, eligible = validate_question_records(data)
        print(f"OK question bank: {count} questions, {eligible} eligible")
    elif args.kind == "question_bank":
        validate_question_bank(data)
        print(f"OK question bank envelope: {len(data['questions'])} questions")
    elif args.kind == "config":
        validate_config(data)
        print("OK config")
    elif args.kind == "manifest":
        validate_manifest(data)
        print("OK manifest")
    else:
        validate_audit(data)
        print("OK audit")


if __name__ == "__main__":
    main()
