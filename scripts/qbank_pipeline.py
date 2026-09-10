"""Canonical v1.0 question-bank pipeline.

This entry point intentionally accepts only the v1.0 structured contract. It
does not read legacy flat records or hard-coded project paths.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import random
import sys
import csv
import subprocess
import unicodedata
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    from mathjax_runtime import ensure_mathjax
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from mathjax_runtime import ensure_mathjax
try:
    from formula_detection import contains_formula
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from formula_detection import contains_formula

TOOL_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = TOOL_ROOT / "references" / "schemas"
SCRIPT_VERSION = "qbank_pipeline-v1.0"
QUESTION_SIGNATURE_VERSION = "question_signature_v1"


def schema_validate(instance, schema_name):
    """Validate with jsonschema when available, otherwise use strict built-ins."""
    try:
        import jsonschema
    except ImportError:
        fallback_schema_validate(instance, schema_name)
        return
    schema = load_json(SCHEMA_DIR / schema_name)
    # Register sibling resources by filename for jsonschema 4.x and older releases.
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    try:
        from referencing import Registry, Resource
        registry = Registry().with_resources([
            (path.name, Resource.from_contents(load_json(path)))
            for path in SCHEMA_DIR.glob("*.schema.json")
        ])
        validator_cls(schema, registry=registry).validate(instance)
    except ImportError:
        resolver = jsonschema.RefResolver.from_schema(
            schema,
            store={path.name: load_json(path) for path in SCHEMA_DIR.glob("*.schema.json")},
        )
        validator_cls(schema, resolver=resolver).validate(instance)


def fallback_schema_validate(instance, schema_name):
    """Validate the supported schemas without importing jsonschema.

    The fallback interprets the JSON Schema keywords used by this package instead
    of maintaining a second, hand-written contract that can drift from the files.
    """
    schemas = {path.name: load_json(path) for path in SCHEMA_DIR.glob("*.schema.json")}
    if schema_name not in schemas:
        raise ValueError(f"unknown schema: {schema_name}")

    def fail(path, message):
        raise ValueError(f"{path} schema failed: {message}")

    def matches_type(value, expected):
        return {
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "null": value is None,
        }.get(expected, False)

    def validate(value, schema, path):
        if "$ref" in schema:
            reference = schema["$ref"]
            if reference not in schemas:
                fail(path, f"unsupported reference {reference}")
            validate(value, schemas[reference], path)
            return

        expected_types = schema.get("type")
        if expected_types:
            expected_types = [expected_types] if isinstance(expected_types, str) else expected_types
            if not any(matches_type(value, expected) for expected in expected_types):
                fail(path, f"expected type {' or '.join(expected_types)}")

        if "enum" in schema and not any(type(value) is type(item) and value == item for item in schema["enum"]):
            fail(path, f"value {value!r} is not in the allowed enum")

        if isinstance(value, dict):
            required = schema.get("required", [])
            missing = [field for field in required if field not in value]
            if missing:
                fail(path, f"missing {', '.join(missing)}")
            if len(value) < schema.get("minProperties", 0):
                fail(path, f"requires at least {schema['minProperties']} properties")
            properties = schema.get("properties", {})
            additional = schema.get("additionalProperties", True)
            for key, item in value.items():
                item_path = f"{path}.{key}"
                if key in properties:
                    validate(item, properties[key], item_path)
                elif additional is False:
                    fail(path, f"unknown field {key}")
                elif isinstance(additional, dict):
                    validate(item, additional, item_path)

        if isinstance(value, list):
            if len(value) < schema.get("minItems", 0):
                fail(path, f"requires at least {schema['minItems']} items")
            item_schema = schema.get("items")
            if item_schema:
                for index, item in enumerate(value):
                    validate(item, item_schema, f"{path}[{index}]")

        if isinstance(value, str):
            if len(value) < schema.get("minLength", 0):
                fail(path, f"requires at least {schema['minLength']} characters")
            if "pattern" in schema and re.search(schema["pattern"], value) is None:
                fail(path, f"does not match pattern {schema['pattern']}")

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                fail(path, f"must be at least {schema['minimum']}")
            if "maximum" in schema and value > schema["maximum"]:
                fail(path, f"must be at most {schema['maximum']}")
            if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
                fail(path, f"must be greater than {schema['exclusiveMinimum']}")

    root_label = schema_name.removesuffix(".schema.json")
    validate(instance, schemas[schema_name], root_label)


def pdf_page_count(path: Path) -> int:
    try:
        result = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=False, check=False)
        stdout = (result.stdout or b"").decode("utf-8", errors="replace")
        for line in stdout.splitlines():
            if line.lower().startswith("pages:"):
                return int(line.split(":", 1)[1].strip())
    except (OSError, ValueError):
        pass
    for module_name in ("pypdf", "PyPDF2"):
        try:
            module = __import__(module_name)
            return len(module.PdfReader(str(path)).pages)
        except Exception:
            continue
    return None


def infer_file_role(path: Path) -> str:
    name = path.stem.lower()
    if any(token in name for token in ("答案", "answer", "key")):
        return "答案"
    if any(token in name for token in ("解析", "solution", "explanation")):
        return "解析"
    if any(token in name for token in ("题库", "question", "bank")):
        return "题库"
    if any(token in name for token in ("试卷", "卷", "paper", "test", "exam")):
        return "试卷"
    if any(token in name for token in ("讲义", "专题", "笔记", "lecture", "note")):
        return "讲义"
    if any(token in name for token in ("封面", "cover")):
        return "封面"
    return "其他"


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def normalized_question_signature(question):
    """Return the v1 canonical identity fields used by the stable question ID."""
    def normalize(value):
        value = unicodedata.normalize("NFC", str(value or ""))
        value = value.replace("\r\n", "\n").replace("\r", "\n")
        return " ".join(value.split())
    payload = {
        "version": QUESTION_SIGNATURE_VERSION,
        "subject": normalize(question.get("subject", "")),
        "source_set": normalize(question.get("source", {}).get("set", "")),
        "number": normalize(question.get("number", "")),
        "text": normalize(question.get("text", "")),
        "options": [normalize(option) for option in question.get("options", [])],
    }
    return canonical_json(payload)


def stable_question_id(question):
    return "q-" + sha256_bytes(normalized_question_signature(question).encode("utf-8"))[:16]


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_text(value):
    """Escape text while preserving MathJax delimiters and source line breaks."""
    return html.escape(str(value or ""), quote=False).replace("\n", "<br>")


def render_html(paper, paper_index, output_path: Path, config, mode="combined"):
    """Render questions, answers, or both; combined mode forces a new answer page."""
    local_mathjax = config.get("mathjax_local_script", "")
    formula_text = "\n".join(
        str(value or "")
        for question in paper
        for value in (
            question.get("text"), *(question.get("options") or []),
            question.get("answer"), question.get("explanation"),
        )
    )
    has_formula = contains_formula(formula_text)
    script_tag = ""
    if local_mathjax:
        script_path = Path(local_mathjax)
        if not script_path.is_absolute():
            script_path = (output_path.parent.parent.parent / script_path).resolve()
        if script_path.is_file():
            script_tag = f'<script defer src="{script_path.as_uri()}"></script>'
        elif has_formula and config.get("mathjax_auto_download", False):
            script_path = ensure_mathjax(
                output_path.parent.parent.parent,
                config.get("work_dir", "work"),
                config.get("mathjax_version", "4.1.3"),
            )
            script_tag = f'<script defer src="{script_path.as_uri()}"></script>'
        elif has_formula:
            raise ValueError(f"mathjax_local_script does not exist: {script_path}")
    elif has_formula and config.get("mathjax_auto_download", False):
        script_path = ensure_mathjax(
            output_path.parent.parent.parent,
            config.get("work_dir", "work"),
            config.get("mathjax_version", "4.1.3"),
        )
        script_tag = f'<script defer src="{script_path.as_uri()}"></script>'
    if not script_tag and config.get("mathjax_allow_network", False) and config.get("mathjax_url"):
        script_tag = f'<script defer src="{html.escape(config["mathjax_url"], quote=True)}"></script>'
    if has_formula and not script_tag:
        raise ValueError(
            "formula input requires mathjax_local_script, mathjax_url, or "
            "mathjax_auto_download=true; the pinned MathJax distribution is cached "
            "under work/_runtime"
        )
    if not script_tag:
        script_tag = '<meta name="math-rendering" content="external-local-resource-required">'
    css = """
@page{size:A4;margin:14mm 15mm}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%;text-size-adjust:100%}
body{margin:0;color:#111;background:#eee;font-family:"Noto Serif CJK SC","SimSun","Microsoft YaHei",serif;font-size:11pt;line-height:1.65}
.paper{width:210mm;min-height:297mm;margin:0 auto 8mm;padding:14mm 15mm;background:#fff}
.question{break-inside:avoid;margin:0 0 6mm}.qhead{font-weight:700}.options{margin:2mm 0 0 7mm}.options div{display:inline-block;min-width:23%;vertical-align:top;padding-right:3mm}
.answers{break-before:page;margin-top:0}.answer{break-inside:avoid;margin:0 0 6mm}.answer h2{font-size:12pt;margin:0 0 2mm}.math{max-width:100%;overflow-x:auto;overflow-y:hidden}
@media screen and (max-width:800px){body{background:#fff;font-size:16px}.paper{width:100%;min-height:0;margin:0;padding:18px 16px}.options{margin-left:12px}.options div{display:block;width:100%;min-width:0;margin-bottom:4px}}
@media print{body{background:#fff}.paper{width:auto;min-height:auto;margin:0;padding:0}}
"""
    parts = [
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">",
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>随机试卷 {paper_index}</title>",
        '<script>window.MathJax={tex:{inlineMath:[["$","$"]],displayMath:[["$$","$$"],["\\\\[","\\\\]"]]},svg:{fontCache:"global"}};</script>',
        script_tag, f'<style>{css}</style></head><body><main class="paper" data-has-formula="{str(has_formula).lower()}">'
    ]
    if mode in ("questions", "combined"):
        for position, q in enumerate(paper, 1):
            parts.append(f'<article class="question"><div class="qhead">{position}. </div><div>{render_text(q.get("text"))}</div>')
            options = q.get("options") or []
            if options:
                parts.append('<div class="options">' + "".join(f"<div>{render_text(option)}</div>" for option in options) + "</div>")
            parts.append("</article>")
    if mode in ("answers", "combined"):
        if mode == "combined":
            parts.append('<section class="answers">')
        else:
            parts.append('<section class="answers standalone-answers">')
        parts.append('<h1>答案与解析</h1>')
        for position, q in enumerate(paper, 1):
            source = q.get("source", {})
            source_text = f"{source.get('set', '来源不明')}｜原题号 {source.get('question', q.get('number', ''))}｜第 {source.get('page', '?')} 页"
            parts.append(
                f'<article class="answer"><h2>第 {position} 题｜来源：{render_text(source_text)}</h2>'
                f'<div><b>答案：</b>{render_text(q.get("answer"))}</div>'
                f'<div><b>解析：</b>{render_text(q.get("explanation"))}</div></article>'
            )
        parts.append("</section>")
    parts.append("</main></body></html>")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("".join(parts), encoding="utf-8")


def question_list(data):
    questions = data.get("questions") if isinstance(data, dict) else data
    if not isinstance(questions, list):
        raise ValueError("question_bank must be an object with a questions array")
    return questions


def validate_question_contract(questions):
    required = {
        "id", "display_id", "number", "type", "subject", "knowledge_points",
        "difficulty", "difficulty_source", "text", "options", "answer",
        "explanation", "source", "match", "assets", "status"
    }
    errors = []
    seen = set()
    for index, q in enumerate(questions, 1):
        missing = sorted(required - set(q))
        if missing:
            errors.append(f"questions[{index}] missing: {', '.join(missing)}")
            continue
        if q["id"] in seen:
            errors.append(f"duplicate id: {q['id']}")
        seen.add(q["id"])
        expected_id = stable_question_id(q)
        if q["id"] != expected_id:
            errors.append(f"questions[{index}] id is not stable: expected {expected_id}, got {q['id']}")
        status = q["status"]
        for field in ("question", "answer", "explanation", "source", "eligible_for_random_paper", "eligibility_reasons"):
            if field not in status:
                errors.append(f"questions[{index}].status missing: {field}")
        if status.get("eligible_for_random_paper"):
            if not all(status.get(k) == "verified" for k in ("question", "answer", "explanation", "source")):
                errors.append(f"questions[{index}] is eligible but not fully verified")
            if not str(q.get("answer", "")).strip() or not str(q.get("explanation", "")).strip():
                errors.append(f"questions[{index}] is eligible but answer/explanation is empty")
            if q.get("match", {}).get("conflict") is True:
                errors.append(f"questions[{index}] is eligible but match.conflict is true")
            if float(q.get("match", {}).get("confidence", 0)) < 0.9:
                errors.append(f"questions[{index}] is eligible but match.confidence is below 0.9")
            blob = "\n".join([str(q.get("text", "")), str(q.get("answer", "")), str(q.get("explanation", ""))])
            if "[unclear]" in blob or "待复核" in blob:
                errors.append(f"questions[{index}] is eligible but contains review markers")
    if errors:
        raise ValueError("question contract failed:\n- " + "\n- ".join(errors))
    for question in questions:
        schema_validate(question, "question.schema.json")


def eligible_questions(questions):
    return [q for q in questions if q["status"].get("eligible_for_random_paper") is True]


def distribution(questions):
    result = defaultdict(Counter)
    for q in questions:
        result[q["type"]][str(q["difficulty"])] += 1
    return {typ: dict(counter) for typ, counter in result.items()}


def allocate_targets(pool, count):
    if not pool:
        return {}
    frequencies = Counter(int(q["difficulty"]) for q in pool)
    total = sum(frequencies.values())
    raw = {d: count * frequencies.get(d, 0) / total for d in frequencies}
    targets = {d: int(v) for d, v in raw.items()}
    remainder = count - sum(targets.values())
    for d, _ in sorted(raw.items(), key=lambda item: item[1] - targets[item[0]], reverse=True)[:remainder]:
        targets[d] += 1
    return targets


def compose(questions, config):
    validate_question_contract(questions)
    requirements = config["requirements"]
    by_type = requirements["by_type"]
    declared_total = int(requirements.get("questions", sum(by_type.values())))
    if declared_total != sum(int(value) for value in by_type.values()):
        raise ValueError("requirements.questions must equal the sum of requirements.by_type")
    paper_count = int(config["paper_count"])
    seed = int(config["random_seed"])
    eligible = eligible_questions(questions)
    if not eligible:
        raise ValueError("no eligible questions")
    pools = {typ: [q for q in eligible if q["type"] == typ] for typ in by_type}
    for typ, count in by_type.items():
        if len(pools[typ]) < count:
            raise ValueError(f"eligible pool too small for {typ}: need {count}, have {len(pools[typ])}")
    targets = {typ: allocate_targets(pools[typ], count) for typ, count in by_type.items()}
    rng = random.Random(seed)
    used_across = Counter()
    used_ids = set()
    papers = []
    selection_orders = []
    max_repeat = int(requirements.get("max_source_repetition", 3))
    min_subject_coverage = requirements.get("min_subject_coverage", {}) or {}
    allow_reuse = bool(requirements.get("allow_cross_paper_reuse", False))
    capacity = {}
    for typ, count in by_type.items():
        available = len(pools[typ])
        required_total = count if allow_reuse else count * paper_count
        capacity[typ] = {"eligible": available, "required": required_total, "shortage": max(0, required_total - available)}
        if available < required_total:
            raise ValueError(f"capacity insufficient for {typ}: eligible={available}, required={required_total}, reuse={allow_reuse}")
    for paper_index in range(paper_count):
        selected = []
        order = []
        local_ids = set()
        for typ, count in by_type.items():
            pool = [q for q in pools[typ] if q["id"] not in local_ids and (allow_reuse or q["id"] not in used_ids)]
            if len(pool) < count:
                raise ValueError(f"paper {paper_index + 1}: insufficient unused candidates for {typ}")
            rng.shuffle(pool)
            local_diff = Counter()
            local_source = Counter()
            for _ in range(count):
                def score(q):
                    target = targets[typ]
                    diff_penalty = abs((local_diff[q["difficulty"]] + 1) - target.get(q["difficulty"], 0))
                    source = q["source"]["set"]
                    source_penalty = max(0, local_source[source] - max_repeat + 1) * 100
                    global_penalty = used_across[q["id"]] * 20
                    return diff_penalty * 5 + source_penalty + global_penalty + rng.random()
                q = min(pool, key=score)
                source = q["source"]["set"]
                if local_source[source] >= max_repeat:
                    alternatives = [candidate for candidate in pool if local_source[candidate["source"]["set"]] < max_repeat]
                    if alternatives:
                        q = min(alternatives, key=score)
                        source = q["source"]["set"]
                pool.remove(q)
                selected.append(q)
                order.append(q["id"])
                local_ids.add(q["id"])
                local_diff[q["difficulty"]] += 1
                local_source[source] += 1
        papers.append(selected)
        selection_orders.append(order)
        if not allow_reuse:
            used_ids.update(local_ids)
        used_across.update(local_ids)
    pool_snapshot = [{
        "id": q["id"], "type": q["type"], "subject": q["subject"],
        "difficulty": q["difficulty"], "knowledge_points": q["knowledge_points"],
        "source_set": q["source"]["set"],
    } for q in sorted(eligible, key=lambda x: x["id"])]
    pool_hash = sha256_bytes(canonical_json(pool_snapshot).encode())
    actual = {str(i + 1): distribution(paper) for i, paper in enumerate(papers)}
    deviation = {}
    issues = []
    for paper_number, paper in enumerate(papers, 1):
        deviation[str(paper_number)] = {}
        for typ, target in targets.items():
            actual_counts = Counter(int(q["difficulty"]) for q in paper if q["type"] == typ)
            deviation[str(paper_number)][typ] = {
                str(difficulty): {
                    "target": target.get(difficulty, 0),
                    "actual": actual_counts.get(difficulty, 0),
                    "deviation": actual_counts.get(difficulty, 0) - target.get(difficulty, 0)
                }
                for difficulty in sorted(set(target) | set(actual_counts))
            }
        source_counts = Counter(q["source"]["set"] for q in paper)
        over = {source: count for source, count in source_counts.items() if count > max_repeat}
        if over:
            issues.append({"level": "ERROR", "message": f"paper {paper_number} source repetition exceeds limit: {over}"})
        for subject, minimum in min_subject_coverage.items():
            actual_subject = sum(1 for q in paper if q.get("subject") == subject)
            if actual_subject < int(minimum):
                issues.append({"level": "ERROR", "message": f"paper {paper_number} subject coverage for {subject} is {actual_subject}, need {minimum}"})
    max_diff_deviation = int(requirements.get("max_difficulty_deviation", 1))
    for paper_number, by_subject in deviation.items():
        for typ, values in by_subject.items():
            for difficulty, item in values.items():
                if abs(item["deviation"]) > max_diff_deviation:
                    issues.append({"level": "WARNING", "message": f"paper {paper_number} {typ} difficulty {difficulty} deviation={item['deviation']}"})
    status = "failed" if any(item["level"] == "ERROR" for item in issues) else ("warning" if issues else "passed")
    audit = {
        "run_id": config.get("run_id", "unassigned"),
        "status": status,
        "counts": {"raw_all": len(questions), "eligible": len(eligible), "papers": paper_count, "capacity": capacity},
        "difficulty_distribution": {
            "raw_all": distribution(questions),
            "eligible_pool": distribution(eligible),
            "paper_target": targets,
            "paper_actual": actual,
            "deviation": deviation
        },
        "randomization": {
            "random_seed": seed,
            "selection_algorithm": "weighted_constrained_sampling_v1",
            "constraints": requirements,
            "candidate_pool_hash": pool_hash,
            "fallback_used": False,
            "selection_order": selection_orders,
            "fallback_path": []
        },
        "issues": issues
    }
    schema_validate(audit, "audit.schema.json")
    return papers, audit


def make_manifest(project_root: Path, config, run_id: str, question_bank: Path):
    input_dir = (project_root / config.get("input_dir", "input")).resolve()
    files = []
    if input_dir.exists():
        question_bank_rel = str(question_bank.relative_to(project_root)).replace("\\", "/")
        for path in sorted(p for p in input_dir.rglob("*") if p.is_file()):
            relative = str(path.relative_to(project_root)).replace("\\", "/")
            role = infer_file_role(path)
            included = relative == question_bank_rel or role in {"试卷", "答案", "解析", "题库"}
            pages = pdf_page_count(path) if path.suffix.lower() == ".pdf" else None
            pages_status = "verified" if path.suffix.lower() == ".pdf" and pages is not None else ("tool_unavailable" if path.suffix.lower() == ".pdf" else "not_applicable")
            files.append({
                "path": relative,
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
                "type": path.suffix.lower().lstrip(".") or "unknown",
                "pages": pages,
                "pages_status": pages_status,
                "role": role,
                "included": included,
                "reason": "recognized input role" if included else "not selected by input role"
            })
    return {
        "run_id": run_id,
        "project_root": str(project_root),
        "input_files": files,
        "script_version": SCRIPT_VERSION,
        "question_signature_version": QUESTION_SIGNATURE_VERSION,
        "prompt_version": config.get("prompt_version", "unknown"),
        "prompt_hash": config.get("prompt_hash", "unknown"),
        "models": config.get("models", []),
        "runtime": config.get("runtime", {}),
        "config": config,
        "status": "running"
    }


def run(config_path: Path):
    config = load_json(config_path)
    schema_validate(config, "config.schema.json")
    configured_root = Path(config.get("project_root", "."))
    project_root = (config_path.parent / configured_root).resolve() if not configured_root.is_absolute() else configured_root.resolve()
    run_id = config.get("run_id") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    config["run_id"] = run_id
    work = project_root / config.get("work_dir", "work") / run_id
    output = project_root / config.get("output_dir", "output") / run_id
    archive = project_root / config.get("archive_dir", "archive") / run_id
    input_dir = (project_root / config.get("input_dir", "input")).resolve()
    for name, directory in (
        ("work_dir", work.parent),
        ("output_dir", output.parent),
        ("archive_dir", archive.parent),
    ):
        resolved = directory.resolve()
        if resolved == input_dir or resolved in input_dir.parents or input_dir in resolved.parents:
            raise ValueError(f"input_dir overlaps {name}: {input_dir} and {resolved}")
    existing = [path for path in (work, output, archive) if path.exists()]
    if existing:
        joined = ", ".join(str(path) for path in existing)
        raise ValueError(f"run_id already exists; refusing to overwrite: {joined}")
    work.mkdir(parents=True, exist_ok=False)
    output.mkdir(parents=True, exist_ok=False)
    archive.mkdir(parents=True, exist_ok=False)
    question_bank = (project_root / config["question_bank"]).resolve()
    manifest = make_manifest(project_root, config, run_id, question_bank)
    schema_validate(manifest, "manifest.schema.json")
    if any(item.get("pages_status") == "tool_unavailable" for item in manifest["input_files"]):
        manifest.setdefault("warnings", []).append("PDF page count unavailable for one or more inputs")
    save_json(work / "manifest.json", manifest)
    try:
        bank_data = load_json(question_bank)
        schema_validate(bank_data, "question_bank.schema.json")
        questions = question_list(bank_data)
        validate_question_contract(questions)
        papers, audit = compose(questions, config)
        if audit.get("status") == "failed":
            raise ValueError("composition audit failed; final output was blocked")
        save_json(output / "question_bank.normalized.json", {"run_id": run_id, "questions": questions})
        save_json(output / "papers.json", {
            "run_id": run_id,
            "papers": [{"paper": index + 1, "questions": paper} for index, paper in enumerate(papers)]
        })
        with (output / "paper_manifest.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["paper", "position", "id", "type", "subject", "difficulty", "knowledge_points", "source_set", "source_question"])
            writer.writeheader()
            for paper_index, paper in enumerate(papers, 1):
                for position, q in enumerate(paper, 1):
                    writer.writerow({
                        "paper": paper_index,
                        "position": position,
                        "id": q["id"],
                        "type": q["type"],
                        "subject": q["subject"],
                        "difficulty": q["difficulty"],
                        "knowledge_points": "；".join(q["knowledge_points"]),
                        "source_set": q["source"]["set"],
                        "source_question": q["source"]["question"]
                    })
        for index, paper in enumerate(papers, 1):
            render_html(paper, index, output / f"paper_{index:02d}.html", config, "combined")
            render_html(paper, index, output / f"paper_{index:02d}.questions.html", config, "questions")
            render_html(paper, index, output / f"paper_{index:02d}.answers.html", config, "answers")
        save_json(output / "audit.json", audit)
        manifest["status"] = "succeeded"
        save_json(archive / "manifest.json", manifest)
        save_json(archive / "audit.json", audit)
        print(json.dumps({"run_id": run_id, "papers": len(papers), "output": str(output)}, ensure_ascii=False, indent=2))
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["failure"] = {"level": "ERROR", "message": str(exc)}
        save_json(archive / "manifest.failed.json", manifest)
        save_json(archive / "audit.failed.json", {"run_id": run_id, "status": "failed", "counts": {}, "difficulty_distribution": {"raw_all": {}, "eligible_pool": {}, "paper_target": {}, "paper_actual": {}, "deviation": {}}, "randomization": {"random_seed": config.get("random_seed", 0), "selection_algorithm": "not_run", "constraints": config.get("requirements", {}), "candidate_pool_hash": "", "fallback_used": False}, "issues": [{"level": "ERROR", "message": str(exc)}]})
        raise


def main():
    parser = argparse.ArgumentParser(description="Canonical v1.0 question-bank pipeline")
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    try:
        run(args.config.resolve())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
