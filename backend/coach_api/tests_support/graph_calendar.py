"""Stateful Graph transport double; application verification runs unpatched."""
from copy import deepcopy
from datetime import datetime, timedelta
from urllib.parse import quote


class GraphCalendar:
    def __init__(self, *, event_id, meeting_id, join_suffix, owner="tutor@example.com",
                 starts=(), duration=60, attendees=(), subject="Risk module"):
        self.path = f"users/{quote(owner, safe='')}/events/{event_id}"
        self.meeting_id = meeting_id
        self.event = {
            "id": event_id, "subject": subject, "hideAttendees": True,
            "onlineMeeting": {"joinUrl": f"https://teams.microsoft.com/l/meetup-join/{join_suffix}"},
            "webLink": "https://outlook.office.com/calendar/item",
            "attendees": [{"emailAddress": {"address": address}} for address in attendees],
        }
        self.instances = []
        for number, start in enumerate(starts, 1):
            instant = datetime.fromisoformat(start.replace("Z", "+00:00"))
            self.instances.append({**deepcopy(self.event), "id": f"instance-{number}",
                "start": {"dateTime": instant.isoformat(), "timeZone": "UTC"},
                "end": {"dateTime": (instant + timedelta(minutes=duration)).isoformat(), "timeZone": "UTC"}})
        if self.instances:
            self.event.update({key: self.instances[0][key] for key in ("start", "end")})

    def __call__(self, method, path, payload=None, **kwargs):
        if method == "POST" and path == self.path.rsplit("/", 1)[0]:
            self.event.update(deepcopy(payload))
            return deepcopy(self.event)
        if method == "GET" and path == self.path:
            return deepcopy(self.event)
        if method == "GET" and path.startswith(self.path + "/instances?"):
            return {"value": deepcopy(self.instances)}
        if method == "PATCH" and path == self.path:
            self.event.update(deepcopy(payload))
            if "attendees" in payload:
                for instance in self.instances:
                    instance["attendees"] = deepcopy(payload["attendees"])
            return deepcopy(self.event)
        if method == "GET" and "/onlineMeetings?" in path:
            return {"value": [{"id": self.meeting_id}]}
        if method == "PATCH" and path.endswith("/onlineMeetings/" + self.meeting_id):
            return {}
        if method == "POST" and path.endswith("/forward"):
            return {}
        raise AssertionError(f"Unexpected Graph request: {method} {path}")
