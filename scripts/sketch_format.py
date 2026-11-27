"""Validate and extract the numbered gaps in a generated LaTeX sketch."""

from __future__ import annotations

import re


HEADER = re.compile(r"\\textbf\{Gap\s+(\d+)(?:\s+\([^{}]*\))?\.\}")
GAP_START = re.compile(r"\\textbf\{Gap\b")
BOUNDARY = re.compile(r"\\medskip\b|\\section\*\{|\\end\{document\}")
SECTIONS = ("Main line", "Gaps to prove independently", "Allowed facts")


def gap_statements(sketch: str, *, allow_zero: bool = False) -> dict[int, str]:
    """Return gap bodies, raising ValueError for a sketch the reviewer cannot parse."""
    if not sketch.lstrip().startswith(r"\documentclass") or r"\end{document}" not in sketch:
        raise ValueError("sketch must be a complete LaTeX document")
    positions = [sketch.find(r"\section*{" + section + "}") for section in SECTIONS]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        raise ValueError("sketch must contain Main line, Gaps to prove independently, and Allowed facts sections in order")
    headers = list(HEADER.finditer(sketch))
    if len(headers) != len(GAP_START.findall(sketch)):
        raise ValueError(r"gap headings must use \textbf{Gap N.} (optional parenthesized title before the period)")
    numbers = [int(match.group(1)) for match in headers]
    if numbers != list(range(1, len(numbers) + 1)):
        raise ValueError(f"gap numbers must be consecutive from 1; found {numbers}")
    if not headers and not allow_zero:
        raise ValueError("root sketch must contain at least one numbered gap")
    if any(not positions[1] < match.start() < positions[2] for match in headers):
        raise ValueError("all gap headings must be in Gaps to prove independently")
    gaps = {}
    for index, match in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else positions[2]
        body = sketch[match.end():end]
        boundary = BOUNDARY.search(body)
        if boundary:
            body = body[:boundary.start()]
        body = body.strip()
        if not body:
            raise ValueError(f"Gap {numbers[index]} has no statement")
        gaps[numbers[index]] = body
    return gaps
