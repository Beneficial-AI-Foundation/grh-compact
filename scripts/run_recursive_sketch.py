#!/usr/bin/env python3
"""Run bounded, resumable sketch/review recursion from New.grh."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
GAP = re.compile(r"\\textbf\{Gap\s+(\d+)\.\}\s*(.*?)(?=\\medskip|\\section\*|\\end\{document\}|\\textbf\{Gap\s+\d+\.)", re.S)


def save(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def key(statement: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", "", statement).encode("utf-8")).hexdigest()


def gaps_from(sketch: Path) -> dict[int, str]:
    text = sketch.read_text(encoding="utf-8")
    found = {int(number): body.strip() for number, body in GAP.findall(text)}
    if len(found) != len(GAP.findall(text)):
        raise ValueError(f"duplicate gap number in {sketch}")
    return found


def call(command: list[str], log: Path) -> None:
    try:
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=1200)
    except subprocess.TimeoutExpired as exc:
        log.write_text("Command timed out after 1200 seconds: " + " ".join(command) +
                       "\n" + str(exc), encoding="utf-8")
        raise RuntimeError(f"command timed out; see {log}") from exc
    log.write_text("COMMAND " + " ".join(command) + "\n\nSTDOUT\n" + result.stdout +
                   "\nSTDERR\n" + result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}); see {log}")


def node_dir(run: Path, identifier: str) -> Path:
    path = run / "nodes" / identifier
    path.mkdir(parents=True, exist_ok=True)
    return path


def generate(node: dict, run: Path, root: dict, model: str | None) -> None:
    folder = node_dir(run, node["id"])
    version = node["version"] + 1
    sketch = folder / f"v{version}.tex"
    facts = run / "allowed-facts.md"
    command = [sys.executable, str(SCRIPTS / "summarize_math.py"), root["source"],
               node["id"] if node["parent"] else root["declaration"],
               "--output", str(sketch), "--allowed-facts", str(facts)]
    if node["parent"]:
        statement = folder / "target.txt"
        statement.write_text(node["statement"] + "\n", encoding="utf-8")
        command += ["--target-statement", str(statement)]
    if node.get("revision_reason"):
        note = folder / f"revision-v{version}.txt"
        note.write_text(node["revision_reason"] + "\n", encoding="utf-8")
        command += ["--revision-note", str(note)]
        command += ["--prior-sketch", str(folder / f"v{node['version']}.tex")]
    if model:
        command += ["--model", model]
    call(command, folder / f"generate-v{version}.log")
    node.update(version=version, status="review", revision_reason=None)


def reviewed_subtree(node: dict, nodes: dict, run: Path) -> str:
    pieces = [f"NODE {node['id']}\nEXACT TARGET\n{node['statement']}\n"]
    if node.get("review_output"):
        answer = run / node["review_output"] / "answer.txt"
        pieces.append("INDEPENDENT REVIEW\n" + answer.read_text(encoding="utf-8"))
    for child_id in node["children"].values():
        pieces.append(reviewed_subtree(nodes[child_id], nodes, run))
    return "\n\n".join(pieces)


def review(node: dict, run: Path, nodes: dict | None = None) -> dict:
    folder = node_dir(run, node["id"])
    version = node["version"]
    target = folder / "target.txt"
    if not target.exists():
        target.write_text(node["statement"] + "\n", encoding="utf-8")
    attempt = 1
    while (folder / f"review-v{version}-a{attempt}").exists():
        attempt += 1
    output = folder / f"review-v{version}-a{attempt}"
    command = [sys.executable, str(SCRIPTS / "review_sketch_docker.py"),
          "--sketch", str(folder / f"v{version}.tex"),
          "--metadata", str(folder / f"v{version}.json"),
          "--facts", str(run / "allowed-facts.md"),
          "--target-file", str(target), "--output-dir", str(output), "--structured"]
    if nodes is not None:
        proofs = folder / f"child-proofs-v{version}-a{attempt}.txt"
        proofs.write_text("\n\n".join(reviewed_subtree(nodes[child_id], nodes, run)
                                     for child_id in node["children"].values()),
                          encoding="utf-8")
        command += ["--child-proofs", str(proofs)]
    call(command, folder / f"review-v{version}-a{attempt}.log")
    node["review_output"] = str(output.relative_to(run))
    return json.loads((output / "verdict.json").read_text(encoding="utf-8"))


def initialize(run: Path) -> dict:
    run.mkdir(parents=True, exist_ok=False)
    (run / "nodes").mkdir()
    shutil.copy2(ROOT / "summaries/New.grh.allowed-facts.md", run / "allowed-facts.md")
    root_meta = json.loads((ROOT / "summaries/New.grh.json").read_text(encoding="utf-8"))
    source = ROOT / root_meta["source"]
    if hashlib.sha256(source.read_bytes()).hexdigest() != root_meta["source_sha256"]:
        raise RuntimeError("root Lean source differs from frozen metadata")
    folder = node_dir(run, "root")
    shutil.copy2(ROOT / "summaries/New.grh.tex", folder / "v1.tex")
    shutil.copy2(ROOT / "summaries/New.grh.json", folder / "v1.json")
    statement = ("For every positive modulus q, Dirichlet character χ modulo q, and complex ρ "
                 "with Re ρ > 1/2, L(ρ, χ) is nonzero unless χ is principal and ρ = 1.")
    return {"schema": "recursive-sketch/v1", "root": "root", "source": root_meta["source"],
            "declaration": root_meta["target"], "rounds_used": 0, "stop_reason": None,
            "nodes": {
                "root": {"id": "root", "parent": None, "parent_gap": None, "depth": 0,
                         "statement": statement, "version": 1, "revisions": 0,
                         "status": "review", "children": {}, "reason": ""}}}


def reserve_round(data: dict, run: Path, args: argparse.Namespace) -> bool:
    """Persist one model-call slot before launching a possibly failing command."""
    maximum = getattr(args, "max_rounds", None)
    data.setdefault("rounds_used", 0)
    if maximum is not None and data["rounds_used"] >= maximum:
        data["stop_reason"] = "max-rounds reached"
        return False
    data["rounds_used"] += 1
    data["stop_reason"] = None
    save(run / "state.json", data)
    return True


def advance(data: dict, run: Path, args: argparse.Namespace) -> bool:
    nodes = data["nodes"]
    # Resolve bookkeeping that needs no model call before enforcing the round cap.
    for node in sorted(nodes.values(), key=lambda n: -n["depth"]):
        if node["status"] != "waiting":
            continue
        children = [nodes[identifier] for identifier in node["children"].values()]
        if any(child["status"] == "stuck" for child in children):
            node["status"] = "stuck"
            node["reason"] = "a child gap remains unproved"
            return True
        if children and all(child["status"] == "closed" for child in children):
            node["status"] = "recheck"
            node["reason"] = "independent upward check pending"
            return True
    for node in list(nodes.values()):
        if node["status"] == "recheck":
            if not reserve_round(data, run, args):
                return False
            verdict = review(node, run, nodes)
            node["verdict"] = verdict
            node["status"] = "closed" if verdict["parent_closes"] else "stuck"
            node["reason"] = verdict["parent_reason"]
            return True
        if node["status"] == "sketch":
            if not reserve_round(data, run, args):
                return False
            generate(node, run, data, args.model)
            return True
        if node["status"] != "review":
            continue
        if not reserve_round(data, run, args):
            return False
        verdict = review(node, run)
        folder = node_dir(run, node["id"])
        sketch_gaps = gaps_from(folder / f"v{node['version']}.tex")
        node["verdict"] = verdict
        incomplete = [g for g in verdict["gaps"] if g["status"] == "incomplete"]
        if incomplete and node["revisions"] < args.max_revisions:
            node["revisions"] += 1
            node["revision_reason"] = "\n".join(
                f"Gap {g['number']}: {g['reason']}" for g in incomplete)
            node["status"] = "sketch"
            node["reason"] = "revising incomplete gap statements"
            return True
        if incomplete:
            node["status"] = "stuck"
            node["reason"] = "gap statements remain incomplete after revision budget"
            return True
        if verdict["parent_closes"]:
            node["status"] = "closed"
            node["reason"] = verdict["parent_reason"]
            return True
        unproved = [g for g in verdict["gaps"] if g["status"] == "unproved"]
        if not unproved:
            node["status"] = "stuck"
            node["reason"] = verdict["parent_reason"]
            return True
        if node["depth"] >= args.max_depth:
            node["status"] = "stuck"
            node["reason"] = "depth budget reached: " + verdict["parent_reason"]
            return True
        needed = sum(f"{node['id']}-g{g['number']}" not in nodes for g in unproved)
        if len(nodes) + needed > args.max_nodes:
            node["status"] = "budget"
            node["reason"] = "node budget reached before all child gaps could be created"
            return True
        for gap in unproved:
            number = gap["number"]
            statement = sketch_gaps[number]
            child_id = f"{node['id']}-g{number}"
            if child_id in nodes:
                continue
            ancestors = []
            current = node
            while current:
                ancestors.append(key(current["statement"]))
                current = nodes.get(current["parent"])
            if key(statement) in ancestors:
                node["status"] = "stuck"
                node["reason"] = f"Gap {number} repeats an ancestor statement"
                return True
            nodes[child_id] = {"id": child_id, "parent": node["id"], "parent_gap": number,
                               "depth": node["depth"] + 1, "statement": statement,
                               "version": 0, "revisions": 0, "status": "sketch",
                               "children": {}, "reason": ""}
            node["children"][str(number)] = child_id
        node["status"] = "waiting"
        node["reason"] = verdict["parent_reason"]
        return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--max-depth", type=int, default=2)
    parser.add_argument("--max-nodes", type=int, default=7)
    parser.add_argument("--max-revisions", type=int, default=2)
    parser.add_argument("--max-rounds", type=int,
                        help="maximum total model calls (sketch generation or Docker review)")
    parser.add_argument("--model", help="model override for sketch generation")
    args = parser.parse_args()
    if (min(args.max_depth, args.max_nodes, args.max_revisions) < 0 or args.max_nodes < 1
        or (args.max_rounds is not None and args.max_rounds < 0)):
        parser.error("budgets must be nonnegative; max-nodes must be positive")
    run = (args.run_dir or ROOT / "summaries/recursive" /
           datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")).resolve()
    state = run / "state.json"
    data = json.loads(state.read_text(encoding="utf-8")) if state.exists() else initialize(run)
    if "rounds_used" not in data:
        data["rounds_used"] = (len(list((run / "nodes").glob("*/generate-v*.log"))) +
                               len(list((run / "nodes").glob("*/review-v*-a*.log"))))
    data["stop_reason"] = None
    save(state, data)
    try:
        while advance(data, run, args):
            save(state, data)
            print("; ".join(f"{n['id']}={n['status']}@v{n['version']}"
                            for n in data["nodes"].values()), flush=True)
    finally:
        save(state, data)
        leaves = [{"id": n["id"], "status": n["status"], "reason": n["reason"]}
                  for n in data["nodes"].values() if not n["children"]]
        save(run / "summary.json", {"root_status": data["nodes"]["root"]["status"],
                                    "node_count": len(data["nodes"]),
                                    "rounds_used": data["rounds_used"],
                                    "stop_reason": data["stop_reason"], "leaves": leaves})
        print(f"recursive run: {run}")
        print(f"root status: {data['nodes']['root']['status']}; nodes: {len(data['nodes'])}; "
              f"rounds: {data['rounds_used']}; stop: {data['stop_reason']}")


if __name__ == "__main__":
    main()
