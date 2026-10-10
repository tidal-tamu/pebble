"""Small server-side client for Harp's bearer-authenticated integration API."""

from dataclasses import dataclass

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


class HarpClient:
    def __init__(self, api_key: str, *, base_url: str = BASE_URL):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def get(self, path: str):
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
            raise HarpError("Harp couldn't provide this information right now. Please try again later.")
        try:
            return response.json()
        except ValueError:
            raise HarpError("Harp returned an unexpected response. Please try again later.") from None

    def registration_stats(self) -> RegistrationStats:
        return parse_registration_stats(self.get("registration/stats"))
