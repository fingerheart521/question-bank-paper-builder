---
name: question-bank-paper-builder
description: 从混合题库资料中整理可追溯的随机试卷，结构化保存答案、解析和知识点，平衡难度，离线渲染公式，并输出经过审计的 HTML/PDF 文件。
metadata:
  short-description: 整理题库并生成经过审计的随机试卷
  version: "1.0"
---

# 题库试卷生成器

当前版本：`v1.0`。详细契约、运行时版本、浏览器参数、验收证据和版本记录见
`references/specification.md`。

当用户提供试卷、答案册、解析、讲义、扫描件或混合题库文件，并希望整理题库、重新组卷时使用本 Skill。适用于数学、英语、408 及其他类似科目。

When one input folder contains multiple ordinary subjects, tell the user to organize and configure each subject independently before processing. Do not automatically combine different subjects into one paper. Treat “考研 408” as one integrated examination project: 数据结构、计算机组成原理、操作系统、计算机网络 are four content domains within the same subject and the same paper, not four separate projects. Keep the domains distinct in `subject` or `knowledge_points` and balance them according to the user’s paper requirements.

## 目标

生成结构化题目记录和一套或多套新试卷。保留原文含义、选项、公式、表格、有意义的换行、答案、解析、知识点、难度依据和可追溯来源。尽可能使用文字或 LaTeX 重新排版；只有图形或表格无法可靠表示时才保留图片。

## 组卷模板优先级

组卷前，如果目标试卷结构不明确，应请求用户提供官方真题模板，并按以下顺序确定结构：

1. 使用用户明确提供的模板。
2. 在用户提供的文件中查找官方真题，或能够代表目标考试格式的仿真模拟试卷。
3. 如果前两者都没有，根据题库中已有试卷推断结构，并记录该结构为推断结果。
4. 只有在本地资料不足以确定结构时，才联网查询可靠、最新的考试信息。记录来源、查询日期和不确定项，不得虚构缺失的规则或比例。

对于考研 408 等综合考试，模板描述的是一张合并试卷，不得将四个内容域拆成不同试卷。

## 不可违反的规则

- 不得直接拼接原始 PDF 页面作为生成试卷。
- 不得猜测无法辨认的字符或公式。应重新检查原文或重试识别；仍无法确认时标记为待复核，并排除出默认组卷。
- 题目页面不得包含来源、答案、解析或处理说明。
- 答案与解析部分必须从新页开始。来源、答案和解析应放在同一条答案记录中，不得单独设置来源章节。
- 应平衡题型、知识点、难度、来源重复和跨试卷复用；在容量允许时尽量保持合格题池的简单、中等、困难比例，并记录偏差。
- 只有题目、答案、解析、来源、匹配置信度和渲染状态均已验证的记录才具备组卷资格。答案或解析为空、`match.conflict=true`、置信度低于 `0.9`、包含 `[unclear]` 或待复核标记的记录不得入选。
- 硬性审计失败时必须停止交付，不得把组卷失败或 PDF 验证失败标记为成功。

## 工作流程

1. 将每个科目目录作为独立项目，包含 `input/`、`work/`、`output/` 和 `archive/`。
2. 在清单中登记输入文件、哈希、页数状态、角色、模型、提示词、运行时和配置。
3. 优先提取原生文字。仅在扫描件、公式损坏、表格、图形或内容缺失时使用视觉识别，并记录重试和未解决字段。
4. 规范化记录，生成并验证稳定题目 ID，保守去重，依据证据匹配答案和解析，并校验题库契约。
5. 使用固定随机种子和多维约束组卷，保存候选池哈希、目标分布、实际分布、偏差、选择顺序和降级信息。
6. 生成只含题目、只含答案解析以及合并版 HTML。首次遇到公式时下载固定版本的 MathJax 到 `work/_runtime`，并按哈希复用；也可以配置现有本地资源。
7. 通过无界面的 Chromium 兼容浏览器导出 PDF，验证尺寸、分页、文字、公式节点、裸 LaTeX、答案来源结构、空白页和截断信号；保留关键页或疑似异常页供 AI 视觉抽查。任何硬性失败都要写入审计证据并阻止对应 PDF 交付。
8. 检查最终审计结果，只保留可复现的归档证据；不得把旧输出目录作为新输入读取。

## 入口

- `scripts/qbank_pipeline.py --config <project>/config.json`：校验、筛选、组卷、审计并生成 HTML。
- `scripts/validate_contract.py <question|question_bank|config|manifest|audit> <file>`：校验一个契约文件。
- `scripts/export_pdf.py --config <project>/config.json --html-dir <project>/output/<run_id> --output-dir <project>/output/<run_id>/pdf [--chromium <path>]`：导出并校验 PDF；省略 `--chromium` 时自动查找浏览器。

两个校验入口在没有 `jsonschema` 时共用基于 Schema 的备用校验逻辑。完整契约、运行时要求、浏览器参数和验收规则见 [references/specification.md](references/specification.md)。只有进程成功退出且 `pdf_audit.json.status` 为 `passed` 时，PDF 才算成功。

## 交付边界

除非用户明确要求，不得修改已有生成试卷。本 Skill 会在配置项目下创建新的运行目录，并拒绝覆盖已有 `run_id`，避免旧文件被误认为当前输出。
