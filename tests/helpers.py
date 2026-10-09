from config import Settings


def settings(**overrides):
    values = {
        "DISCORD_TOKEN": "test-token",
        "SPREADSHEET_ID": "test-sheet-id",
        "OFFICER_ROLE_ID": "123",
        "REMINDER_CHANNEL_ID": "456",
        "GUILD_ID": "789",
    }
    values.update(overrides)
    return Settings.from_mapping(values)
