#!/usr/bin/env python3
"""Run resumable gap-by-gap sketch/review recursion from New.grh."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sketch_format import gap_statements

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent


def save(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def key(statement: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", "", statement).encode("utf-8")).hexdigest()


def gaps_from(sketch: Path) -> dict[int, str]:
    return gap_statements(sketch.read_text(encoding="utf-8"), allow_zero=True)


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


def compile_pdf(sketch: Path) -> None:
    """Build the PDF beside its source, keeping TeX auxiliary files temporary."""
    with tempfile.TemporaryDirectory(prefix="recursive-sketch-pdf-") as temporary:
        result = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-halt-on-error",
             "-output-directory", temporary, str(sketch.resolve())],
            cwd=sketch.parent, text=True, capture_output=True, timeout=120,
        )
        if result.returncode:
            log = sketch.with_suffix(".pdf-build.log")
            log.write_text(result.stdout + "\nSTDERR\n" + result.stderr, encoding="utf-8")
            raise RuntimeError(f"PDF compilation failed; see {log}")
        shutil.copy2(Path(temporary) / sketch.with_suffix(".pdf").name,
                     sketch.with_suffix(".pdf"))


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
        parent = root["nodes"][node["parent"]]
        parent_version = node.get("parent_version", parent["version"])
        parent_sketch = node_dir(run, parent["id"]) / f"v{parent_version}.tex"
        if not parent_sketch.with_suffix(".pdf").exists():
            compile_pdf(parent_sketch)
        command += ["--parent-pdf", f"../{parent['id']}/v{parent_version}.pdf",
                    "--parent-gap", str(node["parent_gap"])]
    if node.get("revision_reason"):
        note = folder / f"revision-v{version}.txt"
        note.write_text(node["revision_reason"] + "\n", encoding="utf-8")
        command += ["--revision-note", str(note)]
        command += ["--prior-sketch", str(folder / f"v{node['version']}.tex")]
    if model:
        command += ["--model", model]
    call(command, folder / f"generate-v{version}.log")
    compile_pdf(sketch)
    node.update(version=version, phase="review", revision_reason=None)


def set_active(node: dict, phase: str) -> None:
    node["status"] = "active"
    node["phase"] = phase
    node.pop("blocked_kind", None)


def set_blocked(node: dict, kind: str, reason: str) -> None:
    node["status"] = "blocked"
    node["phase"] = None
    node["blocked_kind"] = kind
    node["reason"] = reason


def migrate_node_states(data: dict) -> None:
    """Keep existing runs resumable while separating status from the next phase."""
    if data.get("schema") == "recursive-sketch/v3":
        return
    phases = {"sketch", "review", "expand", "recheck"}
    for node in data["nodes"].values():
        old = node["status"]
        if old in phases:
            set_active(node, old)
        elif old in {"stuck", "budget"}:
            set_blocked(node, "budget" if old == "budget" or
                        node.get("reason", "").startswith("depth budget reached:") else "proof",
                        node.get("reason", ""))
        elif old in {"waiting", "closed"}:
            node["phase"] = None
        else:
            raise ValueError(f"unknown node status: {old}")
    data["schema"] = "recursive-sketch/v3"


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
    return {"schema": "recursive-sketch/v3", "round_semantics": "gap-attempts",
            "root": "root", "source": root_meta["source"],
            "declaration": root_meta["target"], "rounds_used": 0, "stop_reason": None,
            "nodes": {
                "root": {"id": "root", "parent": None, "parent_gap": None, "depth": 0,
                         "statement": statement, "version": 1, "revisions": 0,
                         "status": "active", "phase": "review", "children": {}, "reason": ""}}}


def reserve_round(data: dict, run: Path, args: argparse.Namespace, node: dict) -> bool:
    """Reserve one round for a new child gap before attempting its sketch."""
    if node.get("round_reserved"):
        return True
    maximum = getattr(args, "max_rounds", None)
    data.setdefault("rounds_used", 0)
    if maximum is not None and data["rounds_used"] >= maximum:
        data["stop_reason"] = "max-rounds reached"
        return False
    data["rounds_used"] += 1
    node["round_reserved"] = True
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
        if any(child["status"] in {"active", "waiting"}
               for child in children):
            continue
        if any(child["status"] == "blocked" for child in children):
            set_blocked(node, "child", "a child gap remains blocked")
            return True
        if children and all(child["status"] == "closed" for child in children):
            set_active(node, "recheck")
            node["reason"] = "independent upward check pending"
            return True
    for node in list(nodes.values()):
        if node["status"] != "active":
            continue
        phase = node["phase"]
        if phase == "recheck":
            verdict = review(node, run, nodes)
            node["verdict"] = verdict
            if verdict["parent_closes"]:
                node["status"] = "closed"
                node["phase"] = None
            else:
                set_blocked(node, "proof", verdict["parent_reason"])
            node["reason"] = verdict["parent_reason"]
            return True
        if phase == "sketch":
            # A round is one attempt to explain a gap. The root sketch is
            # already frozen, and revisions stay within the same attempt.
            if node["parent"] is not None and node["version"] == 0 and not reserve_round(data, run, args, node):
                return False
            generate(node, run, data, args.model)
            return True
        if phase not in {"review", "expand"}:
            raise ValueError(f"unknown active phase: {phase}")
        verdict = node["verdict"] if phase == "expand" else review(node, run)
        folder = node_dir(run, node["id"])
        sketch_gaps = gaps_from(folder / f"v{node['version']}.tex")
        node["verdict"] = verdict
        incomplete = [g for g in verdict["gaps"] if g["status"] == "incomplete"]
        if incomplete and node["revisions"] < args.max_revisions:
            node["revisions"] += 1
            node["revision_reason"] = "\n".join(
                f"Gap {g['number']}: {g['reason']}" for g in incomplete)
            set_active(node, "sketch")
            node["reason"] = "revising incomplete gap statements"
            return True
        if verdict["parent_closes"]:
            node["status"] = "closed"
            node["phase"] = None
            node["reason"] = verdict["parent_reason"]
            return True
        unresolved = [g for g in verdict["gaps"] if g["status"] in {"unproved", "incomplete"}]
        if not unresolved:
            set_blocked(node, "proof", verdict["parent_reason"])
            return True
        if args.max_depth is not None and node["depth"] >= args.max_depth:
            set_blocked(node, "budget", "depth budget reached: " + verdict["parent_reason"])
            return True
        needed = sum(f"{node['id']}-g{g['number']}" not in nodes for g in unresolved)
        if args.max_nodes is not None and len(nodes) + needed > args.max_nodes:
            set_blocked(node, "budget", "node budget reached before all child gaps could be created")
            return True
        for gap in unresolved:
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
                set_blocked(node, "proof", f"Gap {number} repeats an ancestor statement")
                return True
            nodes[child_id] = {"id": child_id, "parent": node["id"], "parent_gap": number,
                               "parent_version": node["version"],
                               "depth": node["depth"] + 1, "statement": statement,
                               "version": 0, "revisions": 0, "status": "active", "phase": "sketch",
                               "children": {}, "reason": ""}
            node["children"][str(number)] = child_id
        node["status"] = "waiting"
        node["phase"] = None
        node["reason"] = verdict["parent_reason"]
        return True
    return False


def resume_blocked(data: dict, *, requested: bool = False,
                   max_depth: int | None = None,
                   max_nodes: int | None = None) -> bool:
    """Reopen a blocked leaf when requested or when its budget was increased."""
    nodes = data["nodes"]
    candidates = sorted(nodes.values(), key=lambda n: -n["depth"])
    for node in candidates:
        if node["status"] != "blocked" or node["children"] or not node.get("verdict"):
            continue
        reason = node.get("reason", "")
        budget = node.get("blocked_kind") == "budget"
        if not requested and not budget:
            continue
        if max_depth is not None and node["depth"] >= max_depth:
            continue
        if not any(g["status"] in {"unproved", "incomplete"}
                   for g in node["verdict"]["gaps"]):
            continue
        if budget and reason.startswith("node budget reached"):
            needed = sum(f"{node['id']}-g{g['number']}" not in nodes
                         for g in node["verdict"]["gaps"]
                         if g["status"] in {"unproved", "incomplete"})
            if max_nodes is not None and len(nodes) + needed > max_nodes:
                continue
        set_active(node, "expand")
        node["reason"] = "resuming unresolved gaps from saved review"
        parent_id = node["parent"]
        while parent_id:
            parent = nodes[parent_id]
            if parent["status"] == "blocked":
                parent["status"] = "waiting"
                parent["phase"] = None
                parent.pop("blocked_kind", None)
            parent_id = parent["parent"]
        return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--max-depth", type=int,
                        help="optional maximum nesting depth (unlimited by default)")
    parser.add_argument("--max-nodes", type=int,
                        help="optional maximum total node count (unlimited by default)")
    parser.add_argument("--max-revisions", type=int, default=2)
    parser.add_argument("--max-rounds", type=int,
                        help="maximum gap attempts; initial root review, revisions, and rechecks do not count")
    parser.add_argument("--model", help="model override for sketch generation")
    parser.add_argument("--resume-stuck", action="store_true",
                        help="expand unresolved gaps in a blocked leaf using its saved review")
    args = parser.parse_args()
    if ((args.max_depth is not None and args.max_depth < 0)
        or (args.max_nodes is not None and args.max_nodes < 1) or args.max_revisions < 0
        or (args.max_rounds is not None and args.max_rounds < 0)):
        parser.error("budgets must be nonnegative; max-nodes must be positive")
    run = (args.run_dir or ROOT / "summaries/recursive" /
           datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")).resolve()
    state = run / "state.json"
    data = json.loads(state.read_text(encoding="utf-8")) if state.exists() else initialize(run)
    if not (run / "nodes/root/v1.pdf").exists():
        compile_pdf(run / "nodes/root/v1.tex")
    # Runs created by v1 counted every model call. Recount their child-gap
    # attempts so --run-dir can resume them with the new meaning of a round.
    if data.get("round_semantics") != "gap-attempts":
        data["rounds_used"] = sum(
            (run / "nodes" / node["id"] / "generate-v1.log").exists()
            for node in data["nodes"].values() if node["parent"] is not None)
        for node in data["nodes"].values():
            if node["parent"] is not None and (run / "nodes" / node["id"] / "generate-v1.log").exists():
                node["round_reserved"] = True
        data["round_semantics"] = "gap-attempts"
    migrate_node_states(data)
    data["stop_reason"] = None
    while resume_blocked(data, requested=args.resume_stuck,
                         max_depth=args.max_depth, max_nodes=args.max_nodes):
        pass
    save(state, data)
    try:
        while advance(data, run, args):
            save(state, data)
            print("; ".join(f"{n['id']}={n['status']}"
                            f"{':' + n['phase'] if n.get('phase') else ''}@v{n['version']}"
                            for n in data["nodes"].values()), flush=True)
    finally:
        save(state, data)
        leaves = [{"id": n["id"], "status": n["status"], "phase": n.get("phase"),
                   "reason": n["reason"]}
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
