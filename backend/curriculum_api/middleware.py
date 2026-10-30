"""Curriculum request middleware: schema errors, and a log of what was written.

``SchemaNotProvisioned`` means required Curriculum tables are absent. Before the
schema-ownership refactor, request handlers would have silently created them;
now they refuse and say so. Surfacing that as a 503 with the missing relations
named makes it an obvious deployment/configuration problem rather than an opaque
500 that looks like a code bug.
"""
from __future__ import annotations

import logging

from django.http import JsonResponse

from .schema_gate import SchemaNotProvisioned

logger = logging.getLogger(__name__)

CURRICULUM_API_PREFIX = '/curriculum_api'
SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS', 'TRACE'})


class CurriculumChangeLogMiddleware:
    """Record which API path moved the cache epoch, for the pollers to read.

    A client in another country learns that somebody saved by watching the shared
    counter. The counter alone cannot say *what* was saved, so every tab that saw
    it move dropped its entire cache and read everything back -- for one edit to
    one module, across every open tab, every time anybody wrote anything.

    Naming the path is what makes that proportionate, and this is the one place
    that can do it cheaply: `invalidate_curriculum_cache()` has 94 call sites and
    is invoked from deep inside write helpers that never see a request, while the
    request knows its own path and nothing else. So the epoch is read either side
    of the view and the span attributed to the path that produced it.

    A write that bumps nothing records nothing, and a failed request is not a
    change -- both simply leave the log alone.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method in SAFE_METHODS or not request.path.startswith(CURRICULUM_API_PREFIX):
            return self.get_response(request)
        # Imported here rather than at module scope: views.py is large and
        # imports plenty of its own, and middleware is constructed during
        # settings load.
        from .views import (
            begin_curriculum_write_scope,
            curriculum_write_pending,
            end_curriculum_write_scope,
            record_curriculum_change,
            shared_curriculum_epoch,
        )

        from .cache_scope import affected_programmes, write_seeds

        before = shared_curriculum_epoch()
        # Which programmes this save can change, read before the view so a
        # module or group moved out of a programme still retires the old one.
        # None (not an attributable save, or unsure) keeps the global bump.
        seeds = write_seeds(request.method, request.path[len(CURRICULUM_API_PREFIX):])
        scopes_before = affected_programmes(seeds) if seeds else None
        # The shared epoch is moved once here, after the view, rather than at
        # each of invalidate_curriculum_cache()'s 94 call sites: a tree save
        # invalidates per written row, and the counter now lives in the database
        # so that other workers can see it at all. One save, one UPDATE -- and
        # the new generation appears only once the rows it describes have
        # committed, so no worker can cache pre-write data under a post-write
        # epoch.
        begin_curriculum_write_scope()
        scopes = None
        try:
            response = self.get_response(request)
            if scopes_before is not None and curriculum_write_pending():
                scopes_after = affected_programmes(seeds)
                if scopes_after is not None:
                    scopes = scopes_before | scopes_after
        finally:
            end_curriculum_write_scope(scopes)
        if response.status_code >= 400:
            return response
        after = shared_curriculum_epoch()
        if after > before:
            # Stored the way the client names it -- the browser talks to
            # `/curriculum_api` + path, and the same string is what a same-browser
            # write already broadcasts, so both routes invalidate identically.
            record_curriculum_change(request.path[len(CURRICULUM_API_PREFIX):], before, after)
        return response


class SchemaNotProvisionedMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if not isinstance(exception, SchemaNotProvisioned):
            return None
        logger.error(
            'Curriculum schema not provisioned while serving %s: %s',
            request.path, exception,
        )
        return JsonResponse(
            {
                'error': 'Curriculum schema is not provisioned.',
                'detail': str(exception),
                'missing_tables': exception.missing,
            },
            status=503,
        )
