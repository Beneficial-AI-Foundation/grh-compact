#!/usr/bin/env python3
"""Count English prose words in a LaTeX proof sketch (including headings)."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


MATH_ENVIRONMENTS = (
    "math", "displaymath", "equation", "equation*", "align", "align*",
    "gather", "gather*", "multline", "multline*", "split", "cases",
)
WORD = re.compile(r"[A-Za-z]+(?:['’-][A-Za-z]+)*")


def count_words(latex: str) -> int:
    # A comment consumes the remainder of its line, except after an escaped percent sign.
    latex = re.sub(r"(?<!\\)%[^\n]*", "", latex)
    title = re.search(r"\\title\{([^{}]*)\}", latex)
    document = re.search(r"\\begin\{document\}(.*?)\\end\{document\}", latex,
                         flags=re.DOTALL)
    if document:
        latex = (title.group(1) if title else "") + "\n" + document.group(1)
    for environment in MATH_ENVIRONMENTS:
        name = re.escape(environment)
        latex = re.sub(r"\\begin\{" + name + r"\}.*?\\end\{" + name + r"\}",
                       " ", latex, flags=re.DOTALL)
    for pattern in (r"\$\$.*?\$\$", r"(?<!\\)\$.*?(?<!\\)\$",
                    r"\\\[.*?\\\]", r"\\\(.*?\\\)"):
        latex = re.sub(pattern, " ", latex, flags=re.DOTALL)
    # Keep arguments of section, textbf, etc.; their prose counts.
    latex = re.sub(r"\\[A-Za-z@]+\*?(?:\[[^\]]*\])?", " ", latex)
    latex = re.sub(r"\\[^A-Za-z\s]", " ", latex)
    # Environment names are formatting, not prose.
    latex = re.sub(r"\{(?:document|itemize|enumerate|description)\}", " ", latex)
    return len(WORD.findall(latex))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()
    words = count_words(args.file.read_text(encoding="utf-8"))
    print(f"{words} English prose words (limit {args.limit})")
    if words > args.limit:
        parser.exit(1, "word limit exceeded\n")


if __name__ == "__main__":
    main()
