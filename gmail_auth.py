"""Authorize read-only Gmail access locally, or check an existing saved token.

This helper checks mailbox metadata only. It does not fetch email bodies,
call Gemini, or connect the Discord bot.
"""

import argparse
import json
import os
from pathlib import Path
import tempfile

from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from config import ROOT

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
DEFAULT_TOKEN_FILE = ROOT / "secrets" / "gmail_token.json"


class AuthorizationError(RuntimeError):
    """Safe setup guidance without token values or raw API response bodies."""


def save_token(credentials, destination):
    """Atomically save credentials; do not leave a partial token after a failure."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent, delete=False
        ) as output:
            temporary = Path(output.name)
            output.write(credentials.to_json())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def verify_access(credentials):
    with AuthorizedSession(credentials) as session:
        response = session.get(PROFILE_URL, timeout=(5, 20))
        if response.status_code != 200:
            raise AuthorizationError(
                f"Gmail access failed (HTTP {response.status_code}). Check that Gmail API is enabled and read-only access was granted."
            )
        profile = response.json()
    if not profile.get("emailAddress"):
        raise AuthorizationError("Google did not return a mailbox identity.")
    return profile


def authorize(args):
    token_file = args.token_file.expanduser().resolve()
    if token_file.exists() and not args.reauthorize:
        credentials = Credentials.from_authorized_user_file(token_file, SCOPES)
        if not credentials.valid:
            if not credentials.refresh_token:
                raise AuthorizationError("No refresh token is available. Rerun with --reauthorize and --client-file.")
            # Bound the refresh request as well as the Gmail API request.
            credentials.refresh(lambda **kwargs: Request()(**dict(kwargs, timeout=20)))
    else:
        if args.check_only:
            raise AuthorizationError("No token exists at the selected path. Authorize locally first.")
        if args.client_file is None:
            raise AuthorizationError("Specify --client-file with the downloaded Desktop app OAuth JSON.")
        client_file = args.client_file.expanduser().resolve()
        data = json.loads(client_file.read_text(encoding="utf-8-sig"))
        if "installed" not in data:
            raise AuthorizationError("The OAuth client must be a Desktop app.")
        flow = InstalledAppFlow.from_client_config(data, SCOPES)
        print("Opening Google sign-in. Select the club Gmail account and grant read-only access.", flush=True)
        credentials = flow.run_local_server(
            host="localhost", bind_addr="127.0.0.1", port=0,
            authorization_prompt_message="",
            success_message="Pebble Gmail authorization complete. You can close this tab.",
            timeout_seconds=300, access_type="offline", prompt="consent",
        )
    profile = verify_access(credentials)
    if args.expected_account and profile["emailAddress"].lower() != args.expected_account.lower():
        raise AuthorizationError("The selected mailbox differs from --expected-account. Rerun with --reauthorize and select the club account.")
    if not credentials.refresh_token:
        raise AuthorizationError("Google did not issue a refresh token. Rerun with --reauthorize.")
    save_token(credentials, token_file)
    print("Gmail read-only access: OK")
    print("Connected mailbox: " + profile["emailAddress"])
    print("Refresh token present: yes")
    print("Saved token: " + str(token_file))
    print("No email bodies were retrieved and no data was sent to Gemini.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-file", type=Path, help="Downloaded Desktop app OAuth client JSON.")
    parser.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_FILE)
    parser.add_argument("--expected-account", help="Optional club mailbox address to verify before saving.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--reauthorize", action="store_true", help="Obtain a new token, e.g. after publishing to Production.")
    mode.add_argument("--check-only", action="store_true", help="Verify a saved token without opening a browser.")
    args = parser.parse_args()
    try:
        authorize(args)
    except AuthorizationError as error:
        print("Setup error: " + str(error))
        return 1
    except Exception as error:
        print(f"Setup failed ({type(error).__name__}). Check the file paths, Google authorization, and network access; rerun with --reauthorize if the token was revoked or expired.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
