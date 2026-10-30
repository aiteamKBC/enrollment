"""Request-scoped data shared by Dashboard selectors."""

from dataclasses import dataclass, field
from datetime import date


@dataclass
class CoachDashboardContext:
    owner_email: str
    today: date
    rows: list = field(default_factory=list)
    learners: list[dict] = field(default_factory=list)

    @property
    def profile_ids(self) -> list[int]:
        return [int(row.id) for row in self.rows]

    @property
    def enrolment_ids(self) -> list[str]:
        return [
            str(row.enrolment_id)
            for row in self.rows
            if getattr(row, "enrolment_id", None)
        ]
