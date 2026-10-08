"""Send one explicit test notification using the configured local secrets."""

import argparse

from notifications import NotificationEvent, Notifier


QUEUES = {
    "eec-calls": ("EEC Calls", "call"),
    "eec-chats": ("EEC Chats", "chat"),
    "service-coordinators-calls": ("Service Coordinators Calls", "call"),
    "rapid-response-pro-calls": ("Rapid Response Pro Calls", "call"),
}

parser = argparse.ArgumentParser()
parser.add_argument("--destination", choices=("teams", "outlook", "both"), required=True)
parser.add_argument("--queue-id", choices=tuple(QUEUES), default="eec-calls")
args = parser.parse_args()

queue_name, queue_type = QUEUES[args.queue_id]
Notifier(args.destination).send(NotificationEvent("TEST", args.queue_id, queue_name, queue_type, "00:02:00", 120, 120))
print(f"Test notification sent for {queue_name} via {args.destination}.")
