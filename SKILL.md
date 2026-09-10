---
name: question-bank-paper-builder
description: Build traceable, randomized exam papers from mixed question-bank materials with structured answers, explanations, knowledge points, difficulty balancing, offline formula rendering, and audited HTML/PDF output.
metadata:
  short-description: Organize question banks and generate audited random papers
  version: "1.0"
---

# Question Bank Paper Builder

Current revision: `v1.0`. Detailed contracts, runtime pins, browser parameters,
acceptance evidence, and revision history are maintained in `references/specification.md`.

Use this skill when the user provides exam papers, answer books, explanations, lecture notes, scans, or mixed question-bank files and wants a cleaned question bank plus newly composed papers. It applies across mathematics, English, 408, and similar subjects.

When one input folder contains multiple subjects, tell the user to organize and configure each subject independently before processing. Do not automatically combine different subjects into one paper. Treat the Chinese exam label “考研 408” as one integrated examination project whose content domains are 数据结构、计算机组成原理、操作系统、计算机网络; keep these domains distinct in `subject` or `knowledge_points` and balance them according to the user’s paper requirements.

## Outcome

Produce structured question records and one or more new papers. Preserve the source meaning, options, formulas, tables, meaningful line breaks, answers, explanations, knowledge points, difficulty evidence, and traceable source data. Re-typeset content as text/LaTeX whenever possible; retain an image only when a figure or table cannot be represented reliably.

## Non-negotiable rules

- Do not concatenate original PDF pages as the generated paper.
- Do not guess unreadable characters or formulas. Reinspect the source or retry recognition; otherwise mark the record for review and exclude it from default composition.
- Keep question pages free of source, answer, explanation, and processing notes.
- Start the answer/explanation section on a new page. Put source, answer, and explanation together in the answer record, not in a separate source chapter.
- Balance type, knowledge points, difficulty, source repetition, and cross-paper reuse. Preserve the eligible-pool simple/medium/hard distribution as closely as capacity allows and record deviations.
- A record is eligible only when question, answer, explanation, source, match confidence, and rendering status are verified. Empty answer/explanation, `match.conflict=true`, confidence below `0.9`, `[unclear]`, or review markers disqualify it.
- Hard audit failures stop final delivery. Never present failed composition or failed PDF validation as a successful run.

## Workflow

1. Treat each subject directory as an independent project with `input/`, `work/`, `output/`, and `archive/`.
2. Register inputs, hashes, page-count status, roles, models, prompts, runtime, and configuration in a manifest.
3. Extract native text first. Use visual recognition only for scans, broken formulas, tables, figures, or missing content; record retries and unresolved fields.
4. Normalize records, generate and verify the stable question ID, deduplicate conservatively, match answers/explanations with evidence, and validate the question-bank contract.
5. Compose papers using a fixed seed and multi-dimensional constraints. Save candidate-pool hash, targets, actual distribution, deviations, selection order, and fallback information.
6. Render separate question-only and answer/explanation HTML plus a combined HTML. A pinned MathJax distribution is downloaded on first formula use into `work/_runtime` and reused by hash; an existing local distribution may be configured instead.
7. Export PDF through a headless Chromium-compatible browser. Validate dimensions, pagination, text, formula nodes, raw LaTeX, answer/source structure, blank pages, and clipping signals; retain key or suspicious pages for AI visual sampling. Any hard failure writes audit evidence and blocks the corresponding PDF.
8. Review the final audit and keep only reproducible archive evidence; do not read legacy output directories as new input.

## Entrypoints

- `scripts/qbank_pipeline.py --config <project>/config.json`: validate, filter, compose, audit, and render HTML.
- `scripts/validate_contract.py <question|question_bank|config|manifest|audit> <file>`: validate one contract.
- `scripts/export_pdf.py --config <project>/config.json --html-dir <project>/output/<run_id> --output-dir <project>/output/<run_id>/pdf [--chromium <path>]`: export and validate PDFs; browser auto-discovery is used when `--chromium` is omitted.

Both validation entrypoints share the same Schema-based fallback when `jsonschema` is unavailable. Read [references/specification.md](references/specification.md) for the full contract, runtime requirements, browser parameters, and acceptance rules. A PDF succeeds only when the process exits successfully and `pdf_audit.json.status` is `passed`.

## Delivery boundary

Do not modify a user's existing generated papers unless explicitly requested. This skill creates a new run under the configured project and refuses to overwrite an existing `run_id`, preventing stale files from being mistaken for current output.
