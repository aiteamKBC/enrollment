"""Validated request-independent context for the Coach Caseload endpoint."""

from dataclasses import dataclass


CASELOAD_PAGINATION_PARAMETERS = frozenset({
    "page", "page_size", "search", "status", "cohort", "group", "sort", "direction",
})


@dataclass(frozen=True)
class CaseloadRequestContext:
    summary_only: bool
    paginated: bool
    query_string: str

    @classmethod
    def from_request(cls, request):
        summary = str(request.GET.get("summary") or "").strip().casefold()
        return cls(
            summary_only=summary in {"1", "true", "yes", "on"},
            paginated=any(key in request.GET for key in CASELOAD_PAGINATION_PARAMETERS),
            query_string=request.META.get("QUERY_STRING", ""),
        )
