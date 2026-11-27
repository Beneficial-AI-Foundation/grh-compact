#!/usr/bin/env python3
"""Build navigable PDFs for existing recursive sketch runs without editing reviewed TeX."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
from pathlib import Path

from run_recursive_sketch import compile_pdf
from sketch_format import gap_statements


def linked_source(source: str, parent: tuple[str, int] | None,
                  children: dict[int, tuple[str, int]]) -> str:
    if parent or children:
        source = source.replace(r"\begin{document}",
                                "\\usepackage{hyperref}\n\\begin{document}", 1)
    if parent:
        parent_id, parent_version = parent
        link = (f"\\noindent\\href{{../{parent_id}/v{parent_version}.pdf}}"
                "{Back to parent sketch}\\par\\medskip\n")
        source = source.replace(r"\begin{document}", r"\begin{document}" + "\n" + link, 1)
    for number, (child_id, child_version) in children.items():
        header = re.compile(r"(\\textbf\{Gap\s+" + str(number) +
                            r"(?:\s+\([^{}]*\))?\.\})")
        link = f"\\href{{../{child_id}/v{child_version}.pdf}}{{Open Gap {number} sketch}}"
        source, count = header.subn(lambda match: match.group(1) + " " + link, source, count=1)
        if count != 1:
            raise ValueError(f"could not link Gap {number}")
    return source


def backfill(run: Path) -> int:
    data = json.loads((run / "state.json").read_text(encoding="utf-8"))
    nodes = data["nodes"]
    built = 0
    for node in nodes.values():
        folder = run / "nodes" / node["id"]
        for sketch in sorted(folder.glob("v[0-9]*.tex")):
            version = int(sketch.stem[1:])
            source = sketch.read_text(encoding="utf-8")
            gaps = gap_statements(source, allow_zero=True)
            children = {}
            for number, child_id in node["children"].items():
                if int(number) in gaps and nodes[child_id]["version"]:
                    children[int(number)] = (child_id, nodes[child_id]["version"])
            parent = None
            if node["parent"]:
                parent_version = node.get("parent_version", nodes[node["parent"]]["version"])
                parent = (node["parent"], parent_version)
            linked = linked_source(source, parent, children)
            with tempfile.TemporaryDirectory(prefix="recursive-navigation-") as temporary:
                temporary_sketch = Path(temporary) / sketch.name
                temporary_sketch.write_text(linked, encoding="utf-8")
                compile_pdf(temporary_sketch)
                shutil.copy2(temporary_sketch.with_suffix(".pdf"), sketch.with_suffix(".pdf"))
            built += 1
    return built


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path, nargs="+", help="recursive run directories")
    args = parser.parse_args()
    for run in args.runs:
        print(f"{run}: built {backfill(run)} PDFs")


if __name__ == "__main__":
    main()
