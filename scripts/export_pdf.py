"""Parameterised, header/footer-free A4 HTML to PDF exporter."""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import time
import urllib.request
import re
import tempfile
import shutil
import socket
from html.parser import HTMLParser
from pathlib import Path

try:
    from formula_detection import contains_formula
except ImportError:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from formula_detection import contains_formula

def discover_browsers(configured=None):
    """Find all usable Chromium-compatible browsers without starting them."""
    candidates = []
    if configured:
        candidates.append(Path(configured))
    program_files = [value for value in (
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("LOCALAPPDATA"),
    ) if value]
    for base in program_files:
        root = Path(base)
        candidates.extend([
            root / "Google/Chrome/Application/chrome.exe",
            root / "Microsoft/Edge/Application/msedge.exe",
        ])
    candidates.extend([
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path.home() / "AppData/Local/Google/Chrome/Application/chrome.exe",
        Path.home() / "AppData/Local/Microsoft/Edge/Application/msedge.exe",
        Path("/usr/bin/google-chrome"), Path("/usr/bin/chromium"), Path("/usr/bin/chromium-browser"),
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    ])
    found = []
    seen = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        key = str(resolved).lower()
        if key not in seen and resolved.is_file():
            seen.add(key)
            found.append(resolved)
    for name in ("google-chrome", "chrome", "chromium", "chromium-browser", "msedge"):
        executable = shutil.which(name)
        if executable:
            resolved = Path(executable).resolve()
            key = str(resolved).lower()
            if key not in seen:
                seen.add(key)
                found.append(resolved)
    return found


def discover_browser(configured=None):
    """Find the first Chromium-compatible browser without starting it."""
    browsers = discover_browsers(configured)
    if browsers:
        return browsers[0]
    raise RuntimeError("No Chromium-compatible browser found; configure pdf.chromium or --chromium")


def runtime_dependency_errors():
    """Return actionable errors before starting a headless browser session."""
    errors = []
    try:
        import websocket  # noqa: F401
    except ImportError as exc:
        errors.append(f"Python package websocket-client is unavailable: {exc}")
    try:
        from PIL import Image  # noqa: F401
    except ImportError as exc:
        errors.append(f"Python package Pillow is unavailable: {exc}")
    for command in ("pdfinfo", "pdftotext", "pdftoppm"):
        if not shutil.which(command):
            errors.append(f"required command is unavailable: {command}")
    return errors


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def rpc(ws, counter, method, params=None):
    counter[0] += 1
    ident = counter[0]
    ws.send(json.dumps({"id": ident, "method": method, "params": params or {}}))
    while True:
        message = json.loads(ws.recv())
        if message.get("id") == ident:
            if "error" in message:
                raise RuntimeError(message["error"])
            return message


def html_expectations(html_path: Path):
    source = html_path.read_text(encoding="utf-8")
    return {
        "mode": "answers" if ".answers." in html_path.name else ("questions" if ".questions." in html_path.name else "combined"),
        "questions": len(re.findall(r'<article class=[\"\']question[\"\']>', source)),
        "answers": len(re.findall(r'<article class=[\"\']answer[\"\']>', source)),
    }


class _VisibleTextParser(HTMLParser):
    IGNORED_TAGS = {"head", "script", "style", "template", "noscript"}

    def __init__(self):
        super().__init__()
        self.ignored_depth = 0
        self.parts = []
        self.explicit_formula = None

    def handle_starttag(self, tag, attrs):
        if tag in self.IGNORED_TAGS:
            self.ignored_depth += 1
        attributes = dict(attrs)
        if not self.ignored_depth and "data-has-formula" in attributes:
            value = str(attributes["data-has-formula"]).lower()
            if value in {"true", "false"}:
                self.explicit_formula = value == "true"

    def handle_endtag(self, tag):
        if tag in self.IGNORED_TAGS and self.ignored_depth:
            self.ignored_depth -= 1

    def handle_data(self, data):
        if not self.ignored_depth:
            self.parts.append(data)


def html_has_formula(html_path: Path):
    """Detect formulas from an explicit marker or visible HTML text only."""
    parser = _VisibleTextParser()
    parser.feed(html_path.read_text(encoding="utf-8"))
    if parser.explicit_formula is not None:
        return parser.explicit_formula
    visible_text = "\n".join(parser.parts)
    return contains_formula(visible_text)


def _export_one_attempt(chrome, html_path, pdf_path, port, profile, pdf_config=None, audit_path=None):
    try:
        import websocket
    except ImportError as exc:
        raise RuntimeError(f"Python package websocket-client is unavailable: {exc}") from exc
    pdf_config = pdf_config or {}
    process = subprocess.Popen([
        str(chrome), "--headless=new", "--disable-gpu", "--no-sandbox",
        f"--user-data-dir={profile}", f"--remote-debugging-port={port}",
        "--remote-allow-origins=*", "--no-first-run", "--no-default-browser-check", "about:blank"
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    browser = page = None
    try:
        version = None
        for _ in range(80):
            try:
                version = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version"))
                break
            except Exception:
                time.sleep(0.25)
        if not version:
            raise RuntimeError("Chromium DevTools endpoint did not start")
        browser = websocket.create_connection(version["webSocketDebuggerUrl"])
        rpc(browser, [0], "Target.createTarget", {"url": "about:blank"})
        tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list"))
        tab = [item for item in tabs if item.get("type") == "page"][-1]
        page = websocket.create_connection(tab["webSocketDebuggerUrl"])
        counter = [0]
        rpc(page, counter, "Page.enable")
        rpc(page, counter, "Runtime.enable")
        rpc(page, counter, "Page.navigate", {"url": html_path.resolve().as_uri()})
        formula_expected = html_has_formula(html_path)
        value = {}
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            checks = rpc(page, counter, "Runtime.evaluate", {
                "expression": "(() => ({mjx: document.querySelectorAll('mjx-container').length, katex: document.querySelectorAll('.katex').length, source: document.body.innerText, mathConfig: document.querySelector('meta[name=math-rendering]')?.content || '', ready: document.readyState, fonts: document.fonts ? document.fonts.status : 'loaded'}))()",
                "returnByValue": True
            })
            value = checks.get("result", {}).get("result", {}).get("value", {})
            nodes = int(value.get("mjx", 0)) + int(value.get("katex", 0)) if isinstance(value, dict) else 0
            ready = isinstance(value, dict) and value.get("ready") == "complete" and value.get("fonts") == "loaded"
            if ready and (not formula_expected or nodes > 0):
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("HTML/MathJax rendering timed out after 12 seconds")
        source = value.get("source", "") if isinstance(value, dict) else ""
        if isinstance(value, dict) and value.get("mathConfig") == "external-local-resource-required" and ("$" in source or "\\frac" in source or "\\begin{" in source):
            raise RuntimeError("formula rendering check failed: configure an existing local mathjax_local_script")
        if (value.get("mjx", 0) + value.get("katex", 0)) == 0 and ("$" in source or "\\frac" in source or "\\begin{" in source):
            raise RuntimeError("formula rendering check failed: no MathJax/KaTeX nodes")
        if "\\frac" in source or "\\begin{" in source:
            raise RuntimeError("formula rendering check failed: raw LaTeX remains")
        result = rpc(page, counter, "Page.printToPDF", {
            "paperWidth": float(pdf_config.get("paper_width", 8.27)),
            "paperHeight": float(pdf_config.get("paper_height", 11.7)),
            "marginTop": float(pdf_config.get("margin_top", 0)),
            "marginBottom": float(pdf_config.get("margin_bottom", 0)),
            "marginLeft": float(pdf_config.get("margin_left", 0)),
            "marginRight": float(pdf_config.get("margin_right", 0)),
            "displayHeaderFooter": False, "printBackground": True,
            "preferCSSPageSize": False
        })
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(base64.b64decode(result["result"]["data"]))
        pdf_checks = validate_pdf_artifact(
            pdf_path,
            html_path.name,
            expected_width=float(pdf_config.get("paper_width", 8.27)),
            expected_height=float(pdf_config.get("paper_height", 11.7)),
            expected=html_expectations(html_path),
            pdf_config=pdf_config,
        )
        return {
            "file": pdf_path.name,
            "status": "passed",
            "formula_nodes": int(value.get("mjx", 0)) + int(value.get("katex", 0)),
            "paper_width": float(pdf_config.get("paper_width", 8.27)),
            "paper_height": float(pdf_config.get("paper_height", 11.7)),
            **pdf_checks,
            "issues": []
        }
    except Exception as exc:
        raise
    finally:
        for connection in (page, browser):
            try:
                if connection:
                    connection.close()
            except Exception:
                pass
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def export_one(chrome, html_path, pdf_path, port, profile, pdf_config=None, audit_path=None, max_attempts=3):
    """Export one HTML file with fresh-port retries for browser startup races."""
    last_error = None
    for attempt in range(1, max_attempts + 1):
        current_port = port if attempt == 1 and port else free_port()
        attempt_profile = profile.parent / f"{profile.name}-{attempt}"
        try:
            return _export_one_attempt(
                chrome,
                html_path,
                pdf_path,
                current_port,
                attempt_profile,
                pdf_config,
                audit_path,
            )
        except Exception as exc:
            last_error = exc
            try:
                if pdf_path.exists():
                    pdf_path.unlink()
            except OSError:
                pass
            if attempt < max_attempts:
                time.sleep(0.25 * attempt)
    raise RuntimeError(f"Chromium export failed after {max_attempts} attempts: {last_error}") from last_error


def _extract_pdf_text(pdf_path: Path):
    result = subprocess.run(["pdftotext", "-layout", str(pdf_path), "-"], capture_output=True, text=False, check=False)
    if result.returncode != 0:
        raise RuntimeError("PDF validation failed: pdftotext is unavailable or rejected the file")
    text = result.stdout.decode("utf-8", errors="replace") if result.stdout is not None else ""
    pages = text.split("\f")
    while pages and not pages[-1].strip():
        pages.pop()
    return text, pages


def _visual_check_pdf(pdf_path: Path, pages, review_dir=None, review_stem=None):
    tool = shutil.which("pdftoppm")
    if not tool:
        raise RuntimeError("PDF validation failed: pdftoppm is required for visual page checks")
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(f"PDF validation failed: Pillow is required for visual page checks: {exc}") from exc
    selected = sorted({1, pages.index(next(page for page in pages if "答案与解析" in page)) + 1 if any("答案与解析" in page for page in pages) else 1, len(pages)})
    with tempfile.TemporaryDirectory(prefix="qbank-pdf-check-") as folder:
        prefix = str(Path(folder) / "page")
        result = subprocess.run([tool, "-r", "96", "-png", str(pdf_path), prefix], capture_output=True, text=False, check=False)
        if result.returncode != 0:
            raise RuntimeError("PDF validation failed: visual render command failed")
        image_paths = sorted(Path(folder).glob("page-*.png"), key=lambda path: int(path.stem.rsplit("-", 1)[1]))
        if len(image_paths) != len(pages):
            raise RuntimeError(f"PDF validation failed: visual render produced {len(image_paths)} pages, expected {len(pages)}")
        scan = []
        for number, image_path in enumerate(image_paths, 1):
            if image_path.stat().st_size < 1000:
                raise RuntimeError(f"PDF validation failed: visual render check failed for page {number}")
            with Image.open(image_path).convert("L") as image:
                width, height = image.size
                dark = image.point(lambda pixel: 255 if pixel < 245 else 0)
                bbox = dark.getbbox()
                pixels = dark.load()
                dark_pixels = sum(1 for y in range(height) for x in range(width) if pixels[x, y])
                edge = 0
                edge_width = min(8, width // 20, height // 20)
                if edge_width:
                    for x in range(width):
                        for y in list(range(edge_width)) + list(range(height - edge_width, height)):
                            edge += dark.getpixel((x, y)) > 0
                    for y in range(edge_width, height - edge_width):
                        for x in list(range(edge_width)) + list(range(width - edge_width, width)):
                            edge += dark.getpixel((x, y)) > 0
                edge_ratio = edge / max(dark_pixels, 1)
                if dark_pixels < 20:
                    raise RuntimeError(f"PDF validation failed: probable blank page {number}")
                if bbox and (bbox[0] <= 0 or bbox[1] <= 0 or bbox[2] >= width or bbox[3] >= height) and edge_ratio > 0.02:
                    raise RuntimeError(f"PDF validation failed: probable clipped content on page {number}")
                scan.append({"page": number, "width": width, "height": height, "dark_pixels": dark_pixels, "edge_dark_ratio": round(edge_ratio, 6), "suspect": edge_ratio > 0.01})
        suspect = [item["page"] for item in scan if item["suspect"]]
        ai_review_pages = sorted(set(selected + suspect))
        review_path = None
        if review_dir:
            review_path = Path(review_dir) / (review_stem or pdf_path.stem)
            review_path.mkdir(parents=True, exist_ok=True)
            for page_number in ai_review_pages:
                source = image_paths[page_number - 1]
                shutil.copy2(source, review_path / f"page-{page_number:04d}.png")
        return {"pages": selected, "scan": scan, "ai_review_pages": ai_review_pages, "review_dir": str(review_path) if review_path else ""}


def validate_pdf_artifact(pdf_path: Path, html_name: str, expected_width=8.27, expected_height=11.7, expected=None, pdf_config=None):
    """Run independent PDF checks after Chromium has produced the file."""
    if not pdf_path.exists() or pdf_path.stat().st_size < 100:
        raise RuntimeError("PDF validation failed: output is missing or empty")
    info = subprocess.run(["pdfinfo", str(pdf_path)], capture_output=True, text=False, check=False)
    if info.returncode != 0:
        raise RuntimeError("PDF validation failed: pdfinfo is unavailable or rejected the file")
    page_size = None
    pages = None
    info_text = (info.stdout or b"").decode("utf-8", errors="replace")
    for line in info_text.splitlines():
        if line.startswith("Page size:"):
            page_size = line.split(":", 1)[1].strip()
        elif line.startswith("Pages:"):
            try:
                pages = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
    if not page_size or not pages:
        raise RuntimeError("PDF validation failed: page size or page count missing")
    match = re.search(r"([0-9.]+) x ([0-9.]+) pts", page_size)
    if not match:
        raise RuntimeError(f"PDF validation failed: unrecognised page size {page_size}")
    width, height = map(float, match.groups())
    expected_width_pt = expected_width * 72
    expected_height_pt = expected_height * 72
    if abs(width - expected_width_pt) > 3 or abs(height - expected_height_pt) > 3:
        raise RuntimeError(
            f"PDF validation failed: expected {expected_width:g} x {expected_height:g} in, "
            f"got {width:g} x {height:g} pts"
        )
    text, pages_text = _extract_pdf_text(pdf_path)
    expected = expected or {"mode": "combined", "questions": None, "answers": None}
    mode = expected["mode"]
    if not pages_text:
        raise RuntimeError("PDF validation failed: extracted document is empty")
    if len(pages_text) != pages:
        raise RuntimeError(f"PDF validation failed: pdfinfo reports {pages} pages but pdftotext yielded {len(pages_text)}")
    if any(not page.strip() for page in pages_text):
        raise RuntimeError("PDF validation failed: blank page detected")
    if re.search(r"\\frac|\\begin\{|(?<!\\\\)\$[^$]+\$", text):
        raise RuntimeError("PDF validation failed: raw LaTeX remains")
    answer_heading = "答案与解析"
    answer_start = text.find(answer_heading)
    if mode in {"answers", "combined"} and answer_start < 0:
        raise RuntimeError("PDF validation failed: answer section is missing")
    if mode == "combined":
        if answer_start == 0 or "\f" not in text[:answer_start]:
            raise RuntimeError("PDF validation failed: answer section did not start on a new page")
    if mode in {"questions", "combined"}:
        question_text = text if mode == "questions" else text[:answer_start]
        question_numbers = [int(item) for item in re.findall(r"(?m)^\s*(\d+)\.(?=\s|$)", question_text)]
        if expected["questions"] is not None and len(question_numbers) != expected["questions"]:
            raise RuntimeError(f"PDF validation failed: expected {expected['questions']} questions, found {len(question_numbers)}")
        if question_numbers and question_numbers != list(range(1, len(question_numbers) + 1)):
            raise RuntimeError("PDF validation failed: question numbers are not continuous")
    if mode in {"answers", "combined"}:
        answer_text = text[answer_start:]
        answer_numbers = [int(item) for item in re.findall(r"(?m)^第\s*(\d+)\s*题[^\n]*来源：", answer_text)]
        if expected["answers"] is not None and len(answer_numbers) != expected["answers"]:
            raise RuntimeError(f"PDF validation failed: expected {expected['answers']} answers, found {len(answer_numbers)}")
        if answer_numbers and answer_numbers != list(range(1, len(answer_numbers) + 1)):
            raise RuntimeError("PDF validation failed: answer numbers are not continuous")
        blocks = [block for block in re.split(r"(?=第\s*\d+\s*题[^\n]*来源：)", answer_text) if re.search(r"第\s*\d+\s*题[^\n]*来源：", block)]
        if len(blocks) != len(answer_numbers):
            raise RuntimeError("PDF validation failed: answer blocks are incomplete")
        for block in blocks:
            if "来源：" not in block or "答案：" not in block or "解析：" not in block:
                raise RuntimeError("PDF validation failed: an answer lacks source, answer, or explanation")
    repeated_margin = []
    for page in pages_text:
        lines = [line.strip() for line in page.splitlines() if line.strip()]
        repeated_margin.extend(lines[:1] + lines[-1:])
    if any(marker in line.lower() for line in repeated_margin for marker in ("header", "footer", "file://", "chrome://")):
        raise RuntimeError("PDF validation failed: header/footer marker detected")
    visual = _visual_check_pdf(
        pdf_path,
        pages_text,
        pdf_config.get("_visual_review_dir") if isinstance(pdf_config, dict) else None,
        html_name.rsplit(".", 1)[0],
    )
    return {
        "pages": len(pages_text),
        "visual_pages": visual["pages"],
        "visual_scan": visual["scan"],
        "ai_review_pages": visual["ai_review_pages"],
        "visual_review_dir": visual["review_dir"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--html-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--chromium", type=Path, help="Optional Chromium/Chrome/Edge executable; auto-detected when omitted")
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--port", type=int, default=0, help="DevTools port; 0 selects a free local port")
    parser.add_argument("--config", type=Path, help="Optional pipeline config JSON")
    args = parser.parse_args()
    audit_path = args.output_dir / "pdf_audit.json"
    audit = {"status": "passed", "files": [], "issues": []}
    try:
        pdf_config = {}
        cfg = {}
        if args.config:
            if not args.config.exists():
                raise RuntimeError(f"config file not found: {args.config}")
            cfg = json.loads(args.config.read_text(encoding="utf-8"))
            pdf_config = cfg.get("pdf", {})
        if not isinstance(pdf_config, dict):
            raise RuntimeError("config.pdf must be an object")
        if not pdf_config.get("enabled", True):
            audit = {"status": "skipped", "files": [], "issues": [{"level": "INFO", "message": "pdf.enabled=false"}]}
            print("SKIP PDF export: pdf.enabled=false")
            return
        html_paths = sorted(args.html_dir.glob("*.html"))
        if not html_paths:
            raise RuntimeError("PDF export failed: no HTML files found")
        dependency_errors = runtime_dependency_errors()
        if dependency_errors:
            raise RuntimeError("PDF export prerequisites missing: " + "; ".join(dependency_errors))
        browser_config = pdf_config.get("chromium")
        chrome = discover_browser(str(args.chromium) if args.chromium else browser_config)
        if args.profile:
            profile = args.profile
        elif args.config:
            root_value = Path(cfg.get("project_root", "."))
            root = (args.config.parent / root_value).resolve() if not root_value.is_absolute() else root_value.resolve()
            profile = root / cfg.get("work_dir", "work") / args.output_dir.parent.name / "pdf_profile"
            pdf_config = dict(pdf_config)
            pdf_config["_visual_review_dir"] = str(root / cfg.get("work_dir", "work") / args.output_dir.parent.name / "pdf_visual_review")
        else:
            profile = args.output_dir.parent.parent / "work" / args.output_dir.parent.name / "pdf_profile"
        for html_path in html_paths:
            try:
                item = export_one(chrome, html_path, args.output_dir / (html_path.stem + ".pdf"), args.port, profile, pdf_config)
                audit["files"].append(item)
                print(f"OK {html_path.name}")
            except Exception as exc:
                issue = {"level": "ERROR", "file": html_path.name, "message": str(exc)}
                audit["issues"].append(issue)
                audit["files"].append({"file": html_path.with_suffix(".pdf").name, "status": "failed", "issues": [issue]})
                raise
    except Exception as exc:
        audit["status"] = "failed"
        message = str(exc)
        if not any(issue.get("message") == message for issue in audit["issues"]):
            audit["issues"].append({"level": "ERROR", "message": message})
        for partial_pdf in args.output_dir.glob("*.pdf"):
            try:
                partial_pdf.unlink()
            except OSError:
                pass
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise
    finally:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        if not audit_path.exists():
            audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
