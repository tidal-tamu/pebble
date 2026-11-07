"""Small server-side client for Harp's bearer-authenticated integration API."""

from dataclasses import dataclass
from urllib.parse import quote

import requests


BASE_URL = "https://portal.tidaltamu.com/v1/integrations"
TIMEOUT_SECONDS = 5
STATUSES = ("draft", "submitted", "accepted", "rejected", "waitlisted")


class HarpError(Exception):
    """A safe, user-facing Harp failure message."""


@dataclass(frozen=True)
class RegistrationStats:
    total_started: int
    total_submitted: int
    by_status: dict[str, int]


@dataclass(frozen=True)
class QuestionOption:
    label: str
    count: int


@dataclass(frozen=True)
class QuestionStats:
    label: str
    total_submitted: int
    answered: int
    options: tuple[QuestionOption, ...]


def parse_registration_stats(payload) -> RegistrationStats:
    """Validate the response before any of its values reach Discord."""
    try:
        data = payload["data"]
        statuses = data["by_status"]
        started = data["total_started"]
        submitted = data["total_submitted"]
        counts = {status: statuses[status] for status in STATUSES}
        if any(type(value) is not int or value < 0 for value in (started, submitted, *counts.values())):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise HarpError("Harp returned registration stats in an unexpected format. Please try again later.") from None
    return RegistrationStats(started, submitted, counts)


def parse_question_stats(payload, question_id: str) -> QuestionStats:
    try:
        data = payload["data"]
        label = data["label"]
        submitted = data["total_submitted"]
        answered = data["answered"]
        options = data["options"]
        if (
            data["question_id"] != question_id
            or data["type"] not in ("select", "multi_select", "checkbox")
            or not isinstance(label, str)
            or type(submitted) is not int
            or type(answered) is not int
            or not 0 <= answered <= submitted
            or not isinstance(options, list)
        ):
            raise ValueError
        choices = tuple(QuestionOption(option["label"], option["count"]) for option in options)
        if any(
            not isinstance(choice.label, str)
            or type(choice.count) is not int
            or not 0 <= choice.count <= answered
            for choice in choices
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise HarpError("Harp returned application question stats in an unexpected format. Please try again later.") from None
    return QuestionStats(label, submitted, answered, choices)


class HarpClient:
    def __init__(self, api_key: str, *, base_url: str = BASE_URL):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def get(self, path: str, *, status_errors: dict[int, str] | None = None):
        if not self.api_key or self.api_key.startswith("your_"):
            raise HarpError("Harp is not configured. Ask a bot maintainer to set HARP_BOT_API_KEY.")
        try:
            response = requests.get(
                f"{self.base_url}/{path.lstrip('/')}",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=TIMEOUT_SECONDS,
            )
        except requests.RequestException:
            raise HarpError("Couldn't reach Harp. Please try again later.") from None
        if response.status_code != 200:
            message = (status_errors or {}).get(
                response.status_code, "Harp couldn't provide this information right now. Please try again later."
            )
            raise HarpError(message)
        try:
            return response.json()
        except ValueError:
            raise HarpError("Harp returned an unexpected response. Please try again later.") from None

    def registration_stats(self) -> RegistrationStats:
        return parse_registration_stats(self.get("registration/stats"))

    def question_stats(self, question_id: str) -> QuestionStats:
        payload = self.get(
            f"application/questions/{quote(question_id, safe='')}/stats",
            status_errors={
                401: "Harp rejected the bot credential. Ask a bot maintainer to check HARP_BOT_API_KEY.",
                403: "The bot credential is missing Harp's application_questions:read scope.",
                404: "Harp could not find question stats. Ask a bot maintainer to check the integration, field ID, type, and allowlist.",
            },
        )
        return parse_question_stats(payload, question_id)
