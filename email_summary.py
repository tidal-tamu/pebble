"""Email summarization orchestration and Discord-safe page formatting."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from urllib.parse import quote

import discord

from email_samples import SYNTHETIC_THREADS
from gemini import GeminiClient, GeminiSettings, MAX_INPUT_CHARACTERS, SECTIONS
from gmail import GmailClient, PERIODS, ThreadSelection

MAX_BATCHES = 3


@dataclass
class SummaryResult:
    digest: dict
    selection: ThreadSelection
    model: str
    demo: bool


def build_batches(threads):
    batches, current, warnings = [], [], []
    omitted = 0
    for original in threads:
        thread = deepcopy(original)
        while len(json.dumps({"threads": [thread]}, ensure_ascii=False)) > MAX_INPUT_CHARACTERS and len(thread["messages"]) > 1:
            thread["messages"].pop(0)
            warnings.append("Older messages in some threads were omitted to fit the model input limit.")
        if len(json.dumps({"threads": [thread]}, ensure_ascii=False)) > MAX_INPUT_CHARACTERS:
            omitted += 1
            continue
        if current and len(json.dumps({"threads": [*current, thread]}, ensure_ascii=False)) > MAX_INPUT_CHARACTERS:
            batches.append(current)
            current = []
        if len(batches) >= MAX_BATCHES:
            omitted += 1
            continue
        current.append(thread)
    if current:
        batches.append(current)
    if omitted:
        warnings.append(f"{omitted} thread(s) were omitted to keep the summary within three bounded requests.")
    return batches, list(dict.fromkeys(warnings))


class EmailSummaryService:
    def __init__(self, settings):
        self.settings = settings
        self.gemini = GeminiClient(GeminiSettings(settings.gemini_api_key, settings.gemini_model))

    def run(self, period):
        if period not in PERIODS:
            raise ValueError("Unsupported period.")
        if self.settings.email_summary_mode == "demo":
            now = datetime.now(timezone.utc)
            selection = ThreadSelection(deepcopy(SYNTHETIC_THREADS), [
                "DEMO: fictional emails only. Both periods use the same four example conversations."
            ], now - PERIODS[period], now, "")
        elif self.settings.email_summary_mode == "live":
            selection = GmailClient(self.settings.gmail_token_file, self.settings.gmail_expected_account).select_threads(period)
        else:
            raise ValueError("Email summaries are disabled.")
        batches, warnings = build_batches(selection.threads)
        selection.warnings.extend(warnings)
        selection.threads = [thread for batch in batches for thread in batch]
        digest = {"overview": "No email conversations are available to summarize in this period.", **{section: [] for section in SECTIONS}}
        overviews = []
        for batch in batches:
            partial = self.gemini.summarize(batch)
            overviews.append(partial["overview"])
            for section in SECTIONS:
                digest[section].extend(partial[section])
        if overviews:
            digest["overview"] = "\n\n".join(overviews)
        return SummaryResult(digest, selection, self.settings.gemini_model, self.settings.email_summary_mode == "demo")


def _safe(text):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(text)))


def summary_pages(result, limit=3500):
    sources = {thread["thread_id"]: thread for thread in result.selection.threads}
    def reference(thread_id):
        thread = sources[thread_id]
        subject = _safe(thread.get("subject", "(No subject)"))
        if result.demo or not re.fullmatch(r"[a-fA-F0-9]+", thread_id):
            return "Source: " + subject
        # authuser selects the connected account rather than assuming browser account 0.
        url = f"https://mail.google.com/mail/?authuser={quote(result.selection.mailbox, safe='')}#all/{thread_id}"
        return f"[{subject}]({url})"
    lines = [f"Conversations summarized: **{len(sources)}**"]
    if not result.demo:
        lines.append(f"Window: {result.selection.start:%Y-%m-%d %H:%M} to {result.selection.end:%Y-%m-%d %H:%M} UTC. Older replies may be included for context.")
    lines.extend(_safe(warning) for warning in result.selection.warnings)
    lines.extend(["", "**Overview**", _safe(result.digest["overview"])])
    for section, label in [("action_items", "Action items"), ("awaiting_our_reply", "Possibly awaiting our reply"), ("waiting_on_others", "Waiting on others")]:
        lines.extend(["", f"**{label}**"])
        for item in result.digest[section]:
            lines.append("• " + _safe(item.get("action", item.get("reason", ""))))
            if section == "action_items":
                lines.append(f"Owner: {_safe(item['owner'])} · Deadline: {_safe(item['deadline'])}")
            lines.append(reference(item["thread_id"]))
        if not result.digest[section]:
            lines.append("None identified.")
    lines.extend(["", "AI-generated; verify actions and reply status against the source conversations."])
    text = "\n".join(lines)
    pages = []
    while len(text) > limit:
        split = text.rfind("\n", 0, limit)
        if split <= 0:
            split = limit
        pages.append(text[:split])
        text = text[split:].lstrip("\n")
    if text:
        pages.append(text)
    return pages
