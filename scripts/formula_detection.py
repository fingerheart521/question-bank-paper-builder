"""Conservative formula detection for generated and legacy HTML/text."""
from __future__ import annotations

import re


_COMMAND_RE = re.compile(r"\\(?:frac|dfrac|tfrac|sqrt|begin|end|sum|prod|int|lim|alpha|beta|gamma|Delta|infty|cdot|times|leq|geq|neq|pm|matrix|cases)\b")
_DELIMITED_RE = re.compile(r"\\\([^\n]*?\\\)|\\\[[\s\S]*?\\\]")
_DOLLAR_RE = re.compile(r"(?<![\w$])\$([^$\n]{1,400})\$(?![\w$])")
_CURRENCY_RE = re.compile(r"^\s*[+-]?\d[\d,]*(?:\.\d{1,2})?\s*$")


def contains_formula(value: str) -> bool:
    """Return true for likely TeX math while rejecting ordinary currency text."""
    text = str(value or "")
    if _COMMAND_RE.search(text) or _DELIMITED_RE.search(text):
        return True
    for match in _DOLLAR_RE.finditer(text):
        body = match.group(1).strip()
        if not body or _CURRENCY_RE.fullmatch(body):
            continue
        if re.search(r"[\\^_{}=<>]|[A-Za-zΑ-Ωα-ω]", body):
            return True
    return False
