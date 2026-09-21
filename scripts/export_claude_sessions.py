#!/usr/bin/env python3
"""Export redacted Claude Code session summaries as Markdown."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


SECRET_PATTERNS = [
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password|authorization)\b\s*[:=]\s*[^\s,;]+"),
    re.compile(r"\b(?:ghp|github_pat|sk-ant|sk-proj|sk)-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{12,}=*"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\b(?:xox[baprs]-|npm_)[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]*?-----END [A-Z ]+PRIVATE KEY-----"),
]


def redact(value: str) -> str:
    value = value.replace(str(Path.home()), "~")
    for pattern in SECRET_PATTERNS:
        value = pattern.sub("[REDACTED]", value)
    return value


def compact(value: str, limit: int = 500) -> str:
    value = re.sub(r"\s+", " ", redact(value)).strip()
    if len(value) > limit:
        return value[: limit - 1].rstrip() + "…"
    return value


def content_blocks(message: Any) -> Iterable[dict[str, Any]]:
    if not isinstance(message, dict):
        return []
    content = message.get("content", [])
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [item for item in content if isinstance(item, dict)] if isinstance(content, list) else []


def text_from_message(message: Any) -> list[str]:
    return [
        str(block.get("text", ""))
        for block in content_blocks(message)
        if block.get("type") == "text" and str(block.get("text", "")).strip()
    ]


def tool_name(block: dict[str, Any]) -> str:
    return str(block.get("name") or "unknown")


def file_candidates(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in {"file_path", "path", "notebook_path"} and isinstance(child, str):
                yield child
            yield from file_candidates(child)
    elif isinstance(value, list):
        for child in value:
            yield from file_candidates(child)


def safe_project_name(cwd: str, transcript: Path) -> str:
    if cwd:
        return Path(cwd).name or "unknown-project"
    return transcript.parent.name.strip("-").split("-")[-1] or "unknown-project"


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:60] or "session"


def load_events(path: Path) -> list[dict[str, Any]]:
    events = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
    return events


def render_summary(transcript: Path) -> tuple[str, str] | None:
    events = load_events(transcript)
    if not events:
        return None

    session_id = next((str(e.get("sessionId")) for e in events if e.get("sessionId")), transcript.stem)
    timestamps = [str(e.get("timestamp")) for e in events if e.get("timestamp")]
    cwd = next((str(e.get("cwd")) for e in events if e.get("cwd")), "")
    project = safe_project_name(cwd, transcript)
    title = next((compact(str(e.get("aiTitle")), 120) for e in events if e.get("type") == "ai-title"), "")

    prompts: list[str] = []
    outcomes: list[str] = []
    errors: list[str] = []
    files: set[str] = set()
    tools: Counter[str] = Counter()

    for event in events:
        kind = event.get("type")
        message = event.get("message")
        if kind == "user":
            prompts.extend(compact(text) for text in text_from_message(message) if compact(text))
        elif kind == "assistant":
            blocks = list(content_blocks(message))
            texts = [compact(text) for text in text_from_message(message) if compact(text)]
            if texts:
                outcomes.append(" ".join(texts))
            for block in blocks:
                if block.get("type") == "tool_use":
                    tools[tool_name(block)] += 1
                    for candidate in file_candidates(block.get("input")):
                        files.add(redact(candidate))
                if block.get("type") == "tool_result" and block.get("is_error"):
                    errors.append(compact(str(block.get("content", "Unknown tool error")), 350))
        elif kind == "file-history-delta" and event.get("trackingPath"):
            files.add(redact(str(event["trackingPath"])))

    # Last assistant text is generally the clearest statement of the completed outcome.
    outcome = outcomes[-1] if outcomes else "No final assistant outcome was recorded."
    date = (timestamps[0][:10] if timestamps else datetime.fromtimestamp(transcript.stat().st_mtime).date().isoformat())
    display_title = title or (prompts[0][:100] if prompts else f"Claude session {session_id[:8]}")
    filename = f"{date}/{slug(project)}--{session_id}.md"

    lines = [
        f"# {display_title}",
        "",
        f"- Date: {date}",
        f"- Project: `{project}`",
        f"- Session ID: `{session_id}`",
        f"- Started: {timestamps[0] if timestamps else 'unknown'}",
        f"- Last activity: {timestamps[-1] if timestamps else 'unknown'}",
        "",
        "## Work requested",
        "",
    ]
    lines.extend(f"- {item}" for item in prompts[:8])
    if len(prompts) > 8:
        lines.append(f"- …and {len(prompts) - 8} additional prompt(s).")
    if not prompts:
        lines.append("- No user prompt text was recorded.")

    lines += ["", "## Outcome", "", outcome, "", "## Files referenced or changed", ""]
    lines.extend(f"- `{compact(item, 300)}`" for item in sorted(files))
    if not files:
        lines.append("- No file paths were recorded.")

    lines += ["", "## Tool activity", ""]
    lines.extend(f"- `{name}`: {count}" for name, count in sorted(tools.items()))
    if not tools:
        lines.append("- No tool calls were recorded.")

    lines += ["", "## Errors", ""]
    lines.extend(f"- {item}" for item in errors[:20])
    if not errors:
        lines.append("- No tool errors were recorded.")

    digest = hashlib.sha256(transcript.read_bytes()).hexdigest()
    lines += [
        "",
        "---",
        "Generated automatically from the local Claude Code transcript.",
        f"Source digest: `{digest}`",
        "Raw transcript content is not stored in this repository.",
        "",
    ]
    return filename, "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path.home() / ".claude" / "projects")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    # Claude may duplicate a session transcript across project directories. Keep the
    # largest copy, which is normally the most complete, for each destination file.
    summaries: dict[str, tuple[int, str]] = {}
    for transcript in sorted(args.source.rglob("*.jsonl")):
        result = render_summary(transcript)
        if not result:
            continue
        relative, content = result
        size = transcript.stat().st_size
        if relative not in summaries or size > summaries[relative][0]:
            summaries[relative] = (size, content)

    written = 0
    for relative, (_, content) in sorted(summaries.items()):
        destination = args.output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists() or destination.read_text(encoding="utf-8") != content:
            destination.write_text(content, encoding="utf-8")
            written += 1
    print(f"Updated {written} session summary file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
