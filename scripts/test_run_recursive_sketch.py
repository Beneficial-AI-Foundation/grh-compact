"""Offline checks for recursive scheduling and upward closure."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import run_recursive_sketch as recursive
from backfill_recursive_sketch_pdfs import backfill
from summarize_math import add_parent_link
from sketch_format import gap_statements


def sketch_with_gaps(body):
    return (r"\documentclass{article}\begin{document}"
            r"\section*{Main line}Main."
            r"\section*{Gaps to prove independently}" + body +
            r"\section*{Allowed facts}None.\end{document}")


class RecursiveSketchTest(unittest.TestCase):
    def test_migrates_legacy_node_states(self):
        old = ["sketch", "review", "expand", "recheck", "waiting", "closed", "stuck", "budget"]
        data = {"schema": "recursive-sketch/v2", "nodes": {
            name: {"status": name, "reason": "node budget reached" if name == "budget" else ""}
            for name in old}}
        recursive.migrate_node_states(data)
        self.assertEqual(data["schema"], "recursive-sketch/v3")
        for name in old[:4]:
            self.assertEqual(data["nodes"][name]["status"], "active")
            self.assertEqual(data["nodes"][name]["phase"], name)
        for name in ("waiting", "closed"):
            self.assertEqual(data["nodes"][name]["status"], name)
            self.assertIsNone(data["nodes"][name]["phase"])
        self.assertEqual(data["nodes"]["stuck"]["blocked_kind"], "proof")
        self.assertEqual(data["nodes"]["budget"]["blocked_kind"], "budget")
        recursive.migrate_node_states(data)
        self.assertEqual(data["nodes"]["review"]["phase"], "review")

    def test_backfill_preserves_reviewed_tex(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            root_dir = recursive.node_dir(run, "root")
            child_dir = recursive.node_dir(run, "root-g1")
            original = sketch_with_gaps(r"\textbf{Gap 1.} Prove P.")
            (root_dir / "v1.tex").write_text(original, encoding="utf-8")
            (child_dir / "v1.tex").write_text(sketch_with_gaps(""), encoding="utf-8")
            state = {"nodes": {
                "root": {"id": "root", "parent": None, "children": {"1": "root-g1"},
                         "version": 1},
                "root-g1": {"id": "root-g1", "parent": "root", "children": {},
                            "parent_gap": 1, "version": 1}}}
            (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
            self.assertEqual(backfill(run), 2)
            self.assertEqual((root_dir / "v1.tex").read_text(encoding="utf-8"), original)
            self.assertTrue((root_dir / "v1.pdf").is_file())
            self.assertTrue((child_dir / "v1.pdf").is_file())

    def test_child_pdf_links_back_to_parent_version(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "nodes/root-g1"
            folder.mkdir(parents=True)
            sketch = folder / "v1.tex"
            linked = add_parent_link(sketch_with_gaps(""), "../root/v3.pdf", 1)
            sketch.write_text(linked, encoding="utf-8")
            self.assertIn(r"\usepackage{hyperref}", linked)
            self.assertIn(r"\href{../root/v3.pdf}{Back to parent sketch (Gap 1)}", linked)
            self.assertEqual(gap_statements(linked, allow_zero=True), {})
            recursive.compile_pdf(sketch)
            self.assertTrue(sketch.with_suffix(".pdf").is_file())

    def test_named_gap_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            sketch = Path(directory) / "sketch.tex"
            sketch.write_text(
                sketch_with_gaps(r"\textbf{Gap 1 (geometric inequality).} Prove P.\medskip"
                                 r"\textbf{Gap 2 (polar window).} Prove R."),
                encoding="utf-8",
            )
            self.assertEqual(recursive.gaps_from(sketch), {1: "Prove P.", 2: "Prove R."})

    def test_rejects_malformed_or_repeated_gap_headers(self):
        for body in (r"\textbf{Gap 1:} Prove P.",
                     r"\textbf{Gap 1.} Prove P.\medskip\textbf{Gap 1.} Prove Q.",
                     r"\textbf{Gap 2.} Prove P.",
                     r"\textbf{Gap 1.}\medskip"):
            with self.subTest(body=body), self.assertRaises(ValueError):
                gap_statements(sketch_with_gaps(body))
        self.assertEqual(gap_statements(sketch_with_gaps("Complete proof."), allow_zero=True), {})

    def test_initial_review_does_not_consume_gap_round(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            recursive.node_dir(run, "root")
            node = {"id": "root", "parent": None, "parent_gap": None, "depth": 0,
                    "statement": "Q", "version": 1, "revisions": 0, "status": "active", "phase": "review",
                    "children": {}, "reason": ""}
            data = {"nodes": {"root": node}, "rounds_used": 0}
            args = SimpleNamespace(max_depth=0, max_nodes=1, max_revisions=0,
                                   max_rounds=0, model=None)
            with patch.object(recursive, "review", return_value={
                    "gaps": [], "parent_closes": True, "parent_reason": "proved"}):
                (run / "nodes/root/v1.tex").write_text(sketch_with_gaps(""), encoding="utf-8")
                self.assertTrue(recursive.advance(data, run, args))
            self.assertEqual(data["rounds_used"], 0)
            self.assertEqual(node["status"], "closed")

    def test_one_round_covers_child_generation_review_and_parent_recheck(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            root_dir = recursive.node_dir(run, "root")
            (root_dir / "v1.tex").write_text(
                sketch_with_gaps(r"\textbf{Gap 1.} Prove P.\medskip"
                                 r"\textbf{Gap 2.} Prove R."),
                encoding="utf-8")
            root = {"id": "root", "parent": None, "parent_gap": None, "depth": 0,
                    "statement": "Q", "version": 1, "revisions": 0, "status": "active", "phase": "review",
                    "children": {}, "reason": ""}
            data = {"nodes": {"root": root}, "source": "src/New/Main.lean",
                    "declaration": "New.grh", "rounds_used": 0}
            args = SimpleNamespace(max_depth=1, max_nodes=3, max_revisions=0,
                                   max_rounds=1, model=None)

            def fake_generate(node, _run, _data, _model):
                node["version"] = 1
                node["phase"] = "review"
                (recursive.node_dir(run, node["id"]) / "v1.tex").write_text(
                    sketch_with_gaps(""), encoding="utf-8")

            def fake_review(node, _run, nodes=None):
                if node["id"] == "root":
                    if nodes is None:
                        return {"gaps": [
                            {"number": 1, "status": "unproved", "reason": "P"},
                            {"number": 2, "status": "unproved", "reason": "R"}],
                            "parent_closes": False, "parent_reason": "two gaps"}
                    return {"gaps": [
                        {"number": 1, "status": "proved", "reason": "P"},
                        {"number": 2, "status": "proved", "reason": "R"}],
                        "parent_closes": True, "parent_reason": "checked"}
                return {"gaps": [], "parent_closes": True, "parent_reason": "proved"}

            with patch.object(recursive, "generate", fake_generate), \
                 patch.object(recursive, "review", fake_review):
                self.assertTrue(recursive.advance(data, run, args))  # Initial review.
                self.assertTrue(recursive.advance(data, run, args))  # Gap 1 sketch.
                self.assertEqual(data["rounds_used"], 1)
                self.assertTrue(recursive.advance(data, run, args))  # Gap 1 review.
                self.assertFalse(recursive.advance(data, run, args))  # Gap 2 awaits budget.
                self.assertEqual(data["nodes"]["root-g2"]["status"], "active")
                self.assertEqual(data["nodes"]["root-g2"]["phase"], "sketch")
                args.max_rounds = 2
                self.assertTrue(recursive.advance(data, run, args))  # Gap 2 sketch.
                self.assertTrue(recursive.advance(data, run, args))  # Gap 2 review.
                self.assertTrue(recursive.advance(data, run, args))  # Parent bookkeeping.
                self.assertTrue(recursive.advance(data, run, args))  # Parent recheck.
            self.assertEqual(data["rounds_used"], 2)
            self.assertEqual(root["status"], "closed")

    def test_failed_child_generation_retries_in_same_round(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            child = {"id": "root-g1", "parent": "root", "depth": 1, "version": 0,
                     "status": "active", "phase": "sketch"}
            data = {"nodes": {"root-g1": child}, "rounds_used": 0}
            args = SimpleNamespace(max_rounds=1, model=None)
            with patch.object(recursive, "generate", side_effect=RuntimeError("failed")):
                with self.assertRaisesRegex(RuntimeError, "failed"):
                    recursive.advance(data, run, args)
            self.assertEqual(data["rounds_used"], 1)
            self.assertTrue(child["round_reserved"])
            with patch.object(recursive, "generate") as generate:
                self.assertTrue(recursive.advance(data, run, args))
                generate.assert_called_once()
            self.assertEqual(data["rounds_used"], 1)

    def test_resume_stuck_expands_unproved_and_incomplete_gaps(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            parent_dir = recursive.node_dir(run, "root-g1")
            (parent_dir / "v3.tex").write_text(
                sketch_with_gaps(r"\textbf{Gap 1.} Prove P.\medskip"
                                 r"\textbf{Gap 2.} Define and prove Q."), encoding="utf-8")
            verdict = {"gaps": [{"number": 1, "status": "unproved", "reason": "P"},
                                {"number": 2, "status": "incomplete", "reason": "Q"}],
                       "parent_closes": False, "parent_reason": "both open"}
            root = {"id": "root", "parent": None, "depth": 0, "status": "blocked",
                    "statement": "Target",
                    "children": {"1": "root-g1"}}
            child = {"id": "root-g1", "parent": "root", "parent_gap": 1,
                     "depth": 1, "status": "blocked", "children": {}, "version": 3,
                     "revisions": 2, "statement": "P and Q", "verdict": verdict}
            data = {"nodes": {"root": root, "root-g1": child}}
            args = SimpleNamespace(max_depth=None, max_nodes=None, max_revisions=2)
            self.assertTrue(recursive.resume_blocked(data, requested=True))
            self.assertEqual(root["status"], "waiting")
            self.assertTrue(recursive.advance(data, run, args))
            self.assertEqual(child["status"], "waiting")
            self.assertEqual(set(child["children"]), {"1", "2"})
            self.assertEqual(root["status"], "waiting")

    def test_depth_limited_leaf_reopens_when_limit_removed(self):
        verdict = {"gaps": [{"number": 1, "status": "unproved", "reason": "P"}]}
        leaf = {"id": "root-g1-g1", "parent": None, "depth": 2, "status": "blocked",
                "blocked_kind": "budget", "children": {}, "reason": "depth budget reached: P",
                "verdict": verdict}
        data = {"nodes": {leaf["id"]: leaf}}
        self.assertFalse(recursive.resume_blocked(data, max_depth=2))
        self.assertTrue(recursive.resume_blocked(data, max_depth=None))
        self.assertEqual(leaf["status"], "active")
        self.assertEqual(leaf["phase"], "expand")

    def test_node_budget_leaf_reopens_only_after_limit_increases(self):
        leaf = {"id": "root", "parent": None, "depth": 0, "status": "blocked",
                "blocked_kind": "budget", "children": {}, "reason": "node budget reached",
                "verdict": {"gaps": [{"number": 1, "status": "unproved", "reason": "P"}]}}
        data = {"nodes": {"root": leaf}}
        self.assertFalse(recursive.resume_blocked(data, max_nodes=1))
        self.assertTrue(recursive.resume_blocked(data, max_nodes=2))
        self.assertEqual((leaf["status"], leaf["phase"]), ("active", "expand"))

    def test_expand_then_recheck_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            root_dir = recursive.node_dir(run, "root")
            (root_dir / "v1.tex").write_text(
                sketch_with_gaps(r"\textbf{Gap 1.} Prove P from H."), encoding="utf-8")
            root = {"id": "root", "parent": None, "parent_gap": None, "depth": 0,
                    "statement": "Q", "version": 1, "revisions": 0, "status": "active", "phase": "review",
                    "children": {}, "reason": ""}
            data = {"nodes": {"root": root}, "source": "src/New/Main.lean",
                    "declaration": "New.grh"}
            args = SimpleNamespace(max_depth=1, max_nodes=2, max_revisions=0, model=None)
            first = {"gaps": [{"number": 1, "status": "unproved", "reason": "need proof"}],
                     "parent_closes": False, "parent_reason": "conditional"}
            last = {"gaps": [{"number": 1, "status": "proved", "reason": "child proof"}],
                    "parent_closes": True, "parent_reason": "checked"}

            def fake_generate(node, _run, _data, _model):
                node["version"] = 1
                node["phase"] = "review"
                child_dir = recursive.node_dir(run, node["id"])
                (child_dir / "v1.tex").write_text(sketch_with_gaps(""), encoding="utf-8")

            def fake_review(node, _run, nodes=None):
                if node["id"] == "root":
                    return last if nodes is not None else first
                return {"gaps": [], "parent_closes": True, "parent_reason": "proved P"}

            with patch.object(recursive, "generate", fake_generate), \
                 patch.object(recursive, "review", fake_review):
                for _ in range(5):
                    self.assertTrue(recursive.advance(data, run, args))
            self.assertEqual(root["status"], "closed")
            self.assertEqual(data["nodes"]["root-g1"]["status"], "closed")

    def test_repeated_ancestor_stops_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            folder = recursive.node_dir(run, "root")
            (folder / "v1.tex").write_text(
                sketch_with_gaps(r"\textbf{Gap 1.} Q"), encoding="utf-8")
            node = {"id": "root", "parent": None, "parent_gap": None, "depth": 0,
                    "statement": "Q", "version": 1, "revisions": 0, "status": "active", "phase": "review",
                    "children": {}, "reason": ""}
            verdict = {"gaps": [{"number": 1, "status": "unproved", "reason": "stuck"}],
                       "parent_closes": False, "parent_reason": "stuck"}
            args = SimpleNamespace(max_depth=2, max_nodes=3, max_revisions=0, model=None)
            with patch.object(recursive, "review", return_value=verdict):
                recursive.advance({"nodes": {"root": node}}, run, args)
            self.assertEqual(node["status"], "blocked")
            self.assertIn("ancestor", node["reason"])


if __name__ == "__main__":
    unittest.main()
