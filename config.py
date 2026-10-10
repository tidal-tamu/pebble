"""Validated startup settings shared by the bot and its integrations."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
import os
import re

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent


class ConfigurationError(ValueError):
    """Configuration needs attention before Pebble can start."""


@dataclass(frozen=True)
class Settings:
    discord_token: str = field(repr=False)
    spreadsheet_id: str
    officer_role_id: int
    reminder_channel_id: int
    guild_id: int | None = None
    credentials_file: Path = ROOT / "service_account.json"
    log_level: str = "INFO"
    email_summary_mode: str = "disabled"
    gmail_token_file: Path = ROOT / "secrets" / "gmail_token.json"
    gmail_expected_account: str = ""
    gemini_api_key: str = field(default="", repr=False)
    gemini_model: str = "gemini-3.5-flash-lite"
    harp_bot_api_key: str = field(default="", repr=False)

    @classmethod
    def from_env(cls):
        # Explicit environment variables take precedence over the local .env file.
        load_dotenv(ROOT / ".env")
        return cls.from_mapping(os.environ)

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]):
        def required(name):
            value = values.get(name, "").strip()
            if not value or value.startswith("your_"):
                raise ConfigurationError(f"Set {name} in the environment or .env.")
            return value

        def discord_id(name, optional=False):
            value = values.get(name, "").strip()
            if optional and not value:
                return None
            value = required(name)
            if not value.isascii() or not value.isdigit() or int(value) <= 0:
                raise ConfigurationError(f"{name} must be a positive Discord ID.")
            return int(value)

        spreadsheet_id = required("SPREADSHEET_ID")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", spreadsheet_id):
            raise ConfigurationError("SPREADSHEET_ID must be the ID between /d/ and /edit, not a URL.")
        credentials_file = Path(values.get("GOOGLE_CREDENTIALS_FILE", "service_account.json"))
        if not credentials_file.is_absolute():
            credentials_file = ROOT / credentials_file
        log_level = values.get("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigurationError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL.")
        email_mode = values.get("EMAIL_SUMMARY_MODE", "disabled").strip().lower()
        if email_mode not in {"disabled", "demo", "live"}:
            raise ConfigurationError("EMAIL_SUMMARY_MODE must be disabled, demo, or live.")
        gmail_token_file = Path(values.get("GMAIL_TOKEN_FILE", "secrets/gmail_token.json"))
        if not gmail_token_file.is_absolute():
            gmail_token_file = ROOT / gmail_token_file
        gemini_key = values.get("GEMINI_API_KEY", "").strip()
        gemini_model = values.get("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
        gmail_account = values.get("GMAIL_EXPECTED_ACCOUNT", "").strip().lower()
        if email_mode != "disabled":
            if not gemini_key or gemini_key.startswith("your_"):
                raise ConfigurationError("Set GEMINI_API_KEY before enabling email summaries.")
            if not re.fullmatch(r"[A-Za-z0-9._-]+", gemini_model):
                raise ConfigurationError("GEMINI_MODEL must be a model name, not a URL.")
        if email_mode == "live" and not gmail_account:
            raise ConfigurationError("Set GMAIL_EXPECTED_ACCOUNT before enabling live email summaries.")
        return cls(
            discord_token=required("DISCORD_TOKEN"),
            spreadsheet_id=spreadsheet_id,
            officer_role_id=discord_id("OFFICER_ROLE_ID"),
            reminder_channel_id=discord_id("REMINDER_CHANNEL_ID"),
            guild_id=discord_id("GUILD_ID", optional=True),
            credentials_file=credentials_file,
            log_level=log_level,
            email_summary_mode=email_mode,
            gmail_token_file=gmail_token_file,
            gmail_expected_account=gmail_account,
            gemini_api_key=gemini_key,
            gemini_model=gemini_model,
            harp_bot_api_key=values.get("HARP_BOT_API_KEY", "").strip(),
        )
