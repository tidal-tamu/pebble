"""Read-only Gmail selection and MIME parsing, with bounded work and output."""

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials
import requests

from gmail_auth import SCOPES

API = "https://gmail.googleapis.com/gmail/v1/users/me"
PERIODS = {"24h": timedelta(hours=24), "7d": timedelta(days=7)}
MAX_THREADS = 20
MAX_MESSAGES = 20
MAX_BODY = 3000
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class GmailError(RuntimeError):
    """Safe Gmail error suitable for a private command response."""


@dataclass
class ThreadSelection:
    threads: list
    warnings: list[str]
    start: datetime
    end: datetime
    mailbox: str


class _VisibleHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.output = []

    def handle_starttag(self, tag, attrs):
        hidden = tag in {"script", "style", "blockquote"} or any(
            name == "class" and "gmail_quote" in (value or "") for name, value in attrs
        )
        if tag not in {"br", "img", "hr", "meta", "link", "input", "wbr"}:
            self.stack.append((tag, hidden))
        if tag in {"br", "p", "div", "li", "tr"} and not any(hidden for _, hidden in self.stack):
            self.output.append("\n")

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                self.stack = self.stack[:index]
                break
        if tag in {"p", "div", "li", "tr"}:
            self.output.append("\n")

    def handle_data(self, text):
        if not any(hidden for _, hidden in self.stack):
            self.output.append(text)


def _decode(part):
    data = part.get("body", {}).get("data", "")
    if not data:
        return ""
    try:
        raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
        headers = {h["name"].lower(): h["value"] for h in part.get("headers", [])}
        match = re.search(r"charset=[\"']?([\w-]+)", headers.get("content-type", ""), re.I)
        charset = match.group(1) if match else "utf-8"
        return raw.decode(charset, errors="replace")
    except (ValueError, LookupError):
        return ""


def _texts(part):
    # Attachments are never fetched, including text files attached to a message.
    if part.get("filename") or part.get("mimeType") == "message/rfc822":
        return [], []
    plain, html = [], []
    if part.get("mimeType") == "text/plain":
        plain.append(_decode(part))
    elif part.get("mimeType") == "text/html":
        html.append(_decode(part))
    for child in part.get("parts", []):
        child_plain, child_html = _texts(child)
        plain.extend(child_plain)
        html.extend(child_html)
    return plain, html


def message_text(payload):
    plain, html = _texts(payload)
    plain = [text for text in plain if text.strip()]
    if plain:
        text = "\n".join(plain)
    else:
        parser = _VisibleHTML()
        parser.feed("\n".join(html))
        text = "".join(parser.output)
    lines = []
    for line in text.splitlines():
        if re.match(r"^\s*On .+wrote:\s*$", line) or line.strip() in {"-----Original Message-----", "Begin forwarded message:"}:
            break
        if not line.lstrip().startswith(">"):
            lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def parse_thread(raw, mailbox):
    all_messages = sorted(raw.get("messages", []), key=lambda m: int(m.get("internalDate", "0")))
    messages = []
    shortened = len(all_messages) > MAX_MESSAGES
    unreadable = False
    for message in all_messages[-MAX_MESSAGES:]:
        labels = message.get("labelIds", [])
        if any(label in labels for label in ["DRAFT", "SPAM", "TRASH"]):
            continue
        payload = message.get("payload", {})
        headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
        # Login and tracking URLs can contain credentials and consume the body
        # budget. Officers open the original through the generated Gmail link.
        body = re.sub(r"https?://[^\s<>]+", "[Link omitted]", message_text(payload), flags=re.I)
        shortened |= len(body) > MAX_BODY
        unreadable |= not bool(body)
        timestamp = datetime.fromtimestamp(int(message.get("internalDate", "0")) / 1000, timezone.utc)
        messages.append({
            "direction": "outgoing" if "SENT" in labels or parseaddr(headers.get("from", ""))[1].lower() == mailbox.lower() else "incoming",
            "from": headers.get("from", "")[:200],
            "date": timestamp.isoformat(),
            "body": body[:MAX_BODY] if body else "[No readable text; attachments were not inspected.]",
        })
    headers = all_messages[0].get("payload", {}).get("headers", []) if all_messages else []
    subject = next((h["value"] for h in headers if h["name"].lower() == "subject"), "(No subject)")
    return {"thread_id": raw["id"], "subject": subject[:200], "messages": messages}, shortened, unreadable


class GmailClient:
    def __init__(self, token_file: Path, expected_account: str):
        self.token_file = token_file
        self.expected_account = expected_account.lower()

    def _get(self, session, resource, deadline, params=None):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise GmailError("Gmail selection timed out. Try the shorter period later.")
        with session.get(f"{API}/{resource}", params=params, timeout=(5, min(20, remaining)), stream=True) as response:
            if response.status_code != 200:
                raise GmailError(f"Gmail request failed (HTTP {response.status_code}). Check authorization, API access, and quota.")
            content = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                if time.monotonic() > deadline:
                    raise GmailError("Gmail selection timed out. Try again later.")
                content.extend(chunk)
                if len(content) > MAX_RESPONSE_BYTES:
                    raise GmailError("A Gmail response is too large to process safely. Narrow the period.")
            return json.loads(content)

    def select_threads(self, period, now=None):
        if period not in PERIODS:
            raise GmailError("Choose a period of 24h or 7d.")
        end = now or datetime.now(timezone.utc)
        if end.tzinfo is None:
            raise GmailError("The selection clock must include a timezone.")
        start = end - PERIODS[period]
        query = f"after:{int(start.timestamp())} before:{int(end.timestamp())} -in:spam -in:trash -in:drafts"
        deadline = time.monotonic() + 120
        try:
            credentials = Credentials.from_authorized_user_file(self.token_file, SCOPES)
        except (OSError, ValueError) as error:
            raise GmailError("Can't load the Gmail token. Run the local Gmail authorization helper and check GMAIL_TOKEN_FILE.") from error
        warnings = []
        try:
            with AuthorizedSession(credentials, refresh_timeout=20) as session:
                profile = self._get(session, "profile", deadline)
                mailbox = profile["emailAddress"]
                if not self.expected_account or mailbox.lower() != self.expected_account:
                    raise GmailError("The Gmail token doesn't match GMAIL_EXPECTED_ACCOUNT. Reauthorize the intended mailbox.")
                ids, seen, page = [], set(), None
                for _ in range(MAX_THREADS):
                    params = {"q": query, "maxResults": MAX_THREADS - len(ids)}
                    if page:
                        params["pageToken"] = page
                    result = self._get(session, "threads", deadline, params)
                    for thread in result.get("threads", []):
                        if thread["id"] not in seen:
                            seen.add(thread["id"])
                            ids.append(thread["id"])
                    page = result.get("nextPageToken")
                    if not page or len(ids) >= MAX_THREADS:
                        break
                if page:
                    warnings.append("Thread selection was limited to at most 20 conversations; additional matches were omitted.")
                threads = []
                for thread_id in ids[:MAX_THREADS]:
                    raw = self._get(session, "threads/" + thread_id, deadline, {"format": "full"})
                    thread, shortened, unreadable = parse_thread(raw, mailbox)
                    if shortened:
                        warnings.append("Some bodies or older messages were shortened; reply classification may be incomplete.")
                    if unreadable:
                        warnings.append("Some messages had no readable text; attachments and encrypted content were not inspected.")
                    if thread["messages"]:
                        threads.append(thread)
        except (GoogleAuthError, requests.RequestException) as error:
            raise GmailError("Gmail authorization or network access failed. Check the connection; an expired or revoked token needs local reauthorization.") from error
        except (KeyError, TypeError, ValueError) as error:
            raise GmailError("Gmail returned an unexpected response. Please try again later.") from error
        return ThreadSelection(threads, list(dict.fromkeys(warnings)), start, end, mailbox)
