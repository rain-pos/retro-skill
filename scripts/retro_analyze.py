#!/usr/bin/env python3
"""Deterministic metrics for a Claude Code session retrospective.

Reads the on-disk JSONL transcript of one session (plus its sub-agent journals
and Workflow runs), prints a compact text summary and, with ``--out DIR``, writes
``<id8>.json`` (full data) and ``<id8>-narrative.md`` (human prompts, assistant
replies before each prompt, agent hand-backs, questions). Without ``--out`` those two files
go to ``<project>/<artifacts directory from project.md>/.data/``. Reads transcripts only;
writes nothing else. Stdlib only, Python 3.9+.

Usage:
  retro_analyze.py --session <id-prefix> [--project-dir DIR] [--out DIR]
                   [--prices FILE] [--project-md FILE] [--gap-cap SEC]
                   [--exclude-last-turn] [--json]

Transcript facts this parser relies on:
  * One JSONL line per content block; every line of an assistant message carries
    the message's whole ``usage`` -> usage is de-duplicated by ``message.id``.
  * Sub-agent journals: ``<session>/subagents/**/agent-*.jsonl`` with a sibling
    ``.meta.json`` (agentType, description, toolUseId). Workflow agents live under
    ``subagents/workflows/<runId>/``; the run summary is ``<session>/workflows/<runId>.json``.
    ``agent-acompact-*`` / ``agent-aside_question-*`` journals replay the main
    conversation and are excluded from agent totals.
  * Sessions can span days and resume; wall-clock is not active time.
  * All timestamps are UTC.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

CHARS_PER_TOKEN = 4
HUMAN_WAIT_TOOLS = ("AskUserQuestion", "ExitPlanMode")   # the tool call waits for the human, not the model
COMMAND_SKILL_WINDOW_S = 120   # a typed command counts as a skill run if its body loads within this window
NON_AGENT_JOURNAL_PREFIXES = ("agent-acompact-", "agent-aside_question-")
NON_PROMPT_PREFIXES = ("<local-command-stdout>", "<local-command-stderr>", "[Request interrupted by user")
KNOWN_ATTACHMENTS = {
    "queued_command", "prompt_snapshot", "skill_listing", "language", "command_permissions", "edited_text_file",
    "file", "compact_file_reference", "date", "date_change", "environment", "model", "instructions",
    "session_context", "auto_mode", "total_tokens_reminder", "task_reminder", "batching_reminder_sent",
    "silent_turn_reminder", "deferred_tools_delta", "deferred_tools_record", "agent_listing_delta",
    "mcp_instructions_delta", "remote_session_change", "async_hook_response", "nested_memory", "invoked_skills",
    "workflow_keyword_request", "credential_org",
}
KNOWN_OTHER_RECORDS = {"queue-operation"}
IGNORED_RECORD_TYPES = {
    "last-prompt", "mode", "permission-mode", "custom-title", "agent-name",
    "atis-latch", "file-history-snapshot", "file-history-delta",
}
ZERO_ROW = {"api_calls": 0, "input": 0, "output": 0, "thinking": 0, "thinking_known": 0,
            "cache_read": 0, "cache_write_5m": 0, "cache_write_1h": 0}
COMMAND_RE = re.compile(r"<command-name>(.*?)</command-name>", re.S)
COMMAND_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.S)
SKILL_BODY_RE = re.compile(r"^Base directory for this skill: (.+?)\s*$", re.M)
REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
BASH_READ_RE = re.compile(r"(?:^|[;&|]\s*)(?:cat|head|tail|sed\s+-n\s+\S+)\s+(?:-[a-zA-Z0-9]+\s+)*([^\s;&|<>]+\.[A-Za-z0-9]{1,6})(?=$|[\s;&|<>)])")
CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")
UK_LETTERS_RE = re.compile(r"[іїєґІЇЄҐ]")
RU_LETTERS_RE = re.compile(r"[ыэъЫЭЪ]")


# ---------------------------------------------------------------- helpers

def parse_ts(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def short_ts(s):
    return s[5:16] if s else "?"


def num(x):
    try:
        return int(float(x or 0))
    except (TypeError, ValueError):
        return 0


def content_blocks(message):
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return []


def text_of(blocks):
    return "\n".join(str(b.get("text", "")) for b in blocks if b.get("type") == "text")


def block_text(block, limit):
    content = block.get("content")
    if isinstance(content, str):
        return content[:limit]
    if isinstance(content, list):
        return "\n".join(str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in content)[:limit]
    return ""


def block_len(block):
    content = block.get("content")
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(str(b.get("text", ""))) if isinstance(b, dict) else len(str(b)) for b in content)
    return 0


def fmt_tokens(n):
    if n is None:
        return "n/a"
    n = int(n)
    if n >= 999_500:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def fmt_secs(s):
    if s is None:
        return "n/a"
    s = int(s)
    if s >= 3600:
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"
    if s >= 60:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s}s"


def fmt_cost(c):
    return f"${c:.2f}" if c is not None else "n/a"


def cell(text):
    return str(text or "").replace("|", "\\|").replace("\n", " ")


def fmt_hit(h):
    return f"{h * 100:.0f}%" if h is not None else "n/a"


def slug_for(project_dir):
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(project_dir))


def detect_language(text):
    text = text or ""
    if not CYRILLIC_RE.search(text):
        return "en" if not re.search(r"[^\x00-\x7F]", re.sub(r"[\u2000-\u2BFF\u3000-\u303F\uFE00-\uFFFF\U0001F000-\U0001FFFF—–…«»„“”‘’·•]", "", text)) else "other"
    if UK_LETTERS_RE.search(text):
        return "uk"
    if RU_LETTERS_RE.search(text):
        return "ru"
    return "cyrillic"


def merge_intervals(intervals):
    """Sorted, non-overlapping union of (start, end) datetime intervals."""
    merged = []
    for s, e in sorted(i for i in intervals if i[0] and i[1] and i[1] >= i[0]):
        if merged and s <= merged[-1][1]:
            if e > merged[-1][1]:
                merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return merged


def union_seconds(intervals):
    """Total length of the union of (start, end) datetime intervals."""
    total = 0.0
    cur_s = cur_e = None
    for s, e in sorted(i for i in intervals if i[0] and i[1] and i[1] >= i[0]):
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += (cur_e - cur_s).total_seconds()
            cur_s, cur_e = s, e
        elif e > cur_e:
            cur_e = e
    if cur_e is not None:
        total += (cur_e - cur_s).total_seconds()
    return total


# ---------------------------------------------------------------- journal parsing

class Journal:
    """One JSONL file (main transcript or sub-agent journal), aggregated."""

    def __init__(self, path):
        self.path = path
        self.messages = {}          # message.id -> {model, effort, usage, ts_first, ts_last}
        self.tool_uses = {}         # tool_use.id -> {name, ts, msg_id, summary, input}
        self.tool_results = {}      # tool_use.id -> {ts, is_error, chars, denial, persisted, text}
        self.records = []           # (ts, kind) kind in assistant|tool_result|human|other
        self.human_turns = []       # {ts, text, full, uuid, chars, before}
        self.prompt_ts = []         # human prompts + typed skill commands (turn starters)
        self.skill_events = []      # {ts, kind: command, name, args}: user-typed skill runs (segment starters)
        self.skill_calls = []       # {ts, name, args}: Skill tool calls made by the model (listed inside a run)
        self._pending_command = None
        self.unknown_types = Counter()   # record / attachment types this parser does not handle
        self.skill_loads = []       # {ts, dir, chars}
        self.handbacks = []         # {ts, kind: peer|notification|agent_result, text, label}
        self.interjections = []     # {ts, text}: user messages delivered mid-turn (queued_command attachments)
        self.compactions = []
        self.compact_summaries = [] # {ts, text, chars}
        self.api_errors = []
        self.stop_hooks = []        # {ts, duration_ms, errors}
        self.hook_events = []       # {ts, name, chars, error, duration_ms, tool_use_id}
        self.reminder_chars = 0
        self.system_prompt_chars = None
        self.skill_listing_chars = None
        self.language_setting = None
        self.model_switch_commands = []
        self.versions = set()
        self.branches = set()
        self.cwd = None
        self.cost_state = None
        self.ts_first = None
        self.ts_last = None
        self.lines = 0
        self.bad_lines = 0
        self._last_assistant_text = ""
        self._parse()

    # -- parsing ---------------------------------------------------------

    def _touch(self, ts):
        if not ts:
            return
        if self.ts_first is None or ts < self.ts_first:
            self.ts_first = ts
        if self.ts_last is None or ts > self.ts_last:
            self.ts_last = ts

    def _parse(self):
        with open(self.path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                self.lines += 1
                try:
                    rec = json.loads(line)
                except ValueError:
                    self.bad_lines += 1
                    continue
                if not isinstance(rec, dict):
                    self.bad_lines += 1
                    continue
                rtype = rec.get("type")
                if rtype in IGNORED_RECORD_TYPES:
                    continue
                ts = parse_ts(rec.get("timestamp"))
                self._touch(ts)
                if rec.get("version"):
                    self.versions.add(str(rec["version"]))
                if rec.get("gitBranch"):
                    self.branches.add(str(rec["gitBranch"]))
                if rec.get("cwd") and not self.cwd:
                    self.cwd = rec["cwd"]
                if rtype == "assistant":
                    self._assistant(rec, ts)
                elif rtype == "user":
                    self._user(rec, ts)
                elif rtype == "system":
                    self._system(rec, ts)
                elif rtype == "attachment":
                    self._attachment(rec, ts)
                elif rtype == "cost-state":
                    self.cost_state = rec
                else:
                    if rtype not in KNOWN_OTHER_RECORDS:
                        self.unknown_types[f"record:{rtype}"] += 1
                    self.records.append((ts, "other"))

    def _assistant(self, rec, ts):
        msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
        mid = msg.get("id") or rec.get("uuid")
        usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
        entry = self.messages.get(mid)
        if entry is None:
            entry = {"model": msg.get("model"), "effort": rec.get("effort"), "usage": usage,
                     "ts_first": ts, "ts_last": ts}
            self.messages[mid] = entry
        else:
            if usage:
                entry["usage"] = usage
            if ts and entry["ts_last"] and ts > entry["ts_last"]:
                entry["ts_last"] = ts
        self.records.append((ts, "assistant"))
        blocks = content_blocks(msg)
        txt = text_of(blocks)
        if txt.strip():
            self._last_assistant_text = txt
        for b in blocks:
            if b.get("type") != "tool_use":
                continue
            tid = b.get("id")
            name = b.get("name")
            inp = b.get("input") if isinstance(b.get("input"), dict) else {}
            self.tool_uses[tid] = {"name": name, "ts": ts, "msg_id": mid, "input": inp,
                                   "summary": self._summarize_input(name, inp)}
            if name == "Skill":
                self.skill_calls.append({"ts": ts, "name": inp.get("skill"), "args": str(inp.get("args") or "")[:120]})

    @staticmethod
    def _summarize_input(name, inp):
        if name in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit"):
            return inp.get("file_path") or inp.get("notebook_path")
        if name == "Bash":
            return str(inp.get("command") or "")[:80]
        if name in ("Grep", "Glob"):
            return inp.get("pattern")
        if name == "Agent":
            return f"{inp.get('subagent_type')}: {inp.get('description')}"
        if name == "Skill":
            return inp.get("skill")
        if name == "Workflow":
            return inp.get("name") or "inline script"
        if name == "AskUserQuestion":
            qs = inp.get("questions") if isinstance(inp.get("questions"), list) else []
            return " | ".join(str(q.get("question", ""))[:120] for q in qs if isinstance(q, dict))
        return None

    def _user(self, rec, ts):
        msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
        blocks = content_blocks(msg)
        results = [b for b in blocks if b.get("type") == "tool_result"]
        if results:
            for b in results:
                tid = b.get("tool_use_id")
                name = (self.tool_uses.get(tid) or {}).get("name")
                text = block_text(b, 2000 if name == "Agent" else 300)
                self.tool_results[tid] = {
                    "ts": ts, "is_error": bool(b.get("is_error")), "chars": block_len(b),
                    "denial": rec.get("toolDenialKind"),
                    "persisted": "<persisted-output>" in text,
                    "tool_use_result": rec.get("toolUseResult") if isinstance(rec.get("toolUseResult"), dict) else None,
                    "text": text,
                }
                if name == "Agent" and "Async agent launched" not in text:
                    self.handbacks.append({"ts": ts, "kind": "agent_result", "text": text,
                                           "label": (self.tool_uses.get(tid) or {}).get("summary")})
                self.records.append((ts, "tool_result"))
            return
        txt = text_of(blocks)
        if rec.get("isCompactSummary"):
            self.compact_summaries.append({"ts": ts, "text": txt[:1500], "chars": len(txt)})
            self.records.append((ts, "other"))
            return
        origin = rec.get("origin") if isinstance(rec.get("origin"), dict) else {}
        kind = origin.get("kind")
        if kind in ("peer", "task-notification"):
            self.handbacks.append({"ts": ts, "kind": "peer" if kind == "peer" else "notification",
                                   "text": txt[:2000], "label": origin.get("from")})
            self.records.append((ts, "other"))
            return
        if rec.get("isMeta"):
            m = SKILL_BODY_RE.search(txt)
            if m:
                self.skill_loads.append({"ts": ts, "dir": m.group(1).strip(), "chars": len(txt)})
                pc = self._pending_command
                if pc and ts and pc["ts"] and (ts - pc["ts"]).total_seconds() <= COMMAND_SKILL_WINDOW_S:
                    self._confirm_command(pc)
                    self._pending_command = None
            self.records.append((ts, "other"))
            return
        cmd = COMMAND_RE.search(txt)
        if cmd:
            name = cmd.group(1).strip().lstrip("/")
            args_m = COMMAND_ARGS_RE.search(txt)
            args = (args_m.group(1).strip() if args_m else "")[:120]
            if name == "model":
                self.model_switch_commands.append({"ts": iso(ts), "to": args})
            else:
                full_args = (args_m.group(1).strip() if args_m else "")
                ev = {"ts": ts, "kind": "command", "name": name, "args": args, "full_args": full_args[:1500],
                      "uuid": rec.get("uuid"), "before": self._last_assistant_text[-600:]}
                if ":" in name:            # plugin-namespaced skill: no body record needed
                    self._confirm_command(ev)
                else:                      # built-in unless a skill body loads right after it
                    self._pending_command = ev
            self.records.append((ts, "other"))
            return
        if "<system-reminder>" in txt:
            self.reminder_chars += sum(len(x) for x in REMINDER_RE.findall(txt))
            txt = REMINDER_RE.sub("", txt)
        if not txt.strip() and kind == "human" and any(b.get("type") in ("image", "document") for b in blocks):
            txt = "[image]"
        is_prompt = kind == "human" or (not origin and txt.strip() and not txt.lstrip().startswith(NON_PROMPT_PREFIXES))
        if is_prompt and txt.strip():
            self.human_turns.append({"ts": ts, "text": txt.strip().replace("\n", " ")[:100], "full": txt.strip()[:1500],
                                     "uuid": rec.get("uuid"), "chars": len(txt),
                                     "before": self._last_assistant_text[-600:]})
            self.prompt_ts.append(ts)
            self.records.append((ts, "human"))
            return
        self.records.append((ts, "other"))

    def _confirm_command(self, ev):
        """A typed command that really started a skill: a skill run AND a human turn carrying the brief."""
        self.skill_events.append(ev)
        self.prompt_ts.append(ev["ts"])
        line = f"/{ev['name']} {ev['full_args']}".strip()
        self.human_turns.append({"ts": ev["ts"], "text": line.replace("\n", " ")[:100], "full": line[:1500],
                                 "uuid": ev["uuid"], "chars": len(ev["full_args"]), "before": ev["before"]})
        self.human_turns.sort(key=lambda h: h["ts"] or datetime.min.replace(tzinfo=timezone.utc))
        self.records.append((ev["ts"], "human"))

    def _system(self, rec, ts):
        sub = rec.get("subtype")
        if sub == "compact_boundary":
            meta = rec.get("compactMetadata") if isinstance(rec.get("compactMetadata"), dict) else {}
            self.compactions.append({"ts": iso(ts), "trigger": meta.get("trigger"),
                                     "pre_tokens": num(meta.get("preTokens")) or None,
                                     "post_tokens": num(meta.get("postTokens")) or None,
                                     "duration_ms": num(meta.get("durationMs")) or None})
        elif sub == "api_error":
            err = rec.get("error") if isinstance(rec.get("error"), dict) else {}
            self.api_errors.append({"ts": iso(ts), "status": err.get("status"),
                                    "message": str(err.get("formatted") or err.get("message") or "")[:120],
                                    "attempt": rec.get("retryAttempt")})
        elif sub == "stop_hook_summary":
            infos = rec.get("hookInfos") if isinstance(rec.get("hookInfos"), list) else []
            durs = [num(h.get("durationMs")) for h in infos if isinstance(h, dict) and h.get("durationMs") is not None]
            self.stop_hooks.append({"ts": ts, "duration_ms": sum(durs) if durs else None,
                                    "errors": len(rec.get("hookErrors") or [])})
        self.records.append((ts, "other"))

    def _attachment(self, rec, ts):
        att = rec.get("attachment") if isinstance(rec.get("attachment"), dict) else {}
        atype = att.get("type")
        if atype and atype.startswith("hook_"):
            content = att.get("content")
            if isinstance(content, list):
                content = "\n".join(str(c) for c in content)
            chars = len(str(content or "")) or len(str(att.get("stdout") or ""))
            self.hook_events.append({"ts": ts, "name": att.get("hookName") or "?", "chars": chars,
                                     "error": atype == "hook_non_blocking_error",
                                     "duration_ms": num(att.get("durationMs")) if att.get("durationMs") is not None else None,
                                     "tool_use_id": att.get("toolUseID") or rec.get("uuid")})
        elif atype == "queued_command":
            org = att.get("origin") if isinstance(att.get("origin"), dict) else {}
            if org.get("kind") == "human" and att.get("prompt"):
                self.interjections.append({"ts": ts, "text": str(att["prompt"]).strip()[:1500]})
        elif atype == "prompt_snapshot":
            self.system_prompt_chars = max(self.system_prompt_chars or 0, len(str(att.get("systemPrompt") or "")))
        elif atype == "skill_listing":
            self.skill_listing_chars = max(self.skill_listing_chars or 0, len(str(att.get("content") or "")))
        elif atype == "language":
            self.language_setting = att.get("language")
        elif atype not in KNOWN_ATTACHMENTS:
            self.unknown_types[f"attachment:{atype}"] += 1
        self.records.append((ts, "other"))

    # -- cutoff (the retro request itself) ----------------------------------

    def cut_after(self, cutoff):
        keep = lambda ts: not (ts and ts >= cutoff)  # noqa: E731
        self.human_turns = [h for h in self.human_turns if keep(h["ts"])]
        self.prompt_ts = [t for t in self.prompt_ts if keep(t)]
        self.records = [r for r in self.records if keep(r[0])]
        self.messages = {k: v for k, v in self.messages.items() if keep(v["ts_first"])}
        self.tool_uses = {k: v for k, v in self.tool_uses.items() if keep(v["ts"])}
        self.tool_results = {k: v for k, v in self.tool_results.items() if keep(v["ts"])}
        self.skill_events = [e for e in self.skill_events if keep(e["ts"])]
        self.skill_calls = [e for e in self.skill_calls if keep(e["ts"])]
        self.skill_loads = [e for e in self.skill_loads if keep(e["ts"])]
        self.handbacks = [e for e in self.handbacks if keep(e["ts"])]
        self.interjections = [e for e in self.interjections if keep(e["ts"])]
        self.compactions = [c for c in self.compactions if keep(parse_ts(c["ts"]))]
        self.compact_summaries = [c for c in self.compact_summaries if keep(c["ts"])]
        self.api_errors = [e for e in self.api_errors if keep(parse_ts(e["ts"]))]
        self.stop_hooks = [e for e in self.stop_hooks if keep(e["ts"])]
        self.hook_events = [e for e in self.hook_events if keep(e["ts"])]
        self.ts_last = max((r[0] for r in self.records if r[0]), default=self.ts_first)

    # -- derived -----------------------------------------------------------

    def totals(self):
        t = Counter(ZERO_ROW)
        by_model = defaultdict(lambda: Counter(ZERO_ROW))
        by_model_effort = defaultdict(lambda: Counter(ZERO_ROW))
        for m in self.messages.values():
            u = m.get("usage") or {}
            if not u or m.get("model") == "<synthetic>":
                continue
            cc = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
            w5, w1 = cc.get("ephemeral_5m_input_tokens"), cc.get("ephemeral_1h_input_tokens")
            if w5 is None and w1 is None:
                w5, w1 = num(u.get("cache_creation_input_tokens")), 0
            details = u.get("output_tokens_details") if isinstance(u.get("output_tokens_details"), dict) else {}
            row = {
                "api_calls": 1,
                "input": num(u.get("input_tokens")), "output": num(u.get("output_tokens")),
                "thinking": num(details.get("thinking_tokens")),
                "thinking_known": 1 if details.get("thinking_tokens") is not None else 0,
                "cache_read": num(u.get("cache_read_input_tokens")),
                "cache_write_5m": num(w5), "cache_write_1h": num(w1),
            }
            t.update(row)
            by_model[m.get("model") or "?"].update(row)
            by_model_effort[(m.get("model") or "?", m.get("effort") or "-")].update(row)
        return t, by_model, by_model_effort

    def tool_calls(self):
        return len(self.tool_uses)

    def span_seconds(self):
        if self.ts_first and self.ts_last:
            return (self.ts_last - self.ts_first).total_seconds()
        return None

    def active_intervals(self, gap_cap):
        """Work time as merged intervals: gaps between records under the cap, plus tool calls of any length.
        A journal continued after a long pause (SendMessage) keeps only its working stretches."""
        stamped = sorted(ts for ts, _ in self.records if ts)
        intervals = [(a, b) for a, b in zip(stamped, stamped[1:]) if (b - a).total_seconds() <= gap_cap]
        own, agents = self.tool_intervals()
        return merge_intervals(intervals + own + agents)

    def tool_intervals(self):
        own, agents = [], []
        for tid, tu in self.tool_uses.items():
            tr = self.tool_results.get(tid)
            if not (tr and tr["ts"] and tu["ts"]) or tu["name"] in HUMAN_WAIT_TOOLS:
                continue
            (agents if tu["name"] == "Agent" else own).append((tu["ts"], tr["ts"]))
        return own, agents

    def reads(self):
        out = defaultdict(lambda: {"count": 0, "chars": 0})
        for tid, tu in self.tool_uses.items():
            paths = []
            if tu["name"] == "Read" and tu["summary"]:
                paths = [tu["summary"]]
            elif tu["name"] == "Bash":
                cmd = str((tu.get("input") or {}).get("command") or "")
                base = self.cwd or ""
                m = re.search(r"(?:^|[;&|]\s*)cd\s+([^\s;&|]+)", cmd)
                if m:
                    base = os.path.normpath(os.path.join(base, os.path.expanduser(m.group(1))))
                paths = [os.path.normpath(os.path.join(base, os.path.expanduser(x))) if not os.path.isabs(os.path.expanduser(x)) else os.path.expanduser(x)
                         for x in BASH_READ_RE.findall(cmd)]
            for path in paths:
                out[path]["count"] += 1
                tr = self.tool_results.get(tid)
                if tr:
                    out[path]["chars"] = max(out[path]["chars"], tr["chars"])
        return out

    def friction(self):
        errors, denials, samples, error_samples = Counter(), Counter(), [], defaultdict(list)
        persisted = 0
        for tid, tr in sorted(self.tool_results.items(), key=lambda kv: kv[1]["ts"] or datetime.min.replace(tzinfo=timezone.utc)):
            name = (self.tool_uses.get(tid) or {}).get("name") or "?"
            if tr["persisted"]:
                persisted += 1
            if tr["denial"]:
                denials[tr["denial"]] += 1
                if len(samples) < 5:
                    samples.append(f"{name}: {tr['text'][:110]}")
            elif tr["is_error"]:
                errors[name] += 1
                if len(error_samples[name]) < 3:
                    error_samples[name].append(f"{short_ts(iso(tr['ts']))} {tr['text'][:110]}".replace("\n", " "))
        return {"tool_errors": dict(errors), "tool_error_samples": dict(error_samples),
                "denials": dict(denials), "denial_samples": samples,
                "persisted_outputs": persisted, "api_errors": self.api_errors,
                "hook_errors": sum(1 for e in self.hook_events if e["error"])}


# ---------------------------------------------------------------- pricing

def load_prices(path):
    if not path or not os.path.exists(path):
        return None, None
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return {k: v for k, v in data.items() if not k.startswith("_")}, data.get("_verified")


def price_for(model, prices):
    if not prices or not model:
        return None
    best = None
    for key, p in prices.items():
        if key in model and (best is None or len(key) > len(best[0])):
            best = (key, p)
    return best[1] if best else None


def cost_of(row, model, prices):
    p = price_for(model, prices)
    if not p:
        return None
    m = 1_000_000
    return (row["input"] / m * p.get("in", 0) + row["output"] / m * p.get("out", 0)
            + row["cache_read"] / m * p.get("cache_read", 0)
            + row["cache_write_5m"] / m * p.get("cache_write_5m", p.get("cache_write", 0))
            + row["cache_write_1h"] / m * p.get("cache_write_1h", p.get("cache_write", 0)))


def cost_by_class(by_model, prices):
    """Cost split into input / output / cache read / cache write across models (priced models only)."""
    out = Counter()
    m = 1_000_000
    for model, row in by_model.items():
        p = price_for(model, prices)
        if not p:
            continue
        out["input"] += row["input"] / m * p.get("in", 0)
        out["output"] += row["output"] / m * p.get("out", 0)
        out["cache_read"] += row["cache_read"] / m * p.get("cache_read", 0)
        out["cache_write"] += (row["cache_write_5m"] / m * p.get("cache_write_5m", p.get("cache_write", 0))
                               + row["cache_write_1h"] / m * p.get("cache_write_1h", p.get("cache_write", 0)))
    return {k: round(v, 4) for k, v in out.items()} if out else None


def cost_by_model(by_model, prices):
    total, known, unpriced, out = 0.0, False, [], {}
    for model, row in by_model.items():
        c = cost_of(row, model, prices)
        out[model] = c
        if c is None:
            unpriced.append(model)
        else:
            total += c
            known = True
    return (total if known else None), out, unpriced


# ---------------------------------------------------------------- session discovery

def sessions_dir_for(project_dir, override=None):
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return override or os.path.join(config_dir, "projects", slug_for(project_dir))


def artifacts_dir_from(project_md, default="tasks/retros"):
    """The **Artifacts directory:** `path` line of project.md, else the default."""
    if project_md and os.path.exists(project_md):
        with open(project_md, "r", encoding="utf-8", errors="replace") as fh:
            m = re.search(r"\**Artifacts directory:?\**:?\s*`([^`]+)`", fh.read())
        if m:
            return m.group(1).strip().rstrip("/")
    return default


def gitignore_warning(path, project_dir):
    """One line if `path` is inside a git repo and not ignored (it will hold prompt text)."""
    try:
        import subprocess
        inside = subprocess.run(["git", "-C", project_dir, "rev-parse", "--is-inside-work-tree"], capture_output=True)
        r = subprocess.run(["git", "-C", project_dir, "check-ignore", "-q", path], capture_output=True)
        if inside.returncode == 0 and r.returncode == 1:
            return f"warning: {path} is not git-ignored; it contains prompt text, add it to .gitignore or keep it out of commits"
    except (OSError, ValueError):
        pass
    return None


def resolve_session(sessions_dir, spec):
    files = glob.glob(os.path.join(sessions_dir, "*.jsonl"))
    if not files:
        raise SystemExit(f"no transcripts under {sessions_dir}")
    hits = [f for f in files if os.path.basename(f).startswith(spec)]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise SystemExit(f"no session starting with '{spec}' under {sessions_dir}")
    names = ", ".join(os.path.basename(h)[:12] for h in sorted(hits)[:8])
    raise SystemExit(f"ambiguous session prefix '{spec}' ({len(hits)} matches): {names}")


# ---------------------------------------------------------------- agents and workflows

def load_agents(session_dir, main):
    """Parse every sub-agent journal and link it to the tool_use that spawned it."""
    paths = sorted(glob.glob(os.path.join(session_dir, "subagents", "**", "agent-*.jsonl"), recursive=True))
    agents, journals, skipped = [], {}, Counter()
    seen_messages = set(main.messages)
    for path in paths:
        base = os.path.basename(path)
        if base.startswith(NON_AGENT_JOURNAL_PREFIXES):
            skipped[base.split("-")[1]] += 1
            continue
        meta = {}
        meta_path = path[:-len(".jsonl")] + ".meta.json"
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as fh:
                    meta = json.load(fh)
            except (OSError, ValueError):
                meta = {}
        if not isinstance(meta, dict):
            meta = {}
        j = Journal(path)
        if j.lines == 0:
            continue
        j.messages = {k: v for k, v in j.messages.items() if k not in seen_messages}
        seen_messages.update(j.messages)
        rel = os.path.relpath(path, os.path.join(session_dir, "subagents"))
        workflow_run = rel.split(os.sep)[1] if rel.startswith("workflows" + os.sep) else None
        agent_id = base[len("agent-"):-len(".jsonl")]
        journals[agent_id] = j
        agents.append({"agent_id": agent_id, "type": meta.get("agentType"), "description": meta.get("description"),
                       "tool_use_id": meta.get("toolUseId"), "workflow_run": workflow_run, "journal": j})
    owner = {tid: "main" for tid in main.tool_uses}
    for a in agents:
        for tid in a["journal"].tool_uses:
            owner[tid] = a["agent_id"]
    by_id = {a["agent_id"]: a for a in agents}
    for a in agents:
        parent = owner.get(a["tool_use_id"])
        a["parent"] = parent
        src = main if parent == "main" else (journals.get(parent) if parent else None)
        tu = src.tool_uses.get(a["tool_use_id"]) if src else None
        a["launch_ts"] = tu["ts"] if tu else a["journal"].ts_first
        a["launch_msg_id"] = tu["msg_id"] if tu else None
        inp = (tu or {}).get("input") or {}
        a["type"] = a["type"] or inp.get("subagent_type")
        a["description"] = a["description"] or inp.get("description")
        a["requested_model"] = inp.get("model")
        res = (main.tool_results.get(a["tool_use_id"]) or {}).get("tool_use_result") if parent == "main" else None
        a["async"] = bool(res and res.get("isAsync"))
        a["resolved_model"] = (res or {}).get("resolvedModel")
    depth_cache = {}

    def depth(agent, guard=0):
        aid = agent["agent_id"]
        if aid in depth_cache:
            return depth_cache[aid]
        p = agent.get("parent")
        d = 1 if (p in (None, "main") or p not in by_id or guard > 20) else 1 + depth(by_id[p], guard + 1)
        depth_cache[aid] = d
        return d

    for a in agents:
        a["depth"] = depth(a)
    return agents, dict(skipped)


def agent_rows(agents, prices, gap_cap):
    rows = []
    for a in agents:
        j = a["journal"]
        active = j.active_intervals(gap_cap)
        t, by_model, _ = j.totals()
        cost, _, _ = cost_by_model(by_model, prices)
        cache_in = t["input"] + t["cache_read"] + t["cache_write_5m"] + t["cache_write_1h"]
        dominant = max(by_model.items(), key=lambda kv: kv[1]["api_calls"])[0] if by_model else a.get("resolved_model")
        rows.append({
            "agent_id": a["agent_id"], "type": a["type"], "description": str(a["description"] or "")[:60],
            "depth": a["depth"], "parent": a["parent"], "workflow_run": a["workflow_run"],
            "async": a["async"], "requested_model": a["requested_model"],
            "model": dominant, "models": sorted(by_model.keys()),
            "started": iso(a["launch_ts"] or j.ts_first), "span_s": round(j.span_seconds() or 0),
            "active_s": round(union_seconds(active)), "active_intervals": [(iso(x), iso(y)) for x, y in active],
            "api_calls": t["api_calls"], "input": t["input"], "output": t["output"],
            "thinking": t["thinking"] if t["thinking_known"] else None,
            "cache_read": t["cache_read"], "cache_write": t["cache_write_5m"] + t["cache_write_1h"],
            "cache_hit": round(t["cache_read"] / cache_in, 3) if cache_in else None,
            "tool_calls": j.tool_calls(), "errors": sum(j.friction()["tool_errors"].values()),
            "cost_usd": round(cost, 4) if cost is not None else None,
            "launch_msg_id": a["launch_msg_id"],
        })
    rows.sort(key=lambda r: r["started"] or "")
    return rows


def agent_batches(rows):
    """Clusters of top-level agents whose active intervals overlap (= ran concurrently). An agent continued
    after a long pause contributes each working stretch on its own, so it can appear in several clusters."""
    items = []
    for r in rows:
        if r["workflow_run"] or r["depth"] > 1:
            continue
        for a, b in r["active_intervals"]:
            items.append((parse_ts(a), parse_ts(b), r))
    items.sort(key=lambda x: x[0])
    clusters, cur, cur_end = [], [], None
    for s, e, r in items:
        if cur and s > cur_end:
            clusters.append(cur); cur, cur_end = [], None
        cur.append((s, e, r))
        cur_end = e if (cur_end is None or e > cur_end) else cur_end
    if cur:
        clusters.append(cur)
    batches = []
    for c in clusters:
        ids, g, cost = [], [], 0.0
        for _, _, r in c:
            if r["agent_id"] not in ids:
                ids.append(r["agent_id"]); g.append(r)
        wall = round(union_seconds([(s, e) for s, e, _ in c]))
        active = round(sum((e - s).total_seconds() for s, e, _ in c))
        for r in g:     # an agent spread over several clusters: its cost prorated by the active time inside this one
            here = sum((e - s).total_seconds() for s, e, x in c if x is r)
            cost += (r["cost_usd"] or 0) * (here / r["active_s"] if r["active_s"] else 1)
        batches.append({"started": iso(c[0][0]), "agents": ids, "types": [r["type"] for r in g],
                        "launch_messages": len({r["launch_msg_id"] for r in g}),
                        "sum_active_s": active, "wall_s": wall, "parallelism": round(active / wall, 2) if wall else None,
                        "cost_usd": round(cost, 4) if any(r["cost_usd"] is not None for r in g) else None})
    return batches


def load_workflows(session_dir):
    out = []
    for path in sorted(glob.glob(os.path.join(session_dir, "workflows", "wf_*.json"))):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                w = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(w, dict):
            continue
        progress = [p for p in (w.get("workflowProgress") or []) if isinstance(p, dict)]
        agents = [p for p in progress if p.get("type") == "workflow_agent"]
        phases = defaultdict(lambda: {"agents": 0, "tokens": 0, "tool_calls": 0, "duration_ms": 0, "retries": 0, "not_done": 0, "models": Counter()})
        for a in agents:
            ph = phases[a.get("phaseTitle") or f"phase {a.get('phaseIndex')}"]
            ph["agents"] += 1
            ph["tokens"] += num(a.get("tokens"))
            ph["tool_calls"] += num(a.get("toolCalls"))
            ph["duration_ms"] = max(ph["duration_ms"], num(a.get("durationMs")))
            if num(a.get("attempt") or 1) > 1:
                ph["retries"] += 1
            if str(a.get("state")) not in ("done", "completed", "success"):
                ph["not_done"] += 1
            ph["models"][a.get("model") or "?"] += 1
        out.append({"run_id": w.get("runId"), "name": w.get("workflowName"), "status": w.get("status"),
                    "duration_ms": num(w.get("durationMs")), "agent_count": num(w.get("agentCount")) or len(agents),
                    "reported_tokens": num(w.get("totalTokens")), "total_tool_calls": num(w.get("totalToolCalls")),
                    "default_model": w.get("defaultModel"),
                    "phases": [{"title": k, **{kk: (dict(vv) if isinstance(vv, Counter) else vv) for kk, vv in v.items()}} for k, v in phases.items()]})
    return out


# ---------------------------------------------------------------- turns and segments

def build_turns(main, gap_cap):
    """Human prompt -> next human prompt. active = capped gaps between work records; wait = to next prompt."""
    humans = main.human_turns
    stamped = sorted((ts, kind) for ts, kind in main.records if ts)
    msg_ts = {mid: m["ts_first"] for mid, m in main.messages.items()}
    turns = []
    for i, h in enumerate(humans):
        start = h["ts"]
        end_bound = humans[i + 1]["ts"] if i + 1 < len(humans) else None
        recs = [r for r in stamped if r[0] >= start and (end_bound is None or r[0] < end_bound)]
        work = [r for r in recs if r[1] in ("assistant", "tool_result")]
        last = work[-1][0] if work else start
        work_recs = [r for r in recs if r[0] <= last]
        intervals = [(a[0], b[0]) for a, b in zip(work_recs, work_recs[1:]) if 0 <= (b[0] - a[0]).total_seconds() <= gap_cap]
        for tid, tu in main.tool_uses.items():      # long tool calls (tests, builds) are work even beyond the gap cap
            tr = main.tool_results.get(tid)
            if tr and tu["ts"] and tr["ts"] and start <= tu["ts"] and (end_bound is None or tu["ts"] < end_bound) and tu["name"] != "Agent" and tu["name"] not in HUMAN_WAIT_TOOLS:
                intervals.append((tu["ts"], tr["ts"]))
        active = union_seconds(intervals)
        in_turn = lambda ts: ts and ts >= start and (end_bound is None or ts < end_bound)  # noqa: E731
        msgs = [mid for mid, ts in msg_ts.items() if in_turn(ts)]
        tools = [tid for tid, tu in main.tool_uses.items() if in_turn(tu["ts"])]
        row = Counter()
        for mid in msgs:
            u = main.messages[mid].get("usage") or {}
            row["output"] += num(u.get("output_tokens"))
            row["cache_read"] += num(u.get("cache_read_input_tokens"))
            row["cache_write"] += num(u.get("cache_creation_input_tokens"))
            row["input"] += num(u.get("input_tokens"))
        written = sorted({main.tool_uses[tid]["summary"] for tid in tools
                          if main.tool_uses[tid]["name"] in ("Write", "Edit") and main.tool_uses[tid]["summary"]})
        turns.append({
            "n": i + 1, "ts": iso(start), "end": iso(end_bound) if end_bound else None, "prompt": h["text"],
            "language": detect_language(h["full"]),
            "active_s": round(active), "wait_after_s": round((end_bound - last).total_seconds()) if end_bound else None,
            "api_calls": len(msgs), "tool_calls": len(tools), "output": row["output"], "cache_read": row["cache_read"],
            "cache_write": row["cache_write"],
            "skills": [e["name"] for e in main.skill_events if in_turn(e["ts"])] + [c["name"] for c in main.skill_calls if in_turn(c["ts"])],
            "agents_launched": sum(1 for tid in tools if main.tool_uses[tid]["name"] == "Agent"),
            "questions": sum(1 for tid in tools if main.tool_uses[tid]["name"] == "AskUserQuestion"),
            "interjections": sum(1 for e in main.interjections if in_turn(e["ts"])),
            "answered_question": h["before"].rstrip().endswith("?"),
            "artifacts_written": [os.path.relpath(w, main.cwd) if main.cwd and w.startswith(main.cwd) else w for w in written][:8],
        })
    return turns


def build_segments(main, turns, agent_rows_, prices):
    """Sequential skill runs: user-typed skill commands, plus model Skill calls made before the first
    user command (a skill run by prose, 'run code-review'). A run ends where the next one starts."""
    typed = sorted([e for e in main.skill_events if e["ts"]], key=lambda e: e["ts"])
    first_typed = typed[0]["ts"] if typed else None
    by_model = [{"ts": c["ts"], "kind": "model", "name": c["name"], "args": c["args"]}
                for c in main.skill_calls if c["ts"] and (first_typed is None or c["ts"] < first_typed)]
    events = sorted(typed + by_model, key=lambda e: e["ts"])
    if not events:
        return []
    far = datetime.max.replace(tzinfo=timezone.utc)
    turn_bounds = [(parse_ts(t["ts"]), parse_ts(t["end"]) if t["end"] else None, t) for t in turns]

    def turn_start_for(ts):
        for s, e, _ in turn_bounds:
            if s <= ts and (e is None or ts < e):
                return s
        return ts

    starts = []
    for e in events:
        s = turn_start_for(e["ts"])
        if starts and s <= starts[-1]:
            s = e["ts"]
        starts.append(s)
    pauses = [parse_ts(t["end"]) for t in turns if t["end"] and (t["wait_after_s"] or 0) > 3600]   # next prompt after a > 1h pause
    segments = []
    for i, e in enumerate(events):
        start = starts[i]
        end = starts[i + 1] if i + 1 < len(events) else None
        for pz in pauses:
            if pz and pz > start and (end is None or pz < end):
                end = pz
                break
        in_seg = lambda ts: ts and ts >= start and (end is None or ts < end)  # noqa: E731
        row = Counter(ZERO_ROW)
        by_model = defaultdict(lambda: Counter(ZERO_ROW))
        for m in main.messages.values():
            if not in_seg(m["ts_first"]) or m.get("model") == "<synthetic>":
                continue
            u = m.get("usage") or {}
            cc = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
            w5, w1 = cc.get("ephemeral_5m_input_tokens"), cc.get("ephemeral_1h_input_tokens")
            if w5 is None and w1 is None:
                w5, w1 = num(u.get("cache_creation_input_tokens")), 0
            r = {"api_calls": 1, "input": num(u.get("input_tokens")), "output": num(u.get("output_tokens")),
                 "cache_read": num(u.get("cache_read_input_tokens")), "cache_write_5m": num(w5), "cache_write_1h": num(w1)}
            row.update(r)
            by_model[m.get("model") or "?"].update(r)
        cost, _, _ = cost_by_model(by_model, prices)
        tools = [tu for tu in main.tool_uses.values() if in_seg(tu["ts"])]
        seg_turns = [t for s, e_, t in turn_bounds if s < (end or far) and (e_ is None or e_ > start)]
        agents = [r for r in agent_rows_ if in_seg(parse_ts(r["started"]))]
        compactions = [c for c in main.compactions if in_seg(parse_ts(c["ts"]))]
        errors = sum(1 for tr in main.tool_results.values() if tr["is_error"] and in_seg(tr["ts"]))
        artifacts = sorted({tu["summary"] for tu in tools if tu["name"] in ("Write", "Edit") and tu["summary"]})
        artifacts = [os.path.relpath(a, main.cwd) if main.cwd and a.startswith(main.cwd) else a for a in artifacts]
        cache_in = row["input"] + row["cache_read"] + row["cache_write_5m"] + row["cache_write_1h"]
        first_turn = seg_turns[0] if seg_turns else None
        inside = Counter(c["name"] for c in main.skill_calls if in_seg(c["ts"]) and c["ts"] != e["ts"])
        segments.append({
            "skills_used_inside": dict(inside),
            "n": i + 1, "skill": e["name"], "via": e["kind"], "args": e["args"], "started": iso(start),
            "invoking_turn": ({"n": first_turn["n"], "active_s": first_turn["active_s"], "api_calls": first_turn["api_calls"],
                               "tool_calls": first_turn["tool_calls"], "output": first_turn["output"]} if first_turn else None),
            "ended": iso(end) if end else None, "active_s": sum(t["active_s"] for t in seg_turns),
            "human_turns": len(seg_turns),
            "questions_to_user": sum(1 for t in seg_turns[1:] if t["answered_question"]) + sum(t["questions"] for t in seg_turns),
            "api_calls": row["api_calls"], "tool_calls": len(tools), "input": row["input"], "output": row["output"],
            "cache_read": row["cache_read"], "cache_write": row["cache_write_5m"] + row["cache_write_1h"],
            "cache_hit": round(row["cache_read"] / cache_in, 3) if cache_in else None,
            "cost_usd": round(cost, 4) if cost is not None else None,
            "models": sorted(by_model.keys()), "agents": len(agents),
            "agent_cost_usd": round(sum(a["cost_usd"] or 0 for a in agents), 4) if agents else 0,
            "tool_errors": errors, "compactions": len(compactions), "artifacts_written": artifacts[:12],
        })
    return segments


def duplicate_reads(main, agents):
    readers = defaultdict(dict)
    for path, info in main.reads().items():
        readers[path]["main"] = info
    for a in agents:
        for path, info in a["journal"].reads().items():
            readers[path][a["agent_id"]] = info
    rows = []
    for path, by in readers.items():
        if len(by) < 2:
            continue
        chars = max(v["chars"] for v in by.values())
        reads = sum(v["count"] for v in by.values())
        rows.append({"path": path, "readers": len(by), "reads": reads,
                     "est_tokens_each": chars // CHARS_PER_TOKEN, "est_tokens_total": chars // CHARS_PER_TOKEN * reads})
    rows.sort(key=lambda r: -r["est_tokens_total"])
    return rows[:15]


def context_tax(main):
    hooks = defaultdict(lambda: {"count": 0, "chars": 0, "errors": 0, "duration_ms": 0})
    for e in main.hook_events:
        h = hooks[e["name"]]
        h["count"] += 1
        h["chars"] += e["chars"]
        h["errors"] += 1 if e["error"] else 0
        h["duration_ms"] += e["duration_ms"] or 0
    loads = defaultdict(lambda: {"count": 0, "chars": 0})
    for s in main.skill_loads:
        name = os.path.basename(s["dir"].rstrip("/"))
        loads[name]["count"] += 1
        loads[name]["chars"] += s["chars"]
    stop_durs = [s["duration_ms"] for s in main.stop_hooks if s["duration_ms"] is not None]
    return {
        "system_prompt_est_tokens": (main.system_prompt_chars // CHARS_PER_TOKEN) if main.system_prompt_chars is not None else None,
        "skill_listing_est_tokens": (main.skill_listing_chars // CHARS_PER_TOKEN) if main.skill_listing_chars is not None else None,
        "hook_injections": {k: {"count": v["count"], "est_tokens": v["chars"] // CHARS_PER_TOKEN, "errors": v["errors"], "duration_ms": v["duration_ms"]} for k, v in hooks.items()},
        "hook_est_tokens_total": sum(v["chars"] for v in hooks.values()) // CHARS_PER_TOKEN,
        "system_reminder_est_tokens": main.reminder_chars // CHARS_PER_TOKEN,
        "skill_loads": {k: {"count": v["count"], "est_tokens": v["chars"] // CHARS_PER_TOKEN} for k, v in loads.items()},
        "compact_summary_est_tokens": sum(c["chars"] for c in main.compact_summaries) // CHARS_PER_TOKEN,
        "stop_hooks": {"runs": len(main.stop_hooks), "duration_ms": sum(stop_durs) if stop_durs else None,
                       "errors": sum(s["errors"] for s in main.stop_hooks)},
    }


def cache_rebuilds(main, prices, min_tokens=50_000):
    """Assistant messages that wrote a large cache prefix, with the likely cause."""
    seq = sorted((m for m in main.messages.values() if m["ts_first"] and m.get("model") != "<synthetic>"), key=lambda m: m["ts_first"])
    t, _, _ = main.totals()
    ttl_s = 3600 if t["cache_write_1h"] >= t["cache_write_5m"] else 300     # the TTL this session actually writes
    events = []
    prev = None
    for m in seq:
        u = m.get("usage") or {}
        write = num(u.get("cache_creation_input_tokens"))
        if prev is not None and write >= min_tokens:
            gap = (m["ts_first"] - prev["ts_first"]).total_seconds()
            if prev.get("model") != m.get("model") or prev.get("effort") != m.get("effort"):
                cause = "model/effort switch"
            elif gap > ttl_s:
                cause = f"gap > {'1h' if ttl_s == 3600 else '5m'} TTL"
            else:
                cause = "other (edited prefix, compaction, new context)"
            p = price_for(m.get("model"), prices)
            cc = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
            w1 = num(cc.get("ephemeral_1h_input_tokens")); w5 = write - w1 if cc else write
            cost = ((w5 / 1e6 * p.get("cache_write_5m", p.get("cache_write", 0)) + w1 / 1e6 * p.get("cache_write_1h", p.get("cache_write", 0))) if p else None)
            events.append({"ts": iso(m["ts_first"]), "model": m.get("model"), "effort": m.get("effort"), "tokens": write,
                           "gap_s": round(gap), "cause": cause, "cost_usd": round(cost, 4) if cost is not None else None})
        prev = m
    by_cause = defaultdict(lambda: {"events": 0, "tokens": 0, "cost_usd": 0.0, "priced": False})
    for e in events:
        b = by_cause[e["cause"]]
        b["events"] += 1; b["tokens"] += e["tokens"]
        if e["cost_usd"] is not None:
            b["cost_usd"] += e["cost_usd"]; b["priced"] = True
    return {"events": events[:50], "by_cause": {k: {"events": v["events"], "tokens": v["tokens"], "cost_usd": (round(v["cost_usd"], 2) if v["priced"] else None)} for k, v in by_cause.items()},
            "min_tokens": min_tokens}


def model_timeline(main):
    seq = sorted(((m["ts_first"], m.get("model"), m.get("effort")) for m in main.messages.values()
                  if m["ts_first"] and m.get("model") != "<synthetic>"), key=lambda x: x[0])
    out = []
    for ts, model, effort in seq:
        if not out or out[-1]["model"] != model or out[-1]["effort"] != effort:
            out.append({"from": iso(ts), "model": model, "effort": effort, "api_calls": 1})
        else:
            out[-1]["api_calls"] += 1
    return out


# ---------------------------------------------------------------- analysis

def analyze(transcript, prices, verified, gap_cap, exclude_last_turn):
    sid = os.path.basename(transcript)[:-len(".jsonl")]
    session_dir = transcript[:-len(".jsonl")]
    main = Journal(transcript)
    cutoff = max(main.prompt_ts) if (exclude_last_turn and main.prompt_ts) else None
    if cutoff:
        main.cut_after(cutoff)
    agents, skipped_journals = load_agents(session_dir, main)
    if cutoff:
        agents = [a for a in agents if not (a["launch_ts"] and a["launch_ts"] >= cutoff)]
    rows = agent_rows(agents, prices, gap_cap)
    batches = agent_batches(rows)
    workflows = load_workflows(session_dir)
    turns = build_turns(main, gap_cap)
    segments = build_segments(main, turns, rows, prices)

    t, by_model, by_model_effort = main.totals()
    agent_t = Counter(ZERO_ROW)
    agent_by_model = defaultdict(lambda: Counter(ZERO_ROW))
    for a in agents:
        at, abm, _ = a["journal"].totals()
        agent_t.update(at)
        for m, r in abm.items():
            agent_by_model[m].update(r)
    all_by_model = defaultdict(lambda: Counter(ZERO_ROW))
    for src in (by_model, agent_by_model):
        for m, r in src.items():
            all_by_model[m].update(r)
    grand = Counter(t)
    grand.update(agent_t)
    cost_main, _, _ = cost_by_model(by_model, prices)
    cost_agents, _, _ = cost_by_model(agent_by_model, prices)
    cost_all, cost_per_model, unpriced = cost_by_model(all_by_model, prices)

    def hit(row):
        denom = row["input"] + row["cache_read"] + row["cache_write_5m"] + row["cache_write_1h"]
        return round(row["cache_read"] / denom, 3) if denom else None

    def pack(row, cost):
        return {**{k: v for k, v in row.items() if k != "thinking_known"},
                "thinking": row["thinking"] if row["thinking_known"] else None,
                "cache_hit": hit(row), "cost_usd": round(cost, 4) if cost is not None else None}

    stamped = sorted((ts, kind) for ts, kind in main.records if ts)
    first_prompt = main.human_turns[0]["ts"] if main.human_turns else None
    pre = [r for r in stamped if first_prompt is None or r[0] < first_prompt]
    pre_active = sum((b[0] - a[0]).total_seconds() for a, b in zip(pre, pre[1:]) if 0 <= (b[0] - a[0]).total_seconds() <= gap_cap)
    own_tools, agent_tools = main.tool_intervals()
    resumes = len({e["tool_use_id"] for e in main.hook_events if e["name"].startswith("SessionStart:resume")})
    resumes_source = "SessionStart:resume hooks"
    if not any(e["name"].startswith("SessionStart") for e in main.hook_events):
        hs = [h["ts"] for h in main.human_turns if h["ts"]]
        resumes = sum(1 for a, b in zip(hs, hs[1:]) if (b - a).total_seconds() > 3600)
        resumes_source = "gaps > 1h between prompts (no SessionStart hooks in this session)"
    human_text = " ".join(h["full"] for h in main.human_turns[-5:])

    result = {
        "session": {
            "id": sid, "transcript": transcript, "versions": sorted(main.versions), "branches": sorted(main.branches),
            "cwd": main.cwd, "started": iso(main.ts_first), "ended": iso(main.ts_last), "timezone": "UTC",
            "span_s": round(main.span_seconds() or 0), "active_s": round(pre_active + sum(x["active_s"] for x in turns)),
            "tool_s": round(union_seconds(own_tools)), "agent_wait_s": round(union_seconds(agent_tools)),
            "wait_for_human_s": round(sum(x["wait_after_s"] or 0 for x in turns)),
            "resumes": resumes, "resumes_source": resumes_source,
            "human_turns": len(main.human_turns), "human_interjections": len(main.interjections),
            "api_calls": t["api_calls"],
            "tool_calls": main.tool_calls(), "language_setting": main.language_setting,
            "language_guess": detect_language(human_text),
            "model_timeline": model_timeline(main), "model_switch_commands": main.model_switch_commands,
            "questions_to_user": sum(1 for x in turns[1:] if x["answered_question"]) + sum(x["questions"] for x in turns),
            "excluded_last_turn": bool(cutoff), "bad_lines": main.bad_lines, "skipped_journals": skipped_journals,
        },
        "totals": {
            "main": pack(t, cost_main), "agents": pack(agent_t, cost_agents),
            "all": {**pack(grand, cost_all), "cost_by_model": {m: (round(c, 4) if c is not None else None) for m, c in cost_per_model.items()},
                    "cost_by_class": cost_by_class(all_by_model, prices), "unpriced_models": unpriced},
            "prices_verified": verified,
        },
        "by_model_effort": [{"model": m, "effort": e, **pack(r, cost_of(r, m, prices))}
                            for (m, e), r in sorted(by_model_effort.items(), key=lambda kv: -kv[1]["output"])],
        "turns": turns, "segments": segments, "agents": rows, "agent_batches": batches, "workflows": workflows,
        "compactions": main.compactions, "friction": main.friction(), "context_tax": context_tax(main),
        "cache_rebuilds": cache_rebuilds(main, prices),
        "duplicate_reads": duplicate_reads(main, agents),
        "cost_state": ({"total_cost_usd": main.cost_state.get("totalCostUSD"), "api_duration_ms": main.cost_state.get("totalAPIDuration"),
                        "tool_duration_ms": main.cost_state.get("totalToolDuration"),
                        "models": list(main.cost_state["modelUsage"].keys()) if isinstance(main.cost_state.get("modelUsage"), dict) else []}
                       if main.cost_state else None),
    }
    return result, main


BASE64_RE = re.compile(r"[A-Za-z0-9+/=]{200,}")


def clean(text):
    return BASE64_RE.sub("[base64 omitted]", text or "")


def narrative(main, r):
    """Human prompts, the assistant text before each, questions and agent hand-backs, in order."""
    lines = [f"# narrative: session {r['session']['id'][:8]} (times UTC)", ""]
    floor = datetime.min.replace(tzinfo=timezone.utc)
    events = []
    for i, h in enumerate(main.human_turns):
        events.append((h["ts"] or floor, 0, "turn", (i + 1, h)))
    for hb in main.handbacks:
        events.append((hb["ts"] or floor, 1, "handback", hb))
    for c in main.compact_summaries:
        events.append((c["ts"] or floor, 1, "compact", c))
    for e in main.interjections:
        events.append((e["ts"] or floor, 1, "interjection", e))
    for tu in main.tool_uses.values():
        if tu["name"] == "AskUserQuestion":
            events.append((tu["ts"] or floor, 1, "question", tu["summary"]))
    events.sort(key=lambda e: (e[0], e[1]))
    for ts, _, kind, payload in events:
        if kind == "turn":
            n, h = payload
            if h["before"].strip():
                lines += ["", f"[assistant before turn {n}] …{clean(h['before'].strip())}"]
            skill_tag = " · skill /" + h["full"][1:].split(" ", 1)[0] if h["full"].startswith("/") else ""
            lines += ["", f"## turn {n} — {short_ts(iso(ts))}{skill_tag}", clean(h["full"])]
        elif kind == "question":
            lines += ["", f"[question to user {short_ts(iso(ts))}] {payload}"]
        elif kind == "interjection":
            lines += ["", f"[user, mid-turn {short_ts(iso(ts))}] {clean(payload['text'])}"]
        elif kind == "compact":
            lines += ["", f"[compaction summary {short_ts(iso(ts))}] {payload['text'][:800]}"]
        else:
            label = f" {payload['label']}" if payload.get("label") else ""
            lines += ["", f"[{payload['kind']}{label} {short_ts(iso(ts))}] {clean(payload['text'][:1500])}"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- text report

def render(r):
    s, T = r["session"], r["totals"]
    out = []
    p = out.append
    branches = s["branches"][:5] + ([f"+{len(s['branches']) - 5} more"] if len(s["branches"]) > 5 else [])
    p(f"# retro metrics: session {s['id'][:8]}  ({s['started']} -> {s['ended']}, times UTC, Claude Code {', '.join(s['versions'][-2:]) or '?'}, branch {', '.join(branches) or '?'})")
    if s["excluded_last_turn"]:
        p("(the final turn, the retro request itself, is excluded)")
    p(f"span {fmt_secs(s['span_s'])} | active {fmt_secs(s['active_s'])} | own tools {fmt_secs(s['tool_s'])} | waiting for sync agents {fmt_secs(s['agent_wait_s'])} | waiting for human {fmt_secs(s['wait_for_human_s'])} | resumes {s['resumes']} ({s['resumes_source']})")
    p(f"human turns {s['human_turns']} (+{s['human_interjections']} mid-turn messages) | questions to user {s['questions_to_user']} (AskUserQuestion calls + assistant replies ending in '?' right before a human turn) | api calls {s['api_calls']} | tool calls {s['tool_calls']} | language: setting={s['language_setting'] or '-'} guess={s['language_guess']}")
    tl = " -> ".join(f"{x['model']}@{x['effort'] or '-'}({x['api_calls']})" for x in s["model_timeline"]) or "-"
    p(f"model timeline: {tl}" + (f" | /model commands: {len(s['model_switch_commands'])}" if s["model_switch_commands"] else ""))
    if s["skipped_journals"]:
        p(f"(journals that replay the main conversation, excluded from agent totals: {s['skipped_journals']})")
    p("")
    p("## totals (usage de-duplicated by message.id; 'all' = main + every sub-agent; thinking is part of output; cost = API list-price equivalent, not an invoice)")
    p("| scope | api | input | output | of which thinking | cache read | cache write 5m/1h | hit | cost |")
    p("|---|---|---|---|---|---|---|---|---|")
    for name in ("main", "agents", "all"):
        x = T[name]
        p(f"| {name} | {x['api_calls']} | {fmt_tokens(x['input'])} | {fmt_tokens(x['output'])} | {fmt_tokens(x['thinking'])} | {fmt_tokens(x['cache_read'])} | {fmt_tokens(x['cache_write_5m'])}/{fmt_tokens(x['cache_write_1h'])} | {fmt_hit(x['cache_hit'])} | {fmt_cost(x['cost_usd'])} |")
    if r["agents"]:
        p(f"agents: {len(r['agents'])} journals, sum of active {fmt_secs(sum(a['active_s'] for a in r['agents']))}, sum of spans {fmt_secs(sum(a['span_s'] for a in r['agents']))} (nested {sum(1 for a in r['agents'] if a['depth'] > 1)}, in workflows {sum(1 for a in r['agents'] if a['workflow_run'])})")
    if T["all"]["cost_by_model"]:
        partial = f"; partial, unpriced: {', '.join(T['all']['unpriced_models'])}" if T["all"]["unpriced_models"] else ""
        p("cost by model: " + ", ".join(f"{m} {fmt_cost(c)}" for m, c in T["all"]["cost_by_model"].items()) + f"  (prices verified {T['prices_verified'] or 'n/a'}{partial})")
    if T["all"].get("cost_by_class"):
        cc = T["all"]["cost_by_class"]; tot = sum(cc.values()) or 1
        p("cost by token class: " + ", ".join(f"{k} {fmt_cost(v)} ({v / tot * 100:.0f}%)" for k, v in cc.items()))
    if r["cost_state"]:
        p(f"cost-state record (Claude Code's own tally, may cover only part of the session): ${float(r['cost_state']['total_cost_usd'] or 0):.2f}, models {r['cost_state']['models']}")
    p("")
    if r["by_model_effort"]:
        p("## main session by model x effort")
        p("| model | effort | api | output | of which thinking | cache read | hit | cost |")
        p("|---|---|---|---|---|---|---|---|")
        for x in r["by_model_effort"]:
            p(f"| {x['model']} | {x['effort']} | {x['api_calls']} | {fmt_tokens(x['output'])} | {fmt_tokens(x['thinking'])} | {fmt_tokens(x['cache_read'])} | {fmt_hit(x['cache_hit'])} | {fmt_cost(x['cost_usd'])} |")
        p("")
    if r["segments"]:
        p("## skill runs (typed or model-invoked; from the invoking turn to the next run or the first pause > 1h; main-session numbers, agents separate)")
        p("| # | skill | via | started | active | turns | q->user | api | tools | output | cache read | hit | main cost | agents | agent cost | errors | compact |")
        p("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for g in r["segments"]:
            p(f"| {g['n']} | {g['skill']} | {g['via']} | {short_ts(g['started'])} | {fmt_secs(g['active_s'])} | {g['human_turns']} | {g['questions_to_user']} | {g['api_calls']} | {g['tool_calls']} | {fmt_tokens(g['output'])} | {fmt_tokens(g['cache_read'])} | {fmt_hit(g['cache_hit'])} | {fmt_cost(g['cost_usd'])} | {g['agents']} | {fmt_cost(g['agent_cost_usd'])} | {g['tool_errors']} | {g['compactions']} |")
        for g in r["segments"]:
            if g["artifacts_written"]:
                p(f"- run {g['n']} ({g['skill']}) wrote: " + ", ".join(g["artifacts_written"]))
            if g.get("skills_used_inside"):
                p(f"- run {g['n']} ({g['skill']}) skills used inside: " + ", ".join(f"{k} x{v}" for k, v in g["skills_used_inside"].items()))
            it = g.get("invoking_turn")
            if it:
                p(f"- run {g['n']} ({g['skill']}) invoking turn #{it['n']} alone: {fmt_secs(it['active_s'])}, {it['api_calls']} api, {it['tool_calls']} tools, {fmt_tokens(it['output'])} output")
        p("a run is an upper bound (it ends at the next run or a > 1h pause, not when the skill's work stopped): for a one-shot skill use the invoking-turn line; for a multi-phase skill use the turn table")
        p("")
    direct = [a for a in r["agents"] if not a["workflow_run"]]
    if direct:
        p("## sub-agents launched with the Agent tool (nested agents indented; async = launched in background)")
        p("| agent | type | model | depth | started | active | span | api | tools | output | cache read | hit | errors | cost |")
        p("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for a in direct[:40]:
            indent = "  " * (a["depth"] - 1) + ("└ " if a["depth"] > 1 else "")
            flag = " (async)" if a["async"] else ""
            p(f"| {indent}{a['agent_id'][:8]}{flag} | {a['type'] or '?'} | {a['model'] or '?'} | {a['depth']} | {short_ts(a['started'])} | {fmt_secs(a['active_s'])} | {fmt_secs(a['span_s'])} | {a['api_calls']} | {a['tool_calls']} | {fmt_tokens(a['output'])} | {fmt_tokens(a['cache_read'])} | {fmt_hit(a['cache_hit'])} | {a['errors']} | {fmt_cost(a['cost_usd'])} |")
        if len(direct) > 40:
            rest = direct[40:]
            p(f"(+{len(rest)} more agents, {fmt_cost(sum(a['cost_usd'] or 0 for a in rest))}, types {dict(Counter(a['type'] for a in rest))}; see JSON)")
        p("")
        p("## agent batches (agents whose active intervals overlap = ran concurrently; parallelism = sum of active / wall; an agent continued after a pause can sit in several, its cost prorated by active time)")
        for b in r["agent_batches"]:
            par = f"{b['parallelism']}x" if b["parallelism"] else "n/a"
            p(f"- {short_ts(b['started'])}: {len(b['agents'])} agents {b['types']} from {b['launch_messages']} launch message(s), wall {fmt_secs(b['wall_s'])}, active {fmt_secs(b['sum_active_s'])}, parallelism {par}, cost {fmt_cost(b['cost_usd'])}")
        p("")
    if r["workflows"]:
        p("## workflow runs (phases from workflows/wf_*.json; tokens/cost from the agent journals)")
        for w in r["workflows"]:
            wa = [a for a in r["agents"] if a["workflow_run"] == w["run_id"]]
            jt = Counter()
            for a in wa:
                jt.update({"output": a["output"], "cache_read": a["cache_read"], "cost": a["cost_usd"] or 0, "errors": a["errors"]})
            p(f"- {w['run_id']} {w['name'] or ''}: status {w['status']}, {w['agent_count']} agents, {w['total_tool_calls']} tool calls, {fmt_secs(w['duration_ms'] / 1000)}, default model {w['default_model']}, workflow-reported tokens {fmt_tokens(w['reported_tokens'])} (not comparable to journal totals)")
            if wa:
                p(f"    journals: {len(wa)} agents, output {fmt_tokens(jt['output'])}, cache read {fmt_tokens(jt['cache_read'])}, errors {jt['errors']}, cost {fmt_cost(jt['cost'])}, models {dict(Counter(a['model'] for a in wa))}")
            for ph in w["phases"]:
                p(f"    phase '{ph['title']}': {ph['agents']} agents, {ph['tool_calls']} tools, longest {fmt_secs(ph['duration_ms'] / 1000)}, retries {ph['retries']}, not-done {ph['not_done']}, models {ph['models']}")
        p("")
    cr = r["cache_rebuilds"]
    p(f"## cache prefix rebuilds (main-session calls that wrote >= {fmt_tokens(cr['min_tokens'])} tokens to the cache; cost = write cost of those calls)")
    if cr["by_cause"]:
        for cause, v in sorted(cr["by_cause"].items(), key=lambda kv: -kv[1]["tokens"]):
            p(f"- {cause}: {v['events']} events, {fmt_tokens(v['tokens'])} tokens, {fmt_cost(v['cost_usd'])}")
        big = sorted(cr["events"], key=lambda e: -e["tokens"])[:5]
        p("- largest: " + "; ".join(f"{short_ts(e['ts'])} {fmt_tokens(e['tokens'])} after {fmt_secs(e['gap_s'])} ({e['cause']})" for e in big))
    else:
        p("- none")
    p("")
    p("## compactions")
    if r["compactions"]:
        for c in r["compactions"]:
            p(f"- {c['ts']}: {c['trigger']} {fmt_tokens(c['pre_tokens'])} -> {fmt_tokens(c['post_tokens'])} in {fmt_secs((c['duration_ms'] or 0) / 1000)}")
    else:
        p("- none")
    p("")
    f = r["friction"]
    p("## friction")
    p(f"- tool errors: {f['tool_errors'] or 'none'}")
    for name, smps in f.get("tool_error_samples", {}).items():
        for smp in smps:
            p(f"    · {name}: {smp}")
    p(f"- permission denials: {f['denials'] or 'none'}")
    for smp in f["denial_samples"]:
        p(f"    · {smp}")
    p(f"- api errors: {len(f['api_errors'])}" + (f" (e.g. {f['api_errors'][0]['message'][:70]})" if f["api_errors"] else ""))
    p(f"- hook errors: {f['hook_errors']} | tool outputs spilled to files: {f['persisted_outputs']}")
    p("")
    c = r["context_tax"]
    tax_total = sum(x for x in (c["system_prompt_est_tokens"], c["skill_listing_est_tokens"], c["compact_summary_est_tokens"], c["system_reminder_est_tokens"], c["hook_est_tokens_total"]) if x) + sum(v["est_tokens"] for v in c["skill_loads"].values())
    p(f"## context tax (estimated tokens injected outside the model's own work; total ~{fmt_tokens(tax_total)})")
    p(f"- system prompt ~{fmt_tokens(c['system_prompt_est_tokens'])}, skill listing ~{fmt_tokens(c['skill_listing_est_tokens'])}, compact summaries ~{fmt_tokens(c['compact_summary_est_tokens'])}, system-reminders in prompts ~{fmt_tokens(c['system_reminder_est_tokens'])}")
    if c["hook_injections"]:
        p(f"- hooks ~{fmt_tokens(c['hook_est_tokens_total'])} total: " + ", ".join(f"{k} x{v['count']} ~{fmt_tokens(v['est_tokens'])}" + (f" ({v['errors']} err)" if v["errors"] else "") for k, v in sorted(c["hook_injections"].items(), key=lambda kv: -kv[1]["est_tokens"])))
    if c["skill_loads"]:
        p("- skill bodies loaded: " + ", ".join(f"{k} x{v['count']} ~{fmt_tokens(v['est_tokens'])}" for k, v in c["skill_loads"].items()))
    st = c["stop_hooks"]
    p(f"- stop hooks: {st['runs']} runs, {fmt_secs(st['duration_ms'] / 1000) if st['duration_ms'] is not None else 'n/a'} total, {st['errors']} run(s) reporting an error (hook error attachments are counted under friction)")
    p("")
    if r["duplicate_reads"]:
        p("## files read by more than one reader (main + agents; token estimate = full file x reads, an upper bound)")
        for d in r["duplicate_reads"][:10]:
            p(f"- {d['path']}: {d['readers']} readers, {d['reads']} reads, ~{fmt_tokens(d['est_tokens_each'])} tokens each (~{fmt_tokens(d['est_tokens_total'])} total)")
        p("")
    p("## turns (human prompt -> next human prompt)")
    p("| # | at | active | wait after | api | tools | output | agents | skills | prompt |")
    p("|---|---|---|---|---|---|---|---|---|---|")
    for t_ in r["turns"][-40:]:
        p(f"| {t_['n']} | {short_ts(t_['ts'])} | {fmt_secs(t_['active_s'])} | {fmt_secs(t_['wait_after_s']) if t_['wait_after_s'] is not None else '-'} | {t_['api_calls']} | {t_['tool_calls']} | {fmt_tokens(t_['output'])} | {t_['agents_launched']} | {','.join(t_['skills']) or '-'} | {cell(t_['prompt'][:60])} |")
    if len(r["turns"]) > 40:
        p(f"(first {len(r['turns']) - 40} turns omitted; see JSON)")
    return "\n".join(out)


# ---------------------------------------------------------------- main

def format_warnings(journal, verified, today=None):
    """Warnings about the inputs: stale prices, fast mode, a transcript shape the parser no longer reads."""
    warnings = []
    if verified:
        try:
            age = ((today or datetime.now()) - datetime.strptime(verified, "%Y-%m-%d")).days
            if age > 60:
                warnings.append(f"warning: prices.json was verified {age} days ago; re-check rates before trusting cost figures")
        except ValueError:
            pass
    fast = sum(1 for m in journal.messages.values() if (m.get("usage") or {}).get("speed") not in (None, "standard"))
    if fast:
        warnings.append(f"warning: {fast} api calls ran with a non-standard speed (fast mode); they are priced at standard rates here")
    no_usage = sum(1 for m in journal.messages.values() if not m.get("usage"))
    if journal.messages and no_usage > len(journal.messages) // 2:
        warnings.append(f"warning: {no_usage} of {len(journal.messages)} assistant messages carry no usage; token totals are unreliable (transcript format may have changed)")
    if journal.lines > 50 and not journal.human_turns:
        warnings.append("warning: no human prompts recognised in a non-empty transcript; turn and skill tables are empty (transcript format may have changed)")
    return warnings


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", required=True, help="session id, or a unique prefix (the skill passes ${CLAUDE_SESSION_ID})")
    ap.add_argument("--project-dir", default=os.getcwd())
    ap.add_argument("--sessions-dir", help="override ~/.claude/projects/<slug>")
    ap.add_argument("--out", help="directory for <id8>.json and <id8>-narrative.md (default: <project-dir>/<artifacts dir from project.md>/.data)")
    ap.add_argument("--prices", default=os.path.join(here, "..", "prices.json"))
    ap.add_argument("--project-md", default=os.path.join(here, "..", "project.md"), help="adapter file; supplies the artifacts directory for --out")
    ap.add_argument("--gap-cap", type=int, default=600, help="seconds; longer gaps count as idle")
    ap.add_argument("--exclude-last-turn", action="store_true", help="drop the final turn (the retro request itself)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of the text summary")
    args = ap.parse_args(argv)

    sessions_dir = sessions_dir_for(args.project_dir, args.sessions_dir)
    transcript = resolve_session(sessions_dir, args.session)
    prices, verified = load_prices(args.prices)
    result, journal = analyze(transcript, prices, verified, args.gap_cap, args.exclude_last_turn)
    warnings = format_warnings(journal, verified)
    result["unknown_record_types"] = dict(journal.unknown_types)
    if args.out is None:
        args.out = os.path.join(args.project_dir, artifacts_dir_from(args.project_md), ".data")
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        gw = gitignore_warning(args.out, args.project_dir)
        if gw:
            warnings.append(gw)
    result["warnings"] = warnings
    if args.out:
        id8 = result["session"]["id"][:8]
        json_path = os.path.join(args.out, f"{id8}.json")
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=1, default=str)
        narrative_path = os.path.join(args.out, f"{id8}-narrative.md")
        with open(narrative_path, "w", encoding="utf-8") as fh:
            fh.write(narrative(journal, result))
        result["json_path"], result["narrative_path"] = json_path, narrative_path
    if args.json:
        print(json.dumps(result, indent=1, default=str))
    else:
        print(render(result))
        if args.out:
            print(f"\nfull data: {result['json_path']}\nnarrative: {result['narrative_path']} ({os.path.getsize(result['narrative_path']) // 1024} KB; grep or read in parts when large)")
        for w in warnings:
            print(w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
