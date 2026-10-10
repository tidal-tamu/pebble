"""Fictional conversations for the email summarization demo."""

SYNTHETIC_THREADS = [
    {
        "thread_id": "example-1", "subject": "Sponsor logo needed",
        "messages": [{"direction": "incoming", "body": "Please send the club logo by October 12, 2026 so we can finish the sponsor flyer. Club officer Morgan should coordinate this."}],
    },
    {
        "thread_id": "example-2", "subject": "Room booking",
        "messages": [
            {"direction": "incoming", "body": "Can you confirm you need the meeting room?"},
            {"direction": "outgoing", "body": "Yes, we confirm the booking. Thank you."},
            {"direction": "incoming", "body": "Your booking is confirmed. No further action is needed."},
        ],
    },
    {
        "thread_id": "example-3", "subject": "Catering quote",
        "messages": [{"direction": "outgoing", "body": "Could you send a quote for 100 lunches? We are waiting for pricing before deciding."}],
    },
    {
        "thread_id": "example-4", "subject": "Weekly campus newsletter",
        "messages": [{"direction": "incoming", "body": "Here are this week's campus events. This is an automated newsletter. No reply is requested."}],
    },
]

