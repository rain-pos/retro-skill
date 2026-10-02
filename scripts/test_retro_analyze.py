#!/usr/bin/env python3
"""Synthetic-fixture tests for retro_analyze.py: python3 -m unittest scripts/test_retro_analyze.py (from the skill dir).

The fixture mimics the on-disk transcript format (one line per content block, usage repeated
per line, sub-agent journal with .meta.json, a workflow summary). If Claude Code changes the
format, these tests fail loudly instead of the analyzer printing zeros.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import retro_analyze as ra  # noqa: E402

SID = "11111111-2222-3333-4444-555555555555"


def rec(kind, ts, **kw):
    base = {"type": kind, "timestamp": ts, "sessionId": SID, "version": "2.1.284", "gitBranch": "main", "cwd": "/repo"}
    base.update(kw)
    return base


def assistant_lines(ts, mid, model, blocks, usage, effort="high"):
    """One JSONL line per block, each carrying the whole usage (the real format)."""
    return [rec("assistant", ts, effort=effort, message={"id": mid, "model": model, "usage": usage, "content": [b]}) for b in blocks]


def build_session(root):
    sdir = os.path.join(root, SID)
    os.makedirs(os.path.join(sdir, "subagents", "workflows", "wf_1"), exist_ok=True)
    os.makedirs(os.path.join(sdir, "workflows"), exist_ok=True)
    usage1 = {"input_tokens": 10, "output_tokens": 100, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 300,
              "cache_creation": {"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 200},
              "output_tokens_details": {"thinking_tokens": 40}}
    usage2 = {"input_tokens": 5, "output_tokens": 50, "cache_read_input_tokens": 2000, "cache_creation_input_tokens": 0}
    lines = [
        rec("attachment", "2026-01-01T10:00:00Z", attachment={"type": "language", "language": "Ukrainian"}),
        rec("attachment", "2026-01-01T10:00:00Z", attachment={"type": "hook_success", "hookName": "SessionStart:startup", "content": "x" * 400, "durationMs": "12"}),
        rec("user", "2026-01-01T10:00:01Z", origin={"kind": "human"}, message={"role": "user", "content": "Привіт, зроби ретро"}),
    ]
    lines += assistant_lines("2026-01-01T10:00:05Z", "m1", "claude-sonnet-5-5",
                             [{"type": "thinking", "thinking": "..."}, {"type": "text", "text": "Starting."},
                              {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/repo/a.md"}},
                              {"type": "tool_use", "id": "t2", "name": "Agent", "input": {"subagent_type": "Explore", "description": "look", "prompt": "p"}}],
                             usage1)
    lines += [
        rec("user", "2026-01-01T10:00:07Z", message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "A" * 4000}]}),
        rec("user", "2026-01-01T10:00:20Z", message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t2", "content": "done\n<usage>subagent_tokens: 1</usage>"}]},
            toolUseResult={"status": "completed"}),
        rec("user", "2026-01-01T10:00:21Z", message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t9", "content": "Permission to use Bash denied", "is_error": True}]},
            toolDenialKind="permission-rule"),
        rec("system", "2026-01-01T10:00:30Z", subtype="compact_boundary", compactMetadata={"trigger": "auto", "preTokens": 900000, "postTokens": 12000, "durationMs": 1000}),
        rec("user", "2026-01-01T10:05:00Z", message={"role": "user", "content": "<command-name>/legacy-flow</command-name><command-args>x</command-args>"}),
        rec("user", "2026-01-01T10:05:01Z", isMeta=True, message={"role": "user", "content": [{"type": "text", "text": "Base directory for this skill: /repo/.claude/skills/legacy-flow\n\nbody"}]}),
        rec("user", "2026-01-01T10:05:02Z", message={"role": "user", "content": "<command-name>/compact</command-name>"}),
    ]
    lines += assistant_lines("2026-01-01T10:05:10Z", "m2", "claude-opus-5-5",
                             [{"type": "text", "text": "ok?"}, {"type": "tool_use", "id": "t3", "name": "Skill", "input": {"skill": "kb:context", "args": "AREA=x"}}],
                             usage2, effort="medium")
    lines += [
        rec("user", "2026-01-01T10:05:12Z", message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t3", "content": "Launching skill: kb:context"}]}),
        rec("attachment", "2026-01-01T10:06:00Z", attachment={"type": "queued_command", "prompt": "mid-turn note", "origin": {"kind": "human"}}),
        rec("user", "2026-01-01T12:00:00Z", origin={"kind": "human"}, message={"role": "user", "content": "yes"}),
        rec("user", "2026-01-01T12:00:01Z", origin={"kind": "human"}, message={"role": "user", "content": "/retro"}),
        "{not json",
    ]
    with open(os.path.join(root, SID + ".jsonl"), "w") as fh:
        for l in lines:
            fh.write((json.dumps(l) if isinstance(l, dict) else l) + "\n")
    # sub-agent journal linked through toolUseId t2, plus a journal that replays main (must be skipped)
    ag = assistant_lines("2026-01-01T10:00:08Z", "a1", "claude-haiku-4-5", [{"type": "tool_use", "id": "t7", "name": "Read", "input": {"file_path": "/repo/a.md"}}],
                         {"input_tokens": 1, "output_tokens": 20, "cache_read_input_tokens": 500, "cache_creation_input_tokens": 50})
    ag += [rec("user", "2026-01-01T10:00:18Z", isSidechain=True, message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t7", "content": "A" * 4000}]})]
    with open(os.path.join(sdir, "subagents", "agent-abc.jsonl"), "w") as fh:
        fh.write("\n".join(json.dumps(l) for l in ag) + "\n")
    with open(os.path.join(sdir, "subagents", "agent-abc.meta.json"), "w") as fh:
        json.dump({"agentType": "Explore", "description": "look", "toolUseId": "t2"}, fh)
    with open(os.path.join(sdir, "subagents", "agent-acompact-1.jsonl"), "w") as fh:
        fh.write(json.dumps(lines[3]) + "\n")
    wf = assistant_lines("2026-01-01T10:01:00Z", "w1", "claude-sonnet-5-5", [{"type": "text", "text": "wf"}],
                         {"input_tokens": 1, "output_tokens": 10, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 0})
    with open(os.path.join(sdir, "subagents", "workflows", "wf_1", "agent-w1.jsonl"), "w") as fh:
        fh.write("\n".join(json.dumps(l) for l in wf) + "\n")
    with open(os.path.join(sdir, "subagents", "workflows", "wf_1", "agent-w1.meta.json"), "w") as fh:
        json.dump({"agentType": "workflow-subagent"}, fh)
    with open(os.path.join(sdir, "workflows", "wf_1.json"), "w") as fh:
        json.dump({"runId": "wf_1", "workflowName": "demo", "status": "completed", "durationMs": 5000, "agentCount": 1, "totalTokens": 111, "totalToolCalls": 0,
               "defaultModel": "sonnet", "workflowProgress": [{"type": "workflow_phase", "index": 0, "title": "P"},
                                                                {"type": "workflow_agent", "phaseTitle": "P", "agentId": "w1", "model": "sonnet", "state": "done", "tokens": 111, "toolCalls": 0, "durationMs": 5000, "attempt": 1}]}, fh)
    return os.path.join(root, SID + ".jsonl")


class AnalyzeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.transcript = build_session(cls.tmp.name)
        prices, verified = ra.load_prices(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prices.json"))
        cls.r, cls.j = ra.analyze(cls.transcript, prices, verified, 600, exclude_last_turn=True)

    def test_usage_deduplicated_per_message(self):
        main = self.r["totals"]["main"]
        self.assertEqual(main["api_calls"], 2)
        self.assertEqual(main["output"], 150)           # 100 + 50, not 4x100 + 2x50
        self.assertEqual(main["cache_read"], 3000)
        self.assertEqual((main["cache_write_5m"], main["cache_write_1h"]), (100, 200))
        self.assertEqual(main["thinking"], 40)

    def test_exclude_last_turn_drops_only_the_retro_prompt(self):
        self.assertEqual(self.r["session"]["human_turns"], 3)     # 2 prose prompts + the typed /legacy-flow command
        self.assertEqual(self.r["turns"][1]["prompt"], "/legacy-flow x")
        self.assertTrue(self.r["session"]["excluded_last_turn"])

    def test_agents_linked_and_replay_journal_skipped(self):
        types = sorted(a["type"] for a in self.r["agents"])
        self.assertEqual(types, ["Explore", "workflow-subagent"])
        self.assertEqual(self.r["session"]["skipped_journals"], {"acompact": 1})
        self.assertEqual(self.r["totals"]["agents"]["output"], 30)
        explore = next(a for a in self.r["agents"] if a["type"] == "Explore")
        self.assertEqual((explore["parent"], explore["depth"], explore["model"]), ("main", 1, "claude-haiku-4-5"))

    def test_skill_runs_and_inner_calls(self):
        segs = self.r["segments"]
        self.assertEqual([s["skill"] for s in segs], ["legacy-flow"])      # /compact is not a skill run
        self.assertEqual(segs[0]["skills_used_inside"], {"kb:context": 1})

    def test_compaction_denial_interjection_language(self):
        self.assertEqual(self.r["compactions"][0]["pre_tokens"], 900000)
        self.assertEqual(self.r["friction"]["denials"], {"permission-rule": 1})
        self.assertEqual(self.r["session"]["human_interjections"], 1)
        self.assertEqual(self.r["session"]["language_setting"], "Ukrainian")
        self.assertEqual(self.r["session"]["language_guess"], "uk")
        self.assertEqual(self.r["session"]["bad_lines"], 1)

    def test_duplicate_reads_and_workflow(self):
        dup = self.r["duplicate_reads"][0]
        self.assertEqual((dup["path"], dup["readers"]), ("/repo/a.md", 2))
        self.assertEqual(self.r["workflows"][0]["phases"][0]["agents"], 1)

    def test_costs_and_render(self):
        self.assertIsNotNone(self.r["totals"]["all"]["cost_usd"])
        text = ra.render(self.r)
        self.assertIn("## totals", text)
        self.assertIn("legacy-flow", text)


if __name__ == "__main__":
    unittest.main()


class BehaviourTests(unittest.TestCase):
    """Cases for behaviour added after review: overlap clusters, resumes fallback, Bash reads, long tools, model-invoked skills."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def write_session(self, lines, agents=()):
        sdir = os.path.join(self.root, SID)
        os.makedirs(os.path.join(sdir, "subagents"), exist_ok=True)
        with open(os.path.join(self.root, SID + ".jsonl"), "w") as fh:
            fh.write("\n".join(json.dumps(l) for l in lines) + "\n")
        for name, meta, alines in agents:
            with open(os.path.join(sdir, "subagents", f"agent-{name}.jsonl"), "w") as fh:
                fh.write("\n".join(json.dumps(l) for l in alines) + "\n")
            with open(os.path.join(sdir, "subagents", f"agent-{name}.meta.json"), "w") as fh:
                json.dump(meta, fh)
        return ra.analyze(os.path.join(self.root, SID + ".jsonl"), None, None, 600, exclude_last_turn=False)[0]

    @staticmethod
    def usage(out=10):
        return {"input_tokens": 1, "output_tokens": out, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 0}

    def test_overlapping_agents_from_separate_messages_form_one_cluster(self):
        lines = [rec("user", "2026-01-01T10:00:00Z", origin={"kind": "human"}, message={"content": "go"})]
        lines += assistant_lines("2026-01-01T10:00:05Z", "m1", "claude-opus-5-5", [{"type": "tool_use", "id": "t1", "name": "Agent", "input": {"subagent_type": "Explore", "description": "a", "prompt": "p"}}], self.usage())
        lines += assistant_lines("2026-01-01T10:00:20Z", "m2", "claude-opus-5-5", [{"type": "tool_use", "id": "t2", "name": "Agent", "input": {"subagent_type": "Explore", "description": "b", "prompt": "p"}}], self.usage())
        # nested agent spawned by agent A must not count as parallel to A
        a = assistant_lines("2026-01-01T10:00:06Z", "a1", "claude-sonnet-5-5", [{"type": "tool_use", "id": "t3", "name": "Agent", "input": {"subagent_type": "Explore", "description": "c", "prompt": "p"}}], self.usage())
        a += [rec("user", "2026-01-01T10:03:00Z", message={"content": [{"type": "tool_result", "tool_use_id": "t3", "content": "ok"}]})]
        b = assistant_lines("2026-01-01T10:00:21Z", "b1", "claude-sonnet-5-5", [{"type": "text", "text": "x"}], self.usage()) + [rec("user", "2026-01-01T10:02:00Z", message={"content": [{"type": "tool_result", "tool_use_id": "zz", "content": "ok"}]})]
        c = assistant_lines("2026-01-01T10:00:07Z", "c1", "claude-haiku-4-5", [{"type": "text", "text": "x"}], self.usage()) + [rec("user", "2026-01-01T10:02:30Z", message={"content": [{"type": "tool_result", "tool_use_id": "zz", "content": "ok"}]})]
        r = self.write_session(lines, [("a", {"agentType": "Explore", "toolUseId": "t1"}, a), ("b", {"agentType": "Explore", "toolUseId": "t2"}, b), ("c", {"agentType": "Explore", "toolUseId": "t3"}, c)])
        self.assertEqual(len(r["agent_batches"]), 1)
        self.assertEqual(sorted(r["agent_batches"][0]["types"]), ["Explore", "Explore"])   # depth-1 agents only
        self.assertEqual(r["agent_batches"][0]["launch_messages"], 2)
        self.assertGreater(r["agent_batches"][0]["parallelism"], 1.0)
        self.assertEqual({a_["depth"] for a_ in r["agents"]}, {1, 2})

    def test_resumes_fallback_without_hooks_and_bash_reads(self):
        lines = [rec("user", "2026-01-01T10:00:00Z", origin={"kind": "human"}, message={"content": "one"})]
        lines += assistant_lines("2026-01-01T10:00:05Z", "m1", "claude-opus-5-5", [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "cd sub && sed -n 1,40p notes.md"}}], self.usage())
        lines += [rec("user", "2026-01-01T10:00:06Z", message={"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "n" * 800}]})]
        lines += assistant_lines("2026-01-01T10:00:08Z", "m2", "claude-opus-5-5", [{"type": "tool_use", "id": "t2", "name": "Agent", "input": {"subagent_type": "Explore", "description": "a", "prompt": "p"}}], self.usage())
        lines += [rec("user", "2026-01-01T10:00:09Z", message={"content": [{"type": "tool_result", "tool_use_id": "t2", "content": "ok"}]})]
        lines += [rec("user", "2026-01-01T13:00:00Z", origin={"kind": "human"}, message={"content": "two, after a 3h break"})]
        agent = assistant_lines("2026-01-01T10:00:08Z", "a1", "claude-sonnet-5-5", [{"type": "tool_use", "id": "t9", "name": "Read", "input": {"file_path": "/repo/sub/notes.md"}}], self.usage())
        agent += [rec("user", "2026-01-01T10:00:09Z", message={"content": [{"type": "tool_result", "tool_use_id": "t9", "content": "n" * 800}]})]
        r = self.write_session(lines, [("a", {"agentType": "Explore", "toolUseId": "t2"}, agent)])
        self.assertEqual(r["session"]["resumes"], 1)
        self.assertIn("gaps", r["session"]["resumes_source"])
        self.assertEqual(r["duplicate_reads"][0]["path"], "/repo/sub/notes.md")   # relative Bash path resolved against cwd + cd
        self.assertEqual((r["duplicate_reads"][0]["readers"], r["duplicate_reads"][0]["reads"]), (2, 2))

    def test_long_tool_call_counts_as_active_but_plan_wait_does_not(self):
        lines = [rec("user", "2026-01-01T10:00:00Z", origin={"kind": "human"}, message={"content": "run tests"})]
        lines += assistant_lines("2026-01-01T10:00:05Z", "m1", "claude-opus-5-5", [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "make test"}}], self.usage())
        lines += [rec("user", "2026-01-01T10:20:05Z", message={"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]})]   # 20 min > gap cap
        lines += assistant_lines("2026-01-01T10:20:10Z", "m2", "claude-opus-5-5", [{"type": "tool_use", "id": "t2", "name": "ExitPlanMode", "input": {}}], self.usage())
        lines += [rec("user", "2026-01-01T10:50:10Z", message={"content": [{"type": "tool_result", "tool_use_id": "t2", "content": "approved"}]})]   # 30 min human wait
        r = self.write_session(lines)
        self.assertGreaterEqual(r["session"]["active_s"], 1200)
        self.assertLess(r["session"]["active_s"], 1200 + 120)

    def test_model_invoked_skill_before_any_command_opens_a_run(self):
        lines = [rec("user", "2026-01-01T10:00:00Z", origin={"kind": "human"}, message={"content": "please run code review"})]
        lines += assistant_lines("2026-01-01T10:00:05Z", "m1", "claude-opus-5-5", [{"type": "tool_use", "id": "t1", "name": "Skill", "input": {"skill": "code-review", "args": ""}}], self.usage())
        lines += [rec("user", "2026-01-01T10:00:06Z", message={"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "Launching skill: code-review"}]})]
        r = self.write_session(lines)
        self.assertEqual([(g["skill"], g["via"]) for g in r["segments"]], [("code-review", "model")])
        self.assertEqual(r["segments"][0]["skills_used_inside"], {})

    def test_agent_active_time_and_clusters_ignore_a_long_pause(self):
        """Agent A: 5 min of work, a 10h pause (SendMessage continuation), 5 min more. Agent B runs inside the pause."""
        lines = [rec("user", "2026-01-01T09:00:00Z", origin={"kind": "human"}, message={"content": "go"})]
        lines += assistant_lines("2026-01-01T09:00:05Z", "m1", "claude-opus-5-5", [{"type": "tool_use", "id": "tA", "name": "Agent", "input": {"subagent_type": "general-purpose", "description": "reviewer", "prompt": "p", "run_in_background": True}}], self.usage())
        lines += assistant_lines("2026-01-01T12:00:00Z", "m2", "claude-opus-5-5", [{"type": "tool_use", "id": "tB", "name": "Agent", "input": {"subagent_type": "Explore", "description": "check", "prompt": "p"}}], self.usage())
        lines += [rec("user", "2026-01-01T12:03:00Z", message={"content": [{"type": "tool_result", "tool_use_id": "tB", "content": "ok"}]})]
        a = []
        for n, ts in enumerate(("09:00:00", "09:02:00", "09:05:00", "19:05:00", "19:07:00", "19:10:00")):
            a += assistant_lines(f"2026-01-01T{ts}Z", f"a{n}", "claude-opus-5-5", [{"type": "text", "text": "round"}], self.usage())
        b = assistant_lines("2026-01-01T12:00:10Z", "b0", "claude-sonnet-5-5", [{"type": "text", "text": "x"}], self.usage())
        b += assistant_lines("2026-01-01T12:02:30Z", "b1", "claude-sonnet-5-5", [{"type": "text", "text": "y"}], self.usage())
        r = self.write_session(lines, [("a", {"agentType": "general-purpose", "toolUseId": "tA"}, a), ("b", {"agentType": "Explore", "toolUseId": "tB"}, b)])
        rows = {x["agent_id"]: x for x in r["agents"]}
        self.assertEqual(rows["a"]["active_s"], 600)                      # 5 + 5 minutes, not the 10h10m span
        self.assertEqual(rows["a"]["span_s"], 36600)
        self.assertEqual([sorted(bt["agents"]) for bt in r["agent_batches"]], [["a"], ["b"], ["a"]])   # B is not clustered with A's pause
        self.assertEqual(r["agent_batches"][1]["parallelism"], 1.0)

    def test_skill_run_ends_at_a_pause_longer_than_an_hour(self):
        lines = [rec("user", "2026-01-01T10:00:00Z", origin={"kind": "human"}, message={"content": "please run code review"})]
        lines += assistant_lines("2026-01-01T10:00:05Z", "m1", "claude-opus-5-5", [{"type": "tool_use", "id": "t1", "name": "Skill", "input": {"skill": "code-review", "args": ""}}], self.usage())
        lines += [rec("user", "2026-01-01T10:00:06Z", message={"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "Launching skill: code-review"}]})]
        lines += assistant_lines("2026-01-01T10:00:10Z", "m2", "claude-opus-5-5", [{"type": "text", "text": "review done"}], self.usage())
        lines += [rec("user", "2026-01-01T10:20:00Z", origin={"kind": "human"}, message={"content": "thanks, next question"})]
        lines += assistant_lines("2026-01-01T10:20:05Z", "m3", "claude-opus-5-5", [{"type": "text", "text": "answer"}], self.usage())
        lines += [rec("user", "2026-01-01T14:00:00Z", origin={"kind": "human"}, message={"content": "unrelated work after lunch"})]
        lines += assistant_lines("2026-01-01T14:00:05Z", "m4", "claude-opus-5-5", [{"type": "text", "text": "ok"}], self.usage())
        r = self.write_session(lines)
        seg = r["segments"][0]
        self.assertEqual(seg["ended"], "2026-01-01T14:00:00Z")       # the prompt after the > 1h pause, not the session end
        self.assertEqual((seg["api_calls"], seg["human_turns"]), (3, 2))

    def test_cache_rebuild_cause_follows_the_session_ttl(self):
        def big_write(ts, mid, ttl):
            cc = {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0}
            cc[f"ephemeral_{ttl}_input_tokens"] = 80_000
            u = dict(self.usage(), cache_creation_input_tokens=80_000, cache_creation=cc)
            return assistant_lines(ts, mid, "claude-opus-5-5", [{"type": "text", "text": "x"}], u)
        lines = [rec("user", "2026-01-01T10:00:00Z", origin={"kind": "human"}, message={"content": "go"})]
        lines += big_write("2026-01-01T10:00:05Z", "m1", "1h")
        lines += big_write("2026-01-01T10:40:00Z", "m2", "1h")      # 40 min: inside the 1h TTL
        lines += big_write("2026-01-01T12:00:00Z", "m3", "1h")      # 80 min: past it
        r = self.write_session(lines)
        self.assertEqual([e["cause"] for e in r["cache_rebuilds"]["events"]], ["other (edited prefix, compaction, new context)", "gap > 1h TTL"])
        lines = [rec("user", "2026-01-01T10:00:00Z", origin={"kind": "human"}, message={"content": "go"})]
        lines += big_write("2026-01-01T10:00:05Z", "m1", "5m") + big_write("2026-01-01T10:40:00Z", "m2", "5m")
        r = self.write_session(lines)
        self.assertEqual([e["cause"] for e in r["cache_rebuilds"]["events"]], ["gap > 5m TTL"])   # the same 40 min gap on a 5m session

    def test_gitignore_warning_and_format_warnings(self):
        self.assertIsNone(ra.gitignore_warning(self.root, self.root))   # not a git repo: no warning
        lines = [rec("user", "2026-01-01T10:00:00Z", origin={"kind": "human"}, message={"content": "x"})]
        lines += [rec("assistant", "2026-01-01T10:00:05Z", message={"id": "m1", "model": "claude-opus-5-5", "content": [{"type": "text", "text": "no usage here"}]})]
        r = self.write_session(lines)
        self.assertEqual(r["totals"]["main"]["api_calls"], 0)   # usage-less messages are not counted as api calls

    def test_format_warnings(self):
        lines = [rec("user", "2026-01-01T10:00:00Z", origin={"kind": "human"}, message={"content": "x"})]
        lines += [rec("assistant", "2026-01-01T10:00:05Z", message={"id": m, "model": "claude-opus-5-5", "content": [{"type": "text", "text": "no usage"}]}) for m in ("m1", "m3")]
        lines += assistant_lines("2026-01-01T10:00:09Z", "m2", "claude-opus-5-5", [{"type": "text", "text": "fast"}], dict(self.usage(), speed="fast"))   # 2 of 3 without usage
        lines += [rec("system", "2026-01-01T10:00:10Z", subtype="ai-title", content="t")] * 60   # a long transcript without prompts
        path = os.path.join(self.root, SID + ".jsonl")
        with open(path, "w") as fh:
            fh.write("\n".join(json.dumps(l) for l in lines) + "\n")
        j = ra.Journal(path)
        j.human_turns = []
        w = ra.format_warnings(j, "2026-01-01", today=ra.datetime(2026, 6, 1))
        self.assertEqual(len(w), 4)
        self.assertTrue(w[0].startswith("warning: prices.json was verified 151 days"))
        self.assertIn("fast mode", w[1])
        self.assertIn("carry no usage", w[2])
        self.assertIn("no human prompts", w[3])
        fresh = ra.format_warnings(j, "2026-05-31", today=ra.datetime(2026, 6, 1))
        self.assertEqual(len(fresh), 3)
        self.assertNotIn("prices.json", " ".join(fresh))

    def test_gitignore_warning_positive(self):
        import subprocess
        root = os.path.join(self.root, "repo"); os.makedirs(root)
        subprocess.run(["git", "init", "-q", root], check=True)
        with open(os.path.join(root, ".gitignore"), "w") as fh:
            fh.write("other/\n")
        out = os.path.join(root, "tasks", "retros", ".data"); os.makedirs(out)
        self.assertIsNotNone(ra.gitignore_warning(out, root))
        with open(os.path.join(root, ".gitignore"), "a") as fh:
            fh.write("tasks/retros/\n")
        self.assertIsNone(ra.gitignore_warning(out, root))

    def test_typed_skill_command_is_a_turn_with_its_brief(self):
        brief = "investigate the checkout bug " * 20
        lines = [rec("user", "2026-01-01T10:00:00Z", origin={"kind": "human"}, message={"content": "hi, check MCP"})]
        lines += assistant_lines("2026-01-01T10:00:03Z", "m0", "claude-opus-5-5", [{"type": "text", "text": "ok"}], self.usage())
        lines += [rec("user", "2026-01-01T10:30:00Z", message={"content": f"<command-name>/bug-investigator</command-name><command-args>{brief}</command-args>"}),
                  rec("user", "2026-01-01T10:30:01Z", isMeta=True, message={"content": [{"type": "text", "text": "Base directory for this skill: /x/bug-investigator\n\nbody"}]})]
        lines += assistant_lines("2026-01-01T10:30:10Z", "m1", "claude-opus-5-5", [{"type": "text", "text": "Which store?"}], self.usage())
        lines += [rec("user", "2026-01-01T10:33:00Z", origin={"kind": "human"}, message={"content": "store 5"})]
        lines += assistant_lines("2026-01-01T10:33:05Z", "m2", "claude-opus-5-5", [{"type": "text", "text": "done"}], self.usage())
        path = os.path.join(self.root, SID + ".jsonl")
        with open(path, "w") as fh:
            fh.write("\n".join(json.dumps(l) for l in lines) + "\n")
        r, main = ra.analyze(path, None, None, 600, exclude_last_turn=False)
        self.assertEqual([t["n"] for t in r["turns"]], [1, 2, 3])
        self.assertEqual(r["turns"][1]["prompt"][:17], "/bug-investigator")
        self.assertEqual(r["segments"][0]["invoking_turn"]["n"], 2)
        self.assertEqual(r["segments"][0]["started"], "2026-01-01T10:30:00Z")
        self.assertEqual(r["session"]["questions_to_user"], 1)
        self.assertIn("investigate the checkout bug", ra.narrative(main, r))

    def test_language_other_for_non_ascii_latin(self):
        self.assertEqual(ra.detect_language("Bitte prüfe die Änderung im Modul"), "other")
        self.assertEqual(ra.detect_language("please check this — it's fine…"), "en")
        self.assertEqual(ra.detect_language("перевір це"), "uk")

    def test_main_smoke(self):
        """main() end to end: argument parsing, warnings, file output, exit code."""
        import contextlib, io
        transcript = build_session(self.root)
        out = os.path.join(self.root, "out")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = ra.main(["--session", SID[:8], "--sessions-dir", self.root, "--project-dir", self.root, "--out", out, "--exclude-last-turn"])
        self.assertEqual(rc, 0)
        self.assertIn("## totals", buf.getvalue())
        self.assertTrue(os.path.exists(os.path.join(out, SID[:8] + ".json")))
        self.assertTrue(os.path.exists(os.path.join(out, SID[:8] + "-narrative.md")))

