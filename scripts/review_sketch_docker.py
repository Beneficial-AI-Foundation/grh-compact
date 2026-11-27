#!/usr/bin/env python3
"""Review a frozen proof sketch in a Docker container without the Lean repository."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sketch_format import gap_statements


ROOT = Path(__file__).resolve().parents[1]
CODEX_BINARY = (Path.home() / ".npm-global/lib/node_modules/@openai/codex/node_modules/"
                "@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex")
CODE_MODE_HOST = CODEX_BINARY.with_name("codex-code-mode-host")
AUTH = Path.home() / ".codex/auth.json"
IMAGE = "python:3.12-slim"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sketch", type=Path, default=ROOT / "summaries/New.grh.tex")
    parser.add_argument("--facts", type=Path, default=ROOT / "summaries/New.grh.allowed-facts.md")
    parser.add_argument("--metadata", type=Path, default=ROOT / "summaries/New.grh.json")
    parser.add_argument("--target-file", type=Path, help="exact target statement for this node")
    parser.add_argument("--output-dir", type=Path, help="directory for this review's audit files")
    parser.add_argument("--structured", action="store_true", help="require a machine-readable verdict")
    parser.add_argument("--child-proofs", type=Path,
                        help="independently reviewed child proofs for upward recheck")
    parser.add_argument("--dry-run", action="store_true", help="prepare input and check isolation only")
    args = parser.parse_args()

    sketch_bytes = args.sketch.read_bytes()
    facts_bytes = args.facts.read_bytes()
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    if digest(sketch_bytes) != metadata["sketch_sha256"]:
        parser.error("sketch differs from frozen metadata")
    expected = list(gap_statements(sketch_bytes.decode("utf-8"),
                                   allow_zero=bool(metadata.get("target_statement_sha256"))))
    if not CODEX_BINARY.is_file() or not CODE_MODE_HOST.is_file() or not AUTH.is_file():
        parser.error("local Codex binaries or account authentication are missing")

    target = (args.target_file.read_text(encoding="utf-8") if args.target_file else
              "For every positive modulus q, Dirichlet character χ modulo q, and complex ρ "
              "with Re ρ > 1/2, L(ρ, χ) is nonzero unless χ is principal and ρ = 1.")
    child_proofs = args.child_proofs.read_text(encoding="utf-8") if args.child_proofs else "None."
    verdict_instructions = """
Return only a JSON object with this schema:
{"gaps":[{"number":1,"status":"proved|unproved|incomplete",
"reason":"specific proof or first obstruction"}],
"parent_closes":false,"parent_reason":"specific deduction or obstruction"}.
Use "incomplete" if a gap lacks definitions, quantifiers, hypotheses, or an exact conclusion.
Use "proved" only with a checkable proof in reason. Include every numbered gap exactly once.
Set parent_closes true only if all gaps are proved and the deduction is checked.
""" if args.structured else ""
    prompt = f"""You are an independent mathematical reviewer. Use only the target, allowed facts,
and frozen sketch below. Do not call tools, browse, or seek the original Lean proof.

For each numbered gap, give a checkable informal proof or identify the first precise obstruction.
If reviewed child proofs are supplied, verify that their exact targets and conclusions fill the
corresponding gaps; use their proof text as additional evidence, not as an unchecked assertion.
Then check whether the proved gaps and allowed facts imply the target, including all hypotheses,
quantifiers, and numerical estimates. Report each gap as proved, conditional, or unproved;
report separately whether the whole argument closes. Do not claim success for an unproved gap.
{verdict_instructions}

TARGET
{target}

ALLOWED FACTS
{facts_bytes.decode('utf-8')}

FROZEN SKETCH
{sketch_bytes.decode('utf-8')}

REVIEWED CHILD PROOFS (if any)
{child_proofs}
"""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output_dir or ROOT / "summaries/reviews" / stamp
    with tempfile.TemporaryDirectory(prefix="grh-review-bundle-") as directory:
        bundle = Path(directory)
        bundle.chmod(0o755)
        (bundle / "input.txt").write_text(prompt, encoding="utf-8")
        docker = [
            "docker", "run", "--rm", "--read-only", "--cap-drop", "ALL",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--security-opt", "no-new-privileges", "--pids-limit", "128",
            "--memory", "2g", "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m",
            "--network", "bridge", "--mount", f"type=bind,src={bundle},dst=/review,readonly",
            "--mount", f"type=bind,src={CODEX_BINARY},dst=/codex,readonly",
            "--mount", f"type=bind,src={CODE_MODE_HOST},dst=/codex-code-mode-host,readonly",
            "--mount", f"type=bind,src={AUTH},dst=/auth/auth.json,readonly",
            "--env", "HOME=/tmp/reviewer", "--env", "CODEX_HOME=/tmp/reviewer/.codex",
            IMAGE,
        ]
        probe = subprocess.run(
            docker + ["/bin/sh", "-c", "test ! -e /home/zhang-liao/grh-lean/grh-compact/src/New/Main.lean && "
                      "test ! -e /home/zhang-liao/riemann-hypothesis-lean && "
                      "test \"$(find /review -type f | wc -l)\" -eq 1 && "
                      "test -f /review/input.txt && test -r /auth/auth.json"],
            text=True, capture_output=True,
        )
        if probe.returncode:
            raise RuntimeError(f"Docker isolation preflight failed: {probe.stderr}")
        if args.dry_run:
            print("Docker file-isolation preflight passed; review not started")
            return

        output.mkdir(parents=True, exist_ok=False)
        (output / "input.txt").write_text(prompt, encoding="utf-8")
        command = ("mkdir -p /tmp/reviewer/.codex && cp /auth/auth.json "
                   "/tmp/reviewer/.codex/auth.json && "
                   "exec /codex exec --ephemeral --ignore-user-config --ignore-rules "
                   "--skip-git-repo-check --sandbox read-only --cd /review --json - "
                   "< /review/input.txt")
        result = subprocess.run(docker + ["/bin/sh", "-c", command], text=True,
                                capture_output=True, timeout=1200)
        (output / "events.jsonl").write_text(result.stdout, encoding="utf-8")
        (output / "stderr.txt").write_text(result.stderr, encoding="utf-8")
        events = []
        for line in result.stdout.splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        messages = [e["item"].get("text", "") for e in events
                    if e.get("type") == "item.completed"
                    and e.get("item", {}).get("type") == "agent_message"]
        (output / "answer.txt").write_text(messages[-1] if messages else "", encoding="utf-8")
        if args.structured and messages:
            try:
                answer = messages[-1].strip()
                if answer.startswith("```"):
                    answer = re.sub(r"^```(?:json)?\s*|\s*```$", "", answer).strip()
                verdict = json.loads(answer)
                gaps = verdict["gaps"]
                if (sorted(g["number"] for g in gaps) != sorted(expected)
                    or len(gaps) != len(expected)
                    or any(g["status"] not in {"proved", "unproved", "incomplete"}
                           or not isinstance(g["reason"], str) or not g["reason"] for g in gaps)
                    or not isinstance(verdict["parent_closes"], bool)
                    or not isinstance(verdict["parent_reason"], str)
                    or (verdict["parent_closes"] and
                        any(g["status"] != "proved" for g in gaps))):
                    raise ValueError("verdict does not match numbered gaps or closure rules")
                (output / "verdict.json").write_text(json.dumps(verdict, indent=2) + "\n",
                                                     encoding="utf-8")
            except (ValueError, KeyError, TypeError) as exc:
                (output / "verdict-error.txt").write_text(str(exc), encoding="utf-8")
        tool_items = sorted({e.get("item", {}).get("type", "") for e in events
                             if e.get("type", "").startswith("item.")
                             and e.get("item", {}).get("type") not in
                             ("agent_message", "reasoning")})
        errors = [e.get("item", {}).get("message", "") for e in events
                  if e.get("item", {}).get("type") == "error"]
        report = {
            "schema": "isolated-sketch-review/v1",
            "sketch_sha256": digest(sketch_bytes),
            "facts_sha256": digest(facts_bytes),
            "input_sha256": digest(prompt.encode("utf-8")),
            "docker_image": IMAGE,
            "filesystem_probe_passed": True,
            "exit_code": result.returncode,
            "turn_completed": any(e.get("type") == "turn.completed" for e in events),
            "tool_item_types": tool_items,
            "errors": errors,
            "authentication_mounted_inside_container": True,
        }
        (output / "audit.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"review output: {output}")
        if result.returncode or not report["turn_completed"]:
            raise RuntimeError("review did not complete; inspect stderr and events")
        if args.structured and not (output / "verdict.json").is_file():
            raise RuntimeError("review verdict was invalid; inspect answer and verdict-error")


if __name__ == "__main__":
    main()
