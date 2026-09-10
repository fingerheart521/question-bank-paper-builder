# Question Bank Paper Builder

一个用于整理题库并自动生成随机试卷的 Codex Skill。

## 它是做什么的

它可以把试卷、答案、解析、讲义、扫描件等资料整理成结构化题库，并根据题型、知识点、难度和来源生成一套或多套新试卷。

主要能力：

- 结构化整理题目、选项、答案、解析和来源；
- 生成稳定题目 ID、匹配证据和审计记录；
- 按题型、难度、知识点和来源约束随机组卷；
- 生成题目版、答案解析版和合并版 HTML；
- 使用本地 MathJax 渲染公式；
- 通过 Chromium 导出并验证 A4 PDF。

## 仓库

https://github.com/fingerheart521/question-bank-paper-builder

## 基本使用

准备项目目录，并参考 `assets/config.example.json` 创建配置：

```text
project/
├── input/question_bank.json
├── config.json
├── work/
├── output/
└── archive/
```

运行题库整理和组卷：

```bash
python scripts/qbank_pipeline.py --config project/config.json
```

校验题库或配置：

```bash
python scripts/validate_contract.py question_bank project/input/question_bank.json
python scripts/validate_contract.py config project/config.json
```

导出 PDF：

```bash
python scripts/export_pdf.py --config project/config.json --html-dir project/output/<run_id> --output-dir project/output/<run_id>/pdf
```

## 依赖

Python 依赖见 `requirements.txt`。PDF 导出还需要 Chromium、Chrome 或 Edge，以及 `pdfinfo`、`pdftotext` 和 `pdftoppm`。

含公式时默认使用自动下载并校验的 MathJax 本地缓存。若使用公网 MathJax 地址，必须显式设置 `mathjax_allow_network: true`。

## 验证

```bash
python -m unittest discover -s tests -v
```

当前版本：`1.0`

## 多科目资料

如果输入文件夹包含多个普通科目，应先按科目分别整理和配置，避免不同科目混入同一套试卷。Skill 会提醒 AI 采用“一科目一项目、一项目一套配置”的方式，但不会自动替用户拆分资料。

“考研 408”是一个综合考试项目，数据结构、计算机组成原理、操作系统和计算机网络四门内容是在同一张试卷中统一考试的四个内容域，不应拆成四个项目或四张试卷。处理 408 资料时，应在同一项目中保持四个内容域的区分，并按需要设置各内容域的数量或覆盖要求。
