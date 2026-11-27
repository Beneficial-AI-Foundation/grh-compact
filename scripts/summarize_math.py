#!/usr/bin/env python3
"""Generate a short LaTeX proof sketch for a Lean declaration with Codex."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from check_sketch_words import count_words


ROOT = Path(__file__).resolve().parents[1]
PROMPT = ROOT / "scripts/prompts/latex_sketch_summarizer.md"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Lean source file inside this project")
    parser.add_argument("declaration", help="fully qualified target declaration")
    parser.add_argument("--output", type=Path, help="output .tex file")
    parser.add_argument("--allowed-facts", type=Path, help="text file listing permitted facts")
    parser.add_argument("--target-statement", type=Path,
                        help="exact statement of a child gap (instead of a Lean declaration)")
    parser.add_argument("--revision-note", type=Path,
                        help="specific defects to repair in a new sketch version")
    parser.add_argument("--prior-sketch", type=Path, help="previous sketch to revise")
    parser.add_argument("--model", help="Codex model override")
    parser.add_argument("--dry-run", action="store_true", help="print prompt without running Codex")
    args = parser.parse_args()

    source = args.source.resolve()
    if not source.is_file() or not source.is_relative_to(ROOT):
        parser.error("source must be an existing file inside this project")
    if source.suffix != ".lean":
        parser.error("source must be a Lean file")
    output = (args.output or ROOT / "summaries" / f"{args.declaration.split('.')[-1]}.tex").resolve()
    if output.suffix != ".tex":
        parser.error("output must have a .tex extension")

    source_hash = sha256(source.read_bytes())
    prompt_bytes = PROMPT.read_bytes()
    facts = args.allowed_facts.read_text(encoding="utf-8") if args.allowed_facts else "None specified."
    statement = args.target_statement.read_text(encoding="utf-8") if args.target_statement else ""
    revision = args.revision_note.read_text(encoding="utf-8") if args.revision_note else ""
    prior = args.prior_sketch.read_text(encoding="utf-8") if args.prior_sketch else ""
    prompt = (prompt_bytes.decode("utf-8") + "\n\n"
              + f"Target declaration: `{args.declaration}`\n"
              + f"Exact target statement (if this is a child gap):\n{statement}\n"
              + ("For this child gap, a complete proof with zero further gaps is preferred when "
                 "possible; this overrides the generic instruction to include a gap.\n" if statement else "")
              + f"Source file: `{source.relative_to(ROOT)}`\n"
              + f"Target source SHA-256: `{source_hash}`\n"
              + f"Permitted prerequisite statements:\n{facts}\n"
              + f"Defects to repair, if revising a prior sketch:\n{revision}\n"
              + f"Previous sketch to revise, if supplied:\n{prior}\n")
    if args.dry_run:
        print(prompt)
        return

    with tempfile.TemporaryDirectory(prefix="summarize-math-") as temporary:
        draft = Path(temporary) / "sketch.tex"
        command = ["codex", "exec", "--ephemeral", "--sandbox", "read-only",
                   "-C", str(ROOT), "-o", str(draft)]
        if args.model:
            command.extend(["--model", args.model])
        command.append("-")
        result = subprocess.run(command, input=prompt, text=True, cwd=ROOT,
                                capture_output=True)
        if result.returncode:
            raise RuntimeError(f"summarizer failed ({result.returncode}):\n{result.stderr[-4000:]}")
        sketch = draft.read_text(encoding="utf-8").strip()
        if not sketch.startswith("\\documentclass") or "\\end{document}" not in sketch:
            raise ValueError("summarizer did not produce a complete LaTeX document")
        word_count = count_words(sketch)
        if word_count > 500:
            raise ValueError(f"sketch exceeds 500 English prose words: {word_count}")
        if sha256(source.read_bytes()) != source_hash:
            raise RuntimeError("source changed while the summarizer was running")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(sketch + "\n", encoding="utf-8")
    report = {
        "schema": "math-sketch/v1",
        "target": args.declaration,
        "source": str(source.relative_to(ROOT)),
        "source_sha256": source_hash,
        "prompt_sha256": sha256(prompt_bytes),
        "sketch_sha256": sha256(output.read_bytes()),
        "word_count": word_count,
        "model_override": args.model,
        "target_statement_sha256": sha256(statement.encode("utf-8")) if statement else None,
        "revision_note_sha256": sha256(revision.encode("utf-8")) if revision else None,
        "prior_sketch_sha256": sha256(prior.encode("utf-8")) if prior else None,
    }
    output.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n",
                                           encoding="utf-8")
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
