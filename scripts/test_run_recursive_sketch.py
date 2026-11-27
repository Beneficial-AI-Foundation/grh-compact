"""Offline checks for recursive scheduling and upward closure."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import run_recursive_sketch as recursive


class RecursiveSketchTest(unittest.TestCase):
    def test_initial_review_does_not_consume_gap_round(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            recursive.node_dir(run, "root")
            node = {"id": "root", "parent": None, "parent_gap": None, "depth": 0,
                    "statement": "Q", "version": 1, "revisions": 0, "status": "review",
                    "children": {}, "reason": ""}
            data = {"nodes": {"root": node}, "rounds_used": 0}
            args = SimpleNamespace(max_depth=0, max_nodes=1, max_revisions=0,
                                   max_rounds=0, model=None)
            with patch.object(recursive, "review", return_value={
                    "gaps": [], "parent_closes": True, "parent_reason": "proved"}):
                (run / "nodes/root/v1.tex").write_text(r"\end{document}", encoding="utf-8")
                self.assertTrue(recursive.advance(data, run, args))
            self.assertEqual(data["rounds_used"], 0)
            self.assertEqual(node["status"], "closed")

    def test_one_round_covers_child_generation_review_and_parent_recheck(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            root_dir = recursive.node_dir(run, "root")
            (root_dir / "v1.tex").write_text(
                r"\textbf{Gap 1.} Prove P.\medskip\textbf{Gap 2.} Prove R.\medskip\end{document}",
                encoding="utf-8")
            root = {"id": "root", "parent": None, "parent_gap": None, "depth": 0,
                    "statement": "Q", "version": 1, "revisions": 0, "status": "review",
                    "children": {}, "reason": ""}
            data = {"nodes": {"root": root}, "source": "src/New/Main.lean",
                    "declaration": "New.grh", "rounds_used": 0}
            args = SimpleNamespace(max_depth=1, max_nodes=3, max_revisions=0,
                                   max_rounds=1, model=None)

            def fake_generate(node, _run, _data, _model):
                node["version"] = 1
                node["status"] = "review"
                (recursive.node_dir(run, node["id"]) / "v1.tex").write_text(
                    r"\end{document}", encoding="utf-8")

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
                self.assertEqual(data["nodes"]["root-g2"]["status"], "sketch")
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
                     "status": "sketch"}
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

    def test_expand_then_recheck_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            root_dir = recursive.node_dir(run, "root")
            (root_dir / "v1.tex").write_text(
                r"\textbf{Gap 1.} Prove P from H.\medskip\end{document}", encoding="utf-8")
            root = {"id": "root", "parent": None, "parent_gap": None, "depth": 0,
                    "statement": "Q", "version": 1, "revisions": 0, "status": "review",
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
                node["status"] = "review"
                child_dir = recursive.node_dir(run, node["id"])
                (child_dir / "v1.tex").write_text(r"\end{document}", encoding="utf-8")

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
                r"\textbf{Gap 1.} Q\medskip\end{document}", encoding="utf-8")
            node = {"id": "root", "parent": None, "parent_gap": None, "depth": 0,
                    "statement": "Q", "version": 1, "revisions": 0, "status": "review",
                    "children": {}, "reason": ""}
            verdict = {"gaps": [{"number": 1, "status": "unproved", "reason": "stuck"}],
                       "parent_closes": False, "parent_reason": "stuck"}
            args = SimpleNamespace(max_depth=2, max_nodes=3, max_revisions=0, model=None)
            with patch.object(recursive, "review", return_value=verdict):
                recursive.advance({"nodes": {"root": node}}, run, args)
            self.assertEqual(node["status"], "stuck")
            self.assertIn("ancestor", node["reason"])


if __name__ == "__main__":
    unittest.main()
