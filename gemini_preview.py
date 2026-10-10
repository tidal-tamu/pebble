"""Test Gemini with fictional messages only; never reads the Gmail token."""

from config import ConfigurationError
from gemini import GeminiClient, GeminiError, GeminiSettings

from email_samples import SYNTHETIC_THREADS


def main():
    try:
        settings = GeminiSettings.from_env()
        print("Testing Gemini with four fictional threads. No Gmail content is accessed.", flush=True)
        digest = GeminiClient(settings).summarize(SYNTHETIC_THREADS)
    except (ConfigurationError, GeminiError) as error:
        print("Preview failed: " + str(error))
        return 1
    print("Model: " + settings.model)
    print("\nOverview\n" + digest["overview"])
    for section, title in [
        ("action_items", "Action items"),
        ("awaiting_our_reply", "Possibly awaiting our reply"),
        ("waiting_on_others", "Waiting on others"),
    ]:
        print("\n" + title)
        for item in digest[section]:
            description = item.get("action", item.get("reason", ""))
            print(f"- [{item['thread_id']}] {description}")
            if section == "action_items":
                print(f"  Owner: {item['owner']}; Deadline: {item['deadline']}")
        if not digest[section]:
            print("- None identified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
