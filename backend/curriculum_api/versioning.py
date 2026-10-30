"""Curriculum version history: capture.

Every save that changes a versioned record writes a revision holding the row as
it was saved plus a field-level diff against the previous one. Where the record
declares a version of its own -- a component's ``settings.version``, a module
reaching ``published`` -- that revision is also pinned as a named version.

Three rules shape everything below.

**A revision must never fail a save.** History is worth less than the content it
describes, so every entry point swallows its own failures and every read of the
history tables is guarded by a probe. Dropping the tables turns history off; it
does not turn saving off. See ``history_available``.

**Only real changes are recorded.** A save that rewrites a row to the values it
already held produces no revision. Without this the programme-tree save -- which
bulk-writes every component in a module on each run -- would bury the handful of
genuine edits under thousands of identical rows, and the diff between two
revisions would usually be empty.

**The actor comes from the request, not the row.** No curriculum table has an
author column. ``ActorMiddleware`` parks the signed-in account on a thread-local
for the life of the request so the write helpers, which never see the request,
can still name who saved.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlsplit

from django.db import DEFAULT_DB_ALIAS, DatabaseError, connection, transaction

from . import schema_gate

logger = logging.getLogger(__name__)

REVISIONS_TABLE = 'record_revisions'
VERSIONS_TABLE = 'record_versions'

# The authoring tables that carry material. Keyed by table name, because that is
# what the write helpers know about themselves.
#
# Every table whose rows a person authors belongs here. The ones a person never
# authors deliberately do not: the GOV.UK holiday mirror (england_holidays) is
# replicated, not edited, and the live-session attendance/artifact/recording
# tables are written by Microsoft Graph polling rather than by an author, so
# recording them would fill the history with machine traffic. `live_sessions`
# itself IS authored -- a person schedules it -- so it is here.
VERSIONED_TABLES = {
    'programmes': {'entity_type': 'programme', 'key': 'programme_id', 'title': 'name'},
    'cohorts': {'entity_type': 'cohort', 'key': 'cohort_id', 'title': 'cohort_name'},
    'groups': {'entity_type': 'group', 'key': 'group_id', 'title': 'group_name'},
    'modules': {'entity_type': 'module', 'key': 'module_catalogue_id', 'title': 'title'},
    'module_details': {'entity_type': 'module_details', 'key': 'module_catalogue_id', 'title': ''},
    'module_completion_criteria': {'entity_type': 'module_completion', 'key': 'module_catalogue_id', 'title': ''},
    'weeks': {'entity_type': 'week', 'key': 'id', 'title': 'title'},
    'components': {'entity_type': 'component', 'key': 'id', 'title': 'title'},
    'ksb_mappings': {'entity_type': 'ksb_mapping', 'key': 'id', 'title': 'ksb_code'},
    'live_sessions': {'entity_type': 'live_session', 'key': 'id', 'title': 'module_title'},
    'holidays': {'entity_type': 'holiday', 'key': 'id', 'title': 'label'},
    'week_templates': {'entity_type': 'week_template', 'key': 'id', 'title': 'title'},
    'week_template_components': {'entity_type': 'week_template_component', 'key': 'id', 'title': 'title'},
}

ENTITY_TYPES = {config['entity_type'] for config in VERSIONED_TABLES.values()}

# The columns a snapshot holds, per entity. Declaring them rather than taking
# whatever the caller happened to pass is what makes two write paths comparable:
# `authoring_upsert` hands over a full database row, while the programme-tree
# save hands over the payload it built, and those are not the same shape. A
# column absent from the source is stored as None, so alternating between the
# two paths cannot read as "field removed, field added" on every save.
#
# It also keeps the snapshot to the content that matters. `created_at` and
# `updated_at` move on every save and would defeat the only-real-changes rule;
# `library_state`, `is_programme_deleted` and the `origin_*` columns are
# bookkeeping, not material.
#
# Columns are named, never taken wholesale, which is also the allowlist that
# keeps credentials out of history: nothing here reaches a password, a token, a
# key or a session cookie, because no curriculum authoring table holds one and
# an unnamed column cannot arrive by accident. `live_session` is the one entity
# whose row touches an integration, so its Graph identifiers are named
# deliberately (they are the meeting's identity, and requirement 1 of the Teams
# invariants is that identity is traceable) while `join_url`, `web_link` and
# `meeting_options_url` are left out: those are the capability to enter a
# meeting, not a fact about it.
SNAPSHOT_COLUMNS = {
    'programme': (
        'programme_id', 'name', 'standard', 'level', 'owner', 'color', 'description',
        'structure_type', 'ksb_profile_source_id', 'required_otjh', 'status', 'is_active',
        'is_archived', 'deleted_at', 'deleted_by',
    ),
    'cohort': (
        'cohort_id', 'cohort_name', 'programme_id', 'programme_name', 'start_date', 'end_date',
        'duration_months', 'epa_months', 'apprenticeship_end_date', 'apprenticeship_end_override',
        'color', 'status', 'group_ids', 'module_names', 'holiday_ids', 'excluded_holiday_ids',
        'notes', 'deleted_at', 'deleted_by',
    ),
    'group': (
        'group_id', 'group_name', 'cohort_id', 'cohort_name', 'programme_id', 'programme_name',
        'module_ids', 'module_names', 'coach_name', 'color', 'session_week_day',
        'session_start_time', 'session_end_time', 'notes', 'deleted_at', 'deleted_by',
    ),
    # Carries the delivery schedule and the tutor, not only the title: "who
    # changed this module's tutor" and "who moved this module's start date" are
    # among the questions most often asked of the trail, and neither could be
    # answered while the snapshot stopped at the description.
    'module': (
        'module_catalogue_id', 'programme_id', 'programme_name', 'cohort_id', 'cohort_name',
        'group_id', 'group_name', 'title', 'description', 'total_otjh', 'quality_score',
        'status', 'tutor_name', 'tutor_email', 'coach_name', 'sessions_number', 'weeks_number',
        'start_date', 'end_date', 'session_week_day', 'session_start_time', 'session_end_time',
        'weekly_schedule', 'session_holidays', 'session_overrides', 'ksb_profile_source_id',
        'color', 'cover_image_url', 'deleted_at', 'deleted_by',
    ),
    'week': (
        'id', 'module_catalogue_id', 'week_number', 'title', 'summary', 'learning_outcomes',
        'display_order', 'deleted_at', 'deleted_by',
    ),
    'component': (
        'id', 'module_catalogue_id', 'week_id', 'type', 'title', 'description',
        'expected_otjh', 'points', 'ksb_mappings', 'reflection_required',
        'Reflection_Question', 'workplace_evidence_required', 'tutor_validation_required',
        'coach_validation_required', 'display_order', 'settings_json', 'live_sessions_link',
        'deleted_at', 'deleted_by',
    ),
    'module_details': (
        'module_catalogue_id', 'background', 'epa_requirements',
        'professional_qualification_outcomes', 'intent', 'learner_benefit', 'employer_benefit',
        'sequence_purpose', 'deleted_at', 'deleted_by',
    ),
    'module_completion': (
        'module_catalogue_id', 'quizzes_completed_required', 'checkpoints_completed_required',
        'average_score_required_enabled', 'average_score_required',
        'total_score_required_enabled', 'total_score_required', 'additional_notes',
        'deleted_at', 'deleted_by',
    ),
    'ksb_mapping': (
        'id', 'module_catalogue_id', 'week_id', 'component_id', 'ksb_id', 'ksb_code',
        'ksb_description', 'classification', 'weight', 'weight_class', 'source_type', 'source_id',
        'deleted_at', 'deleted_by',
    ),
    # No join_url / web_link / meeting_options_url: see the note above.
    'live_session': (
        'id', 'module_catalogue_id', 'module_title', 'provider', 'graph_event_id',
        'online_meeting_id', 'organizer_email', 'attendees', 'presenters', 'co_organizers',
        'start_datetime', 'timezone', 'duration_minutes', 'repeat_pattern', 'repeat_occurrences',
        'lobby_bypass', 'recording', 'spoken_language', 'meeting_type', 'request_responses',
        'allow_time_proposals', 'hide_attendees', 'status',
    ),
    'holiday': (
        'id', 'label', 'start_date', 'end_date', 'type', 'color', 'notes', 'is_archived',
    ),
    'week_template': (
        'id', 'title', 'summary', 'learning_outcomes', 'course_type', 'programme_id',
        'programme_name', 'module_catalogue_id', 'group_id', 'group_name', 'status',
        'ksb_mappings', 'total_otjh', 'points', 'component_count', 'author',
        'deleted_at', 'deleted_by',
    ),
    'week_template_component': (
        'id', 'week_template_id', 'type', 'title', 'description', 'expected_otjh', 'points',
        'reflection_required', 'workplace_evidence_required', 'tutor_validation_required',
        'coach_validation_required', 'ksb_mappings', 'settings_json', 'display_order',
    ),
}

# The column whose value IS the entity's position among its siblings. A change
# to only this is a reorder, not an edit -- see ``resolve_action``.
ORDER_COLUMNS = {'display_order', 'week_number'}

# The column naming an entity's parent. A change to this is a move.
PARENT_COLUMNS = {
    'component': ('week_id', 'module_catalogue_id'),
    'week': ('module_catalogue_id',),
    'module': ('group_id', 'cohort_id', 'programme_id'),
    'group': ('cohort_id', 'programme_id'),
    'cohort': ('programme_id',),
    'ksb_mapping': ('component_id', 'week_id', 'module_catalogue_id'),
    'week_template_component': ('week_template_id',),
    'live_session': ('module_catalogue_id',),
}

# Parsed rather than stored as text, so the diff can reach inside them. A
# component's material lives in settings_json, and "settings_json changed" would
# be the least useful thing this feature could say. They also arrive as a JSON
# string from one write path and as a parsed value from the other.
JSON_SNAPSHOT_COLUMNS = {
    'settings_json', 'ksb_mappings', 'learning_outcomes',
    'weekly_schedule', 'session_holidays', 'session_overrides',
    'attendees', 'presenters', 'co_organizers',
    'group_ids', 'module_ids', 'module_names', 'holiday_ids', 'excluded_holiday_ids',
}

# Settings keys that behave the same way inside settings_json.
IGNORED_SETTING_KEYS = {'updatedAt', 'lastEdited'}

# A diff is read by a person, so long values are cut. The full value is always
# in the snapshot; the diff only has to say *that* it changed and roughly how.
DIFF_VALUE_LIMIT = 200

# ------------------------------------------------------ what the log keeps
#
# The log answers "who changed what". It is not a second copy of the records,
# so each rule below removes something that answered nothing.

#: Identifiers and links Teams hands back after a push. The system fills them
#: in and nobody edits them, and a join link is a way into a meeting. The log
#: keeps one fact in their place: whether the component is linked to Teams.
TEAMS_SYSTEM_SETTING_KEYS = frozenset({
    'teamsEventId', 'teamsOnlineMeetingId', 'teamsLiveSessionId', 'teamsOccurrenceId',
    'teamsMeetingUrl', 'teamsMeetingOptionsUrl', 'teamsWebLink', 'teamsProvider',
    'teamsForBusiness', 'teamsCalendarSeries', 'teamsCalendarsToUpdate',
    'liveSessionId', 'liveSessionIds', 'liveSessionUrl',
})
TEAMS_LINK_KEYS = ('teamsEventId', 'teamsLiveSessionId', 'liveSessionId', 'teamsOnlineMeetingId')
TEAMS_LINK_MARKER = 'teamsMeeting'
TEAMS_LINKED = 'Linked'

#: Microsoft's own identifiers on a stored calendar, left out on the same rule.
SYSTEM_COLUMNS = {'live_session': frozenset({'graph_event_id', 'online_meeting_id'})}

#: Invite lists: a count and what moved, not every address on the list.
PEOPLE_LIST_FIELDS = frozenset({'settings.teamsAttendees', 'attendees'})

#: Free text. The log records that it changed, never what it said: the record
#: itself holds the words, and a copy of every draft is what made rows heavy.
TEXT_COLUMNS = frozenset({
    'description', 'summary', 'notes', 'Reflection_Question', 'background',
    'epa_requirements', 'professional_qualification_outcomes', 'intent',
    'learner_benefit', 'employer_benefit', 'sequence_purpose', 'additional_notes',
})
TEXT_SETTING_PATTERN = re.compile(r'(html|content|text|instructions?|description|body|prompt|summary|notes?|question)$', re.I)
TEXT_CHANGED = 'Text changed'

#: One person's saves to one record inside this window are one line.
MERGE_WINDOW = timedelta(minutes=10)
MERGEABLE_ACTIONS = frozenset({'updated'})

#: What an older revision keeps once a newer one exists: where it sat, so the
#: trail can still scope and label it. Created, recorded and deleted revisions
#: keep their whole copy (the trail shows it), and so does any revision a named
#: version points at, because comparing and restoring versions reads it. A
#: recorded revision is the only account of what a pre-history record held when
#: history first saw it; slimming it would leave the record with no origin at all.
SLIM_SNAPSHOT_KEYS = ('_context', 'programme_id', 'cohort_id', 'group_id', 'module_catalogue_id', 'week_id')
FULL_SNAPSHOT_ACTIONS = ('created', 'recorded', 'deleted')

_local = threading.local()


# --------------------------------------------------------------- the actor

class ActorMiddleware:
    """Park the signed-in account where the write helpers can reach it.

    Must run after ``login.middleware.LoginSessionMiddleware``, which is what
    sets ``request.login_account``. The clear in ``finally`` is not optional:
    threads are reused between requests, and a leaked actor would attribute one
    person's save to whoever happened to be served before them.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Cleared on the way IN, not on the way out. Anything still buffered from
        # an earlier request on this thread belongs to a transaction that rolled
        # back -- a committed one flushed on commit -- so it is dropped rather
        # than written: a mutation that failed must not leave an audit record
        # saying it succeeded, and one request's buffer must never reach another.
        #
        # Doing it on the way out instead would throw away the buffer of a
        # request whose transaction commits after the middleware returns, which
        # is what happens under ATOMIC_REQUESTS and under the test runner.
        discard_pending()
        # The context is cleared with the buffer and for the same reason: a block
        # whose transaction rolled back may never have reached its `__exit__`,
        # and one request's "this is a recalculation" must not survive into the
        # next request served on this thread.
        discard_context()
        account = getattr(request, 'login_account', None)
        set_actor(account)
        set_source(request_source(request))
        try:
            with audit_context(metadata=request_metadata(request)):
                return self.get_response(request)
        finally:
            set_actor(None)
            discard_context()


def request_source(request):
    """What kind of write this is, from the request rather than from the body.

    The client may *say* a save is an auto-save -- that is metadata, and it is
    read here -- but it can only ever choose between labels for a write the
    backend has already authenticated and is about to perform itself. It cannot
    name the actor, the action or the values.
    """
    declared = clean(request.headers.get('X-Curriculum-Save-Source')).lower()[:32]
    if declared in ALLOWED_SOURCES:
        return declared
    return ''


def request_metadata(request):
    """Request location, never a request body or a URL's query credentials.

    The browser path is descriptive client context, not evidence of permission.
    The actor and the persisted changes still come exclusively from the server.
    """
    from system_audit import pages

    metadata = {'request_path': request.path, 'request_method': request.method}
    path = clean(request.headers.get('X-Audit-Page'))
    origin = 'browser'
    if not path:
        referer = clean(request.headers.get('Referer'))
        try:
            parsed = urlsplit(referer)
            if parsed.netloc == request.get_host():
                path = parsed.path
                origin = 'referer'
        except ValueError:
            pass
    if path.startswith('/') and not path.startswith('//') and '\\' not in path:
        path = pages.clean_path(path)
        if not pages.excluded(path) or path.startswith(('/learner/', '/old-otjh')):
            page = pages.resolve(path)
            metadata.update(page_path=path, page_label=page['pageLabel'],
                            page_workspace=page['workspace'], page_source=origin)
    return safe_metadata(metadata)


# Every source label the system may record. Stable machine-readable
# identifiers, never UI text -- the reader turns them into words.
SOURCES = {
    'manual',        # a person pressed Save
    'auto-save',     # the builder saved on its own behalf; still the person's edit
    'module-builder',
    'tree-save',     # the programme tree writes a whole branch at once
    'import',        # a file or an external catalogue brought this in
    'upload',        # a file was attached to a component
    'duplicate',
    'wizard',
    'recalculation',  # the system worked this out from something else that moved
    'scheduled-job',
    'system',
    'api',
}

# What a browser is allowed to claim about its own save. A narrower set than
# SOURCES on purpose: `recalculation` and `scheduled-job` are assertions only the
# backend can honestly make, so a client naming one is ignored rather than
# believed. Anything unrecognised is dropped, so this column cannot become free
# text from the browser.
ALLOWED_SOURCES = {'auto-save', 'manual', 'module-builder', 'import', 'duplicate', 'wizard'}

# Actor kinds.
#
# `user` is a person who was signed in. `system` is the LMS acting on its own
# account -- a recalculation, a repair, a cascade -- and is never a claim about
# intent, only the honest reading of a write no person directly made. `job` is a
# scheduled or management-command run; `integration` is an external system
# (Microsoft Graph) writing back through us.
ACTOR_USER = 'user'
ACTOR_SYSTEM = 'system'
ACTOR_INTEGRATION = 'integration'
ACTOR_JOB = 'job'
ACTOR_TYPES = {ACTOR_USER, ACTOR_SYSTEM, ACTOR_INTEGRATION, ACTOR_JOB}

# The name a non-person actor is shown under. It has no email, because inventing
# one would put a real mailbox against a change nobody made.
SYSTEM_ACTOR_NAME = 'System'

NO_ACTOR = {'email': '', 'name': ''}


def set_actor(account):
    if account is None:
        _local.actor = None
        return
    _local.actor = account_actor(account)


def account_actor(account):
    """A signed-in account as the two fields history stores."""
    if account is None:
        return dict(NO_ACTOR)
    if isinstance(account, dict):
        email = clean(account.get('email'))
        return {'email': email, 'name': clean(account.get('name')) or email}
    email = clean(getattr(account, 'email', ''))
    return {'email': email, 'name': clean(getattr(account, 'display_name', '')) or email}


def current_actor():
    return getattr(_local, 'actor', None) or dict(NO_ACTOR)


def actor_type():
    """The declared actor kind, or the honest reading of the request.

    Never guessed from the row: a curriculum table has no author column, so with
    nothing declared the only thing that can distinguish a person from a
    scheduled job is whether the request that reached the write helper carried a
    session.
    """
    declared = clean(current_context().get('actor_type'))
    if declared in ACTOR_TYPES:
        return declared
    return ACTOR_USER if current_actor()['email'] else ACTOR_SYSTEM


def attribute(kind, signed_in, declared_trigger=None):
    """Who to record as the actor, and who to record as having triggered them.

    Three shapes, and the difference between them is the whole point of the
    distinction:

    * a person edits something -- the actor is the person, nothing triggered it;
    * the system works something out inside a person's request -- the actor is
      ``System`` and the person is the trigger, so the trail can say a date moved
      *because of* what they did without claiming they typed it;
    * a scheduled run -- the actor is ``System`` and nothing triggered it,
      because there is no person to name and guessing one would be a lie.

    A signed-in account is used as the trigger when none was declared, because
    it is a fact about the request rather than an inference.
    """
    signed_in = signed_in or dict(NO_ACTOR)
    if kind == ACTOR_USER:
        return dict(signed_in), dict(NO_ACTOR)
    trigger = account_actor(declared_trigger) if declared_trigger is not None else dict(signed_in)
    return {'email': '', 'name': SYSTEM_ACTOR_NAME}, trigger


# ------------------------------------------------------------ audit context

# The context is one dictionary on the thread, not a parameter threaded through
# every write helper. Nothing below it needs to know it exists: `record_rows`
# reads it once, at buffer time.
CONTEXT_FIELDS = ('actor_type', 'source', 'action', 'reason')


def current_context():
    return getattr(_local, 'audit_context', None) or {}


def discard_context():
    _local.audit_context = {}


class audit_context(object):
    """Say what kind of write the block inside is, for history to record.

        with audit_context(actor_type='system', triggered_by=request.login_account,
                           source='recalculation',
                           metadata={'reason': 'module_schedule_recalculation'}):
            recalculate_module_dates(...)

    Every field is optional and only the ones named are changed, so nesting is
    safe: an import running inside a request relabels the source without losing
    the person the request belongs to, and leaving the block restores exactly
    what was there before -- including "nothing".

    ``metadata`` merges rather than replaces, so an outer block can state the
    batch and an inner one the row without either erasing the other.

    Never leaks between requests: ``ActorMiddleware`` clears the whole context on
    the way in, and a management command runs on its own thread.
    """

    def __init__(self, *, actor_type='', triggered_by=None, source='', metadata=None, action='', reason=''):
        self.changes = {}
        kind = clean(actor_type)
        if kind:
            # An unrecognised kind is dropped rather than stored: a column of
            # invented actor kinds is worse than one honest default.
            if kind not in ACTOR_TYPES:
                logger.warning('Ignoring unknown audit actor_type %r.', kind)
            else:
                self.changes['actor_type'] = kind
        label = clean(source)
        if label:
            if label not in SOURCES:
                logger.warning('Ignoring unknown audit source %r.', label)
            else:
                self.changes['source'] = label
        if action:
            self.changes['action'] = clean(action)[:16]
        if reason:
            self.changes['reason'] = clean(reason)
        if triggered_by is not None:
            self.changes['triggered_by'] = account_actor(triggered_by)
        self.metadata = safe_metadata(metadata)
        self.previous = None

    def __enter__(self):
        self.previous = current_context()
        merged = dict(self.previous)
        merged.update(self.changes)
        if self.metadata:
            merged['metadata'] = {**(self.previous.get('metadata') or {}), **self.metadata}
        _local.audit_context = merged
        return self

    def __exit__(self, *exc):
        _local.audit_context = self.previous or {}
        return False


def set_source(value):
    """Set the source outside a block. Used by the middleware for the request."""
    label = clean(value)[:32]
    context = dict(current_context())
    if label:
        context['source'] = label
    else:
        context.pop('source', None)
    _local.audit_context = context


def current_source():
    return clean(current_context().get('source'))


class source(audit_context):
    """``audit_context(source=...)`` under the name the write helpers already use."""

    def __init__(self, label):
        super().__init__(source=label)


# ---------------------------------------------------------------- metadata

# Metadata describes the write; it is never a second copy of the record, and it
# is never a channel for anything secret. Only these keys are stored, and the
# values are bounded -- see the note on SNAPSHOT_COLUMNS for the same rule
# applied to the snapshot itself.
METADATA_KEYS = {
    'import_type', 'import_batch_id', 'filename', 'row_count', 'reason_code',
    'file_name', 'file_type', 'file_size', 'previous_file_name',
    'recalculated_from', 'command', 'job', 'occurrences', 'note',
    'page_path', 'page_label', 'page_workspace', 'page_source',
    'request_path', 'request_method',
    'merged_saves', 'last_saved_at',
}

METADATA_VALUE_LIMIT = 200
METADATA_JSON_LIMIT = 2000


def safe_metadata(metadata):
    """The declared metadata, reduced to what is safe and small enough to keep.

    An allowlist rather than a filter of known-bad names: a deny-list has to
    guess every future spelling of "token", and the first one it has not heard
    of ends up in an append-only log nobody can edit. Anything not named here is
    dropped silently -- the caller is passing descriptive context, not content,
    and a dropped key costs a note in the trail rather than a leak.
    """
    if not isinstance(metadata, dict):
        return {}
    safe = {}
    for key, value in metadata.items():
        name = clean(key)
        if name not in METADATA_KEYS:
            continue
        if value is None or isinstance(value, bool) or isinstance(value, int):
            safe[name] = value
            continue
        text = clean(value if isinstance(value, str) else json.dumps(jsonable(value), ensure_ascii=False))
        # A URL can carry a SAS token, a signature or a session in its query
        # string, so only the part before the '?' is ever kept.
        if '://' in text or text.startswith('/'):
            text = text.split('?', 1)[0].split('#', 1)[0]
        safe[name] = text[:METADATA_VALUE_LIMIT]
    if len(json.dumps(safe, ensure_ascii=False)) > METADATA_JSON_LIMIT:
        # Bounded rather than truncated mid-value: half a JSON document in an
        # append-only column is worse than an honest note that it was too big.
        return {'note': 'metadata omitted: too large'}
    return safe


# ------------------------------------------------------------------ helpers

def clean(value):
    return str(value or '').strip()


def qualified(table):
    if connection.vendor != 'postgresql':
        return f'"{table}"'
    return f'"curriculum"."{table}"'


_AVAILABLE = {}


def history_available():
    """True when both history tables exist. Cached; a miss is not cached.

    Probed with ``to_regclass``, which answers NULL for a missing relation
    instead of raising. Selecting from a table that is not there would raise,
    and in Postgres a raised statement aborts the whole surrounding transaction
    -- so the cheapest possible check would have been able to fail the very save
    it was asked to record.

    Not caching the negative matters: these tables are created by hand against
    Neon, and a process started before that SQL ran must begin recording once it
    has, without a restart.
    """
    if _AVAILABLE.get('ready'):
        return True
    try:
        with connection.cursor() as cursor:
            if connection.vendor == 'postgresql':
                cursor.execute(
                    'select to_regclass(%s) is not null and to_regclass(%s) is not null',
                    [f'curriculum.{REVISIONS_TABLE}', f'curriculum.{VERSIONS_TABLE}'],
                )
            else:
                cursor.execute(
                    "select count(*) = 2 from sqlite_master "
                    "where type = 'table' and name in (%s, %s)",
                    [REVISIONS_TABLE, VERSIONS_TABLE],
                )
            row = cursor.fetchone()
    except Exception:
        return False
    if not (row and row[0]):
        return False
    _AVAILABLE['ready'] = True
    return True


def reset_availability():
    _AVAILABLE.pop('ready', None)
    _AVAILABLE.pop('metadata', None)


def as_list(value):
    """A json column as a list, whether the driver parsed it or handed back text."""
    if isinstance(value, list):
        return value
    text = clean(value)
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def as_dict(value):
    """A jsonb column as a dict, whatever the driver handed back."""
    if isinstance(value, dict):
        return value
    text = clean(value)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def jsonable(value):
    """A column value in a form ``json.dumps`` accepts, without losing meaning.

    Decimal becomes float on purpose. The driver returns ``Decimal('2.00')`` for
    an OTJH column while the tree save passes ``2.0``; as text those differ, and
    every alternate save would report a change to a field nobody touched.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    # UUID and anything else the driver invents: text is lossless enough for a
    # snapshot and always comparable.
    return str(value)


def parse_json_column(value):
    """A JSON column as its parsed value, whether it arrived parsed or as text."""
    if isinstance(value, (dict, list)):
        return value
    text = clean(value)
    if not text:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def strip_credentials(value):
    """The same value with any URL query string removed, however deep it sits.

    A stored upload URL can carry a SAS token, a signature or a one-time key in
    its query string. The allowlist above keeps such things out of *metadata*,
    but a component's own ``settings_json`` holds the URL as ordinary content,
    so without this the snapshot and the diff would quietly archive a working
    credential in an append-only table that nobody can edit afterwards.

    Only the query and fragment go. The path stays, because which file this is
    remains the useful part, and two different signatures for the same file were
    never a change worth reporting anyway.
    """
    if isinstance(value, str):
        if ('://' in value or value.startswith('/')) and ('?' in value or '#' in value):
            return value.split('?', 1)[0].split('#', 1)[0]
        return value
    if isinstance(value, dict):
        return {key: strip_credentials(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [strip_credentials(item) for item in value]
    return value


#: Columns whose value must never reach history, per entity type, registered by
#: ``system_audit.writes``. Curriculum's own tables register none: no authoring
#: table holds a personal detail, and the ``SNAPSHOT_COLUMNS`` allowlist above
#: already keeps out anything not named.
#:
#: A redacted column is not simply dropped. Dropping it would make an edit to it
#: invisible, and "this field changed, by this person, at this time" is exactly
#: what an audit of a sensitive field is for. So the value is replaced by a
#: digest of itself: the same value digests the same way and records no change,
#: a different one records a change, and neither digest can be read back into
#: the value it stands for.
REDACTED_COLUMNS = {}

REDACTED_PREFIX = 'redacted:'


def redacted(value):
    """A stand-in that changes when the value changes and reveals nothing."""
    if value is None or value == '':
        return None
    text = value if isinstance(value, str) else json.dumps(jsonable(value), sort_keys=True, ensure_ascii=False)
    return REDACTED_PREFIX + hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


def audit_display_value(entity_type, field, value):
    """Apply today's privacy policy to old plaintext history on read as well."""
    if field.split('.', 1)[0] in REDACTED_COLUMNS.get(entity_type, ()):
        return None if value is None or value == '' else REDACTED_PREFIX + 'hidden'
    return strip_credentials(value)


def build_snapshot(entity_type, row):
    """The saved record as history will hold it, in a shape both write paths share.

    Always carries exactly the columns ``SNAPSHOT_COLUMNS`` declares for the
    entity — see the note there for why the source's own key set is not used.
    Columns named in ``REDACTED_COLUMNS`` carry a digest instead of the value.
    """
    row = row or {}
    hidden = REDACTED_COLUMNS.get(entity_type) or frozenset()
    skipped = SYSTEM_COLUMNS.get(entity_type) or frozenset()
    snapshot = {}
    for column in SNAPSHOT_COLUMNS.get(entity_type, ()):
        if column in skipped:
            continue
        value = row.get(column)
        if column in hidden:
            snapshot[column] = redacted(value)
            continue
        if column in JSON_SNAPSHOT_COLUMNS:
            parsed = parse_json_column(value)
            if column == 'settings_json':
                parsed = logged_settings(parsed)
            snapshot[column] = strip_credentials(jsonable(parsed)) if parsed is not None else None
            continue
        snapshot[column] = strip_credentials(jsonable(value))
    return snapshot


def logged_settings(settings):
    """Settings as the log keeps them: the Teams ids and links become one flag."""
    if not isinstance(settings, dict):
        return settings
    kept = {key: value for key, value in settings.items() if key not in TEAMS_SYSTEM_SETTING_KEYS}
    if any(settings.get(key) for key in TEAMS_LINK_KEYS):
        kept[TEAMS_LINK_MARKER] = TEAMS_LINKED
    return kept


def logged_view(entity_type, snapshot):
    """An older stored snapshot put in today's shape before it is compared.

    Rows written before these rules still carry the Teams ids and Microsoft's
    calendar ids. Comparing them as they are would report every one of those
    keys as removed on the next save.
    """
    if not snapshot:
        return snapshot
    skipped = SYSTEM_COLUMNS.get(entity_type) or frozenset()
    view = {key: value for key, value in snapshot.items() if key not in skipped}
    if isinstance(view.get('settings_json'), dict):
        view['settings_json'] = logged_settings(view['settings_json'])
    return view


def short(value):
    """A diff-sized rendering of a value, and whether it had to be cut."""
    if value is None:
        return '', False
    if isinstance(value, (dict, list)):
        text = json.dumps(value, sort_keys=True, ensure_ascii=False)
    else:
        text = str(value)
    if len(text) <= DIFF_VALUE_LIMIT:
        return text, False
    return f'{text[:DIFF_VALUE_LIMIT]}…', True


def diff_snapshots(previous, current):
    """Field-level changes between two snapshots, newest value last.

    ``settings_json`` is compared one level in, so an edit reads as
    ``settings.contentHtml`` rather than as the whole settings blob changing.
    """
    changes = []
    previous = previous or {}
    current = current or {}
    for key in sorted(set(previous) | set(current)):
        # Underscore keys are context, not content. See CONTEXT_KEY.
        if key.startswith('_'):
            continue
        before = previous.get(key)
        after = current.get(key)
        if before == after:
            continue
        if key == 'settings_json' and (isinstance(before, dict) or isinstance(after, dict)):
            changes.extend(diff_settings(
                before if isinstance(before, dict) else {},
                after if isinstance(after, dict) else {},
            ))
            continue
        changes.append(change_entry(key, before, after))
    return changes


def people_in(value):
    if isinstance(value, str):
        value = parse_json_column(value) if value.strip().startswith('[') else value.split(',')
    if not isinstance(value, list):
        return set()
    return {clean(item).lower() for item in value if clean(item)}


def is_text_field(field, before, after):
    name = field.split('.', 1)[1] if field.startswith('settings.') else field
    if field.startswith('settings.'):
        if TEXT_SETTING_PATTERN.search(name):
            return isinstance(before, (str, type(None))) and isinstance(after, (str, type(None)))
    elif name in TEXT_COLUMNS:
        return True
    # Anything too long to show whole is prose, not a setting.
    return any(isinstance(value, str) and len(value) > DIFF_VALUE_LIMIT for value in (before, after))


def change_entry(field, before, after):
    """One changed field as the log keeps it."""
    if field in PEOPLE_LIST_FIELDS:
        was, now = people_in(before), people_in(after)
        added, removed = len(now - was), len(was - now)
        return {
            'field': field, 'from': f'{len(was)} invited',
            'to': f'{len(now)} invited (+{added}, -{removed})', 'truncated': False,
        }
    if is_text_field(field, before, after):
        return {'field': field, 'from': '', 'to': TEXT_CHANGED, 'truncated': False, 'text': True}
    from_text, from_cut = short(before)
    to_text, to_cut = short(after)
    return {'field': field, 'from': from_text, 'to': to_text, 'truncated': from_cut or to_cut}


def merge_changes(earlier, later):
    """Two diffs of one record as one: each field from its first value to its last.

    A field put back where it started drops out; text keeps its "changed" line,
    since the log never held the words to compare.
    """
    merged = {}
    for change in list(earlier or ()) + list(later or ()):
        field = clean(change.get('field'))
        if not field:
            continue
        if field in merged:
            first = merged[field]
            combined = dict(change)
            combined['from'] = first.get('from')
            combined['truncated'] = bool(first.get('truncated') or change.get('truncated'))
            combined['text'] = bool(first.get('text') or change.get('text'))
            if not combined['text']:
                combined.pop('text')
            merged[field] = combined
        else:
            merged[field] = dict(change)
    return [change for change in merged.values() if change.get('text') or change.get('from') != change.get('to')]


def diff_settings(previous, current):
    changes = []
    for key in sorted(set(previous) | set(current)):
        if key in IGNORED_SETTING_KEYS:
            continue
        before = previous.get(key)
        after = current.get(key)
        if before == after:
            continue
        changes.append(change_entry(f'settings.{key}', before, after))
    return changes


# Where each entity's own title lives in its snapshot, and which column names
# its parent for the indexed ``parent_id`` column.
ENTITY_FACT_FIELDS = {
    'programme': {'title': 'name', 'parent': '', 'status': 'status'},
    'cohort': {'title': 'cohort_name', 'parent': 'programme_id', 'status': 'status'},
    'group': {'title': 'group_name', 'parent': 'cohort_id', 'status': ''},
    'module': {'title': 'title', 'parent': 'programme_id', 'status': 'status'},
    'module_details': {'title': '', 'parent': 'module_catalogue_id', 'status': ''},
    'module_completion': {'title': '', 'parent': 'module_catalogue_id', 'status': ''},
    'week': {'title': 'title', 'parent': 'module_catalogue_id', 'status': ''},
    'component': {'title': 'title', 'parent': 'week_id', 'status': ''},
    'ksb_mapping': {'title': 'ksb_code', 'parent': 'component_id', 'status': 'classification'},
    'live_session': {'title': 'module_title', 'parent': 'module_catalogue_id', 'status': 'status'},
    'holiday': {'title': 'label', 'parent': '', 'status': ''},
    'week_template': {'title': 'title', 'parent': 'programme_id', 'status': 'status'},
    'week_template_component': {'title': 'title', 'parent': 'week_template_id', 'status': ''},
}


def snapshot_title(snapshot, columns, join=' '):
    """The record's own name, from one snapshot column or several joined.

    Several, because outside the curriculum a record's name is rarely in one
    column: a coaching meeting is identified by whose it is *and* what kind it
    is, and an employer contact by a first name *and* a surname. A column that
    is empty is skipped rather than joined as a gap, so a contact with no
    surname reads as their first name and not as a name with a dangling
    separator.
    """
    if not columns:
        return ''
    if isinstance(columns, str):
        columns = (columns,)
    parts = [clean(snapshot.get(column)) for column in columns]
    return join.join(part for part in parts if part)


def entity_facts(entity_type, snapshot):
    """The columns the history table denormalises out of a snapshot."""
    fields = ENTITY_FACT_FIELDS.get(entity_type) or {}
    settings = as_dict(snapshot.get('settings_json'))
    version_label = clean(settings.get('version')) if entity_type == 'component' else ''
    content_status = clean(settings.get('contentStatus')) if entity_type == 'component' else ''
    if not content_status and fields.get('status'):
        content_status = clean(snapshot.get(fields['status']))
    title_column = fields.get('title')
    title = snapshot_title(snapshot, title_column, fields.get('title_join') or ' ')
    # A record whose usual name is blank still has to be findable in a list.
    # `title_fallback` names the column to use instead -- an email where a
    # username was never set -- rather than leaving the row to be listed under
    # its own primary key, which tells a reader nothing.
    if not title and fields.get('title_fallback'):
        title = snapshot_title(snapshot, fields['title_fallback'])
    # A row with no title of its own is still worth naming in a list, and the
    # only honest name it has is the record it belongs to.
    if not title and entity_type in {'module_details', 'module_completion'}:
        title = clean(snapshot.get('module_catalogue_id'))
    parent_column = fields.get('parent')
    return {
        'module_catalogue_id': clean(snapshot.get('module_catalogue_id')),
        'parent_id': clean(snapshot.get(parent_column)) if parent_column else '',
        'title': title[:500],
        'version_label': version_label,
        'content_status': content_status[:64],
    }


# --------------------------------------------------------- parent context

# Under this key the snapshot carries the entity's ancestry AS IT WAS when the
# action happened -- ids together with the names those ids had at the time, so a
# programme renamed next year does not silently rewrite last year's history.
#
# Underscore-prefixed because the diff skips those keys: ancestry is context, and
# a component must not report "changed" because the programme above it was
# renamed. A move that really does change the entity's own parent still shows,
# because that is a change to the entity's own `week_id` / `programme_id` column.
CONTEXT_KEY = '_context'

#: entity_type -> the snapshot columns that place a record, in reading order.
#: Registered by ``system_audit.writes`` for everything outside the curriculum,
#: whose ancestry is not a programme tree: a coaching meeting is placed by whose
#: it is, a learner by their programme, cohort and group.
#:
#: The curriculum's own entities are deliberately absent. Theirs is resolved
#: from the module table below, because a component's programme is not in the
#: component's own row.
ENTITY_CONTEXT_FIELDS = {}


def context_for(entity_type, snapshot, ancestry):
    """The ancestry to store beside a snapshot, from an already-resolved map."""
    registered = ENTITY_CONTEXT_FIELDS.get(entity_type)
    if registered:
        # Read off the row itself, and stored with it, for the same reason the
        # curriculum's is: this is where the record sat WHEN the change
        # happened. A learner moved to another cohort next month must not
        # silently rewrite the line against today's edit.
        return {
            column: clean(snapshot.get(column))
            for column in registered
            if clean(snapshot.get(column))
        }
    module_id = clean(snapshot.get('module_catalogue_id'))
    context = {}
    if entity_type == 'programme':
        context = {'programme_id': clean(snapshot.get('programme_id')), 'programme_name': clean(snapshot.get('name'))}
    elif entity_type in {'cohort', 'group', 'module', 'week_template'}:
        context = {
            'programme_id': clean(snapshot.get('programme_id')),
            'programme_name': clean(snapshot.get('programme_name')),
        }
        if entity_type in {'group', 'module'}:
            context['cohort_id'] = clean(snapshot.get('cohort_id'))
            context['cohort_name'] = clean(snapshot.get('cohort_name'))
        if entity_type == 'module':
            context['group_id'] = clean(snapshot.get('group_id'))
            context['group_name'] = clean(snapshot.get('group_name'))
    elif module_id:
        context = dict(ancestry.get(module_id) or {})
    if entity_type == 'component':
        context['week_id'] = clean(snapshot.get('week_id'))
        context['module_catalogue_id'] = module_id
    elif entity_type == 'week':
        context['module_catalogue_id'] = module_id
    elif entity_type == 'ksb_mapping':
        context['week_id'] = clean(snapshot.get('week_id'))
        context['component_id'] = clean(snapshot.get('component_id'))
        context['module_catalogue_id'] = module_id
    return {key: value for key, value in context.items() if value}


def resolve_ancestry(module_ids):
    """Programme / cohort / group / module names for these modules, in one read.

    One query for every module touched by the flush, not one per row: a tree
    save buffers thousands of components and they nearly all sit under the same
    handful of modules.
    """
    ids = sorted({clean(value) for value in module_ids if clean(value)})
    if not ids:
        return {}
    placeholders = ', '.join(['%s'] * len(ids))
    query = (
        'select module_catalogue_id, title, programme_id, programme_name, '
        'cohort_id, cohort_name, group_id, group_name '
        f'from {qualified("modules")} where module_catalogue_id in ({placeholders})'
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(query, ids)
            columns = [column[0] for column in cursor.description]
            rows = [dict(zip(columns, values)) for values in cursor.fetchall()]
    except Exception:
        # Context is a nicety; the snapshot and the diff are the record. A module
        # that has just been hard-deleted is exactly the case that lands here.
        logger.debug('Could not resolve curriculum ancestry.', exc_info=True)
        return {}
    return {
        clean(row.get('module_catalogue_id')): {
            key: clean(row.get(key))
            for key in (
                'programme_id', 'programme_name', 'cohort_id', 'cohort_name',
                'group_id', 'group_name',
            )
            if clean(row.get(key))
        } | {'module_name': clean(row.get('title'))}
        for row in rows
    }


# 'recorded' is the first revision of a record that already existed before
# history was switched on: the state it was found in. It is deliberately not
# 'created', because nobody here created it and the log must not invent an
# origin for a record whose real one is unknown.
#
# An action is the business event; `source` is how it arrived. `auto-save` is
# never an action -- the event is still the edit -- and there is no action per
# handler. The file and recalculation entries below are here because each is a
# thing a reader asks about by name ("who replaced that PDF", "why did the end
# date move"), which a generic `updated` cannot answer.
ACTIONS = (
    'created', 'updated', 'archived', 'restored', 'deleted', 'moved', 'reordered', 'recorded',
    'file_uploaded', 'file_replaced', 'file_removed', 'recalculated', 'imported',
)

# Actions a caller may declare for itself through ``audit_context(action=...)``.
# Archive, restore, move and reorder are deliberately absent: those are read off
# the row's own diff, and a caller that could assert them could assert one that
# did not happen.
DECLARABLE_ACTIONS = {'file_uploaded', 'file_replaced', 'file_removed', 'recalculated', 'imported'}

# Where a component records the file attached to it, and the two settings keys
# that describe that file. Nothing here is the file's URL: an upload URL can
# carry a SAS token or a signature in its query string, and an append-only log is
# the last place one should land.
UPLOAD_NAME_FIELD = 'settings.uploadedFileName'
UPLOAD_NAME_KEY = 'uploadedFileName'
UPLOAD_TYPE_KEY = 'uploadedFileContentType'
UPLOAD_SIZE_KEY = 'uploadedFileSize'


def resolve_file_action(changes):
    """Which file event this diff is, if it is one at all."""
    for change in changes or ():
        if change.get('field') != UPLOAD_NAME_FIELD:
            continue
        before, after = clean(change.get('from')), clean(change.get('to'))
        if before and not after:
            return 'file_removed'
        if after and not before:
            return 'file_uploaded'
        if after and before:
            return 'file_replaced'
    return ''


def file_metadata(action, snapshot, previous_snapshot):
    """What the trail records about a file event, read from the rows themselves.

    Only ever the name, the type and the size -- enough for a reader to know
    which file this was, and nothing that is the file or that could let someone
    fetch it.
    """
    if action not in {'file_uploaded', 'file_replaced', 'file_removed'}:
        return {}
    settings = as_dict(snapshot.get('settings_json'))
    previous = as_dict((previous_snapshot or {}).get('settings_json'))
    if action == 'file_removed':
        return safe_metadata({'previous_file_name': previous.get(UPLOAD_NAME_KEY)})
    metadata = {
        'file_name': settings.get(UPLOAD_NAME_KEY),
        'file_type': settings.get(UPLOAD_TYPE_KEY),
        'file_size': settings.get(UPLOAD_SIZE_KEY),
    }
    if action == 'file_replaced':
        metadata['previous_file_name'] = previous.get(UPLOAD_NAME_KEY)
    return safe_metadata({key: value for key, value in metadata.items() if value not in (None, '')})


def resolve_action(entity_type, snapshot, previous_row, changes):
    """The action this revision records, from the row and its own diff.

    Archive and restore win over everything, because withdrawing a record is the
    most consequential thing that can happen to it. Below those, a diff that
    touches only the parent column is a move and a diff that touches only the
    order column is a reorder -- said outright rather than left for a reader to
    infer from a `week_id` that went from one opaque id to another.
    """
    archived = bool(snapshot.get('deleted_at'))
    if previous_row is None:
        return 'archived' if archived else 'created'
    was_archived = bool((previous_row.get('snapshot') or {}).get('deleted_at'))
    if archived and not was_archived:
        return 'archived'
    if was_archived and not archived:
        return 'restored'
    # Attaching, swapping or detaching a file, read off the component's own
    # diff rather than declared by a caller.
    #
    # Derived on purpose. The obvious place to declare it is the upload endpoint,
    # but that endpoint does not write the component (it hands the file's details
    # back and the browser saves them with the next component save), so a
    # declaration there would label a write that never happens while the real one
    # went on reading as a settings edit. The name the component ends up carrying
    # is the fact; whichever save carried it is beside the point.
    file_action = resolve_file_action(changes)
    if file_action:
        return file_action
    fields = {change.get('field') for change in changes or ()}
    if fields:
        parents = set(PARENT_COLUMNS.get(entity_type) or ())
        # A move has to actually touch a parent column. Landing in a new position
        # within the same week is a reorder, and reading it as a move because
        # `week_id` happens to be a parent column would mislabel every drag.
        if fields & parents and fields <= (parents | ORDER_COLUMNS):
            return 'moved'
        if fields <= ORDER_COLUMNS:
            return 'reordered'
    return 'updated'




# ------------------------------------------------------------------- writes

def latest_revisions(entity_type, entity_ids):
    """The newest revision per entity, in one read.

    ``distinct on`` keeps this a single index scan over
    ``record_revisions_entity_idx`` however many ids are asked for, which is
    what makes the bulk path viable — the tree save hands over every component
    in a module at once.
    """
    ids = [clean(value) for value in entity_ids if clean(value)]
    if not ids:
        return {}
    placeholders = ', '.join(['%s'] * len(ids))
    columns_sql = 'entity_id, id, revision_no, snapshot, version_label, content_status, action, actor_email, created_at, changed_fields'
    if metadata_columns_available():
        columns_sql += ', metadata'
    if connection.vendor == 'postgresql':
        query = (
            f'select distinct on (entity_id) {columns_sql} '
            f'from {qualified(REVISIONS_TABLE)} '
            f'where entity_type = %s and entity_id in ({placeholders}) '
            f'order by entity_id, revision_no desc'
        )
    else:
        # sqlite has no `distinct on`, and the correlated-subquery form cannot
        # alias a schema-qualified name. The row count here is bounded by the
        # revisions of the entities being saved, so the newest is picked below.
        query = (
            f'select {columns_sql} from {qualified(REVISIONS_TABLE)} '
            f'where entity_type = %s and entity_id in ({placeholders}) '
            f'order by entity_id, revision_no'
        )
    with connection.cursor() as cursor:
        cursor.execute(query, [entity_type, *ids])
        columns = [column[0] for column in cursor.description]
        rows = [dict(zip(columns, values)) for values in cursor.fetchall()]
    result = {}
    for row in rows:
        key = clean(row.get('entity_id'))
        existing = result.get(key)
        if existing and int(existing.get('revision_no') or 0) >= int(row.get('revision_no') or 0):
            continue
        row['snapshot'] = as_dict(row.get('snapshot'))
        result[key] = row
    return result


def record_rows(table, rows, *, reason='', deleted=False, using=None, before_rows=None):
    """Note what a write left behind, to be recorded when it commits. Never raises.

    Callers pass the rows their write returned, so this costs no extra read of
    the record itself.

    Nothing is written here. A single save touches the same row more than once --
    ``save_module_authoring_structure`` withdraws every week and component in the
    module and then writes them all straight back -- and recording each touch
    produced an archived/restored pair per child per save. On the live database
    that was 1.13 million of 1.15 million revisions: 98% of the history was the
    save cycle describing itself, and the 598 real edits were buried in it.

    So each row is buffered under its identity, last state wins, and the whole
    buffer is compared against the stored history once, when the transaction
    commits. A row that ends the save holding what it already held records
    nothing; a row that was withdrawn and put back unchanged records nothing; a
    row someone actually edited records one revision saying what they changed.

    ``deleted`` marks a row the write destroyed. The snapshot is what it held a
    moment before it went, because after the statement there is nothing left to
    read.
    """
    config = VERSIONED_TABLES.get(table)
    if not config or not rows:
        return
    try:
        entity_type = config['entity_type']
        key_column = config['key']
        before_by_id = {
            clean(row.get(key_column)): build_snapshot(entity_type, row)
            for row in (before_rows or [])
        }
        alias = using or config.get('using') or DEFAULT_DB_ALIAS
        buffer = pending_buffer(alias)
        # Taken here, not at flush time. The flush can run after the request has
        # finished -- it waits for the commit -- and by then the thread-locals
        # have been cleared for the next request. Reading them late would credit
        # every deferred save to nobody, and would lose whichever
        # ``audit_context`` block the write actually happened inside.
        context = current_context()
        kind = actor_type()
        signed_in = current_actor()
        actor, triggered_by = attribute(kind, signed_in, context.get('triggered_by'))
        source_label = current_source()
        action_override = clean(context.get('action'))
        context_reason = clean(context.get('reason'))
        metadata = dict(context.get('metadata') or {})
        for row in rows:
            if not row:
                continue
            entity_id = clean(row.get(key_column))
            if not entity_id:
                continue
            prior_buffer = buffer.get((entity_type, entity_id), {})
            buffer[(entity_type, entity_id)] = {
                'entity_type': entity_type,
                'entity_id': entity_id,
                'snapshot': build_snapshot(entity_type, row),
                # Whether the row genuinely came into existence now, or merely
                # reached history for the first time. See ``build_revision``.
                'created_at': row.get('created_at'),
                'updated_at': row.get('updated_at'),
                'deleted': bool(deleted),
                'reason': context_reason or clean(reason),
                'source': source_label,
                'actor': actor,
                'actor_type': kind,
                'triggered_by': triggered_by,
                'metadata': metadata,
                'action_override': action_override,
                # Keep the first actual database state of a multi-write save.
                'before_snapshot': prior_buffer.get('before_snapshot', before_by_id.get(entity_id)),
            }
        arm_flush(alias)
    except Exception:
        # Deliberately broad: history is worth less than the content it records.
        logger.warning('Could not buffer curriculum revisions for %s.', table, exc_info=True)


def record_deleted_rows(table, rows, *, reason='', using=None):
    """Record rows a hard delete destroyed, from the rows the delete returned."""
    record_rows(table, rows, reason=reason or 'hard-delete', deleted=True, using=using)


# ------------------------------------------------------- the pending buffer

def pending_buffers():
    """Every connection's buffer, keyed by database alias."""
    buffers = getattr(_local, 'pending', None)
    if buffers is None:
        buffers = {}
        _local.pending = buffers
    return buffers


def pending_buffer(alias=DEFAULT_DB_ALIAS):
    """The buffer for one connection.

    Kept per alias, not per thread. One request can write through more than one
    connection -- curriculum's tables on `default`, a learner's on `enrolment`
    -- and each becomes durable at its own commit. A single shared buffer would
    let whichever transaction committed first carry the other's rows out with
    it, so a revision could be recorded for a write that then rolled back. That
    is precisely the claim an audit log must never make.
    """
    return pending_buffers().setdefault(alias, {})


def discard_pending(alias=None):
    """Throw the buffer away. What a rolled-back transaction wrote never happened.

    With no alias, every connection's buffer goes -- which is what a test
    tearing down wants. With one, only that connection's.
    """
    if alias is None:
        _local.pending = {}
    else:
        pending_buffers().pop(alias, None)


def arm_flush(alias=DEFAULT_DB_ALIAS):
    """Arrange for the buffer to be recorded when the write becomes durable.

    Inside a transaction that is ``on_commit``: a rollback drops the callback and
    leaves the buffer to be discarded, so a mutation that failed cannot leave an
    audit record claiming it succeeded. Outside one the write is already
    committed, so there is nothing to wait for.

    Registered on every call rather than once behind a flag. A flag would have to
    be cleared by something, and the one thing that never runs after a rollback
    is the code that would clear it -- so a single rolled-back transaction would
    leave the flag set and silently drop every later save's history on that
    thread. ``flush_pending`` empties the buffer as it runs, so the extra
    callbacks cost one dictionary lookup each and do nothing.

    Registered against the connection the write actually went through. It used
    to ask `connection` -- always `default` -- whether a transaction was open,
    which is the right answer only while every audited write uses that one
    connection. A write on another alias inside its own `atomic()` block would
    have been reported as having no transaction at all, so its revision would
    have been written immediately and kept even if that transaction then rolled
    back.
    """
    alias = alias or DEFAULT_DB_ALIAS
    try:
        if transaction.get_connection(alias).in_atomic_block:
            transaction.on_commit(lambda: flush_pending(alias), using=alias)
            return
    except Exception:
        pass
    flush_pending(alias)


def flush_pending(alias=DEFAULT_DB_ALIAS):
    """Write one revision per entity the transaction actually changed. Never raises."""
    buffer = pending_buffers().pop(alias, None)
    if not buffer:
        return
    try:
        if not history_available():
            return
        # A nested atomic block is a SAVEPOINT, so a history write that fails
        # rolls back only itself and leaves the caller's transaction usable.
        # Without it the `except` below would swallow the error and hand back a
        # transaction Postgres has already marked as aborted, failing the save
        # at commit for a reason nothing in the traceback would explain.
        with transaction.atomic():
            _flush(list(buffer.values()))
    except Exception:
        logger.warning('Could not record curriculum revisions.', exc_info=True)


def _flush(items):
    by_type = {}
    for item in items:
        by_type.setdefault(item['entity_type'], []).append(item)

    previous_by_type = {
        entity_type: latest_revisions(entity_type, [item['entity_id'] for item in group])
        for entity_type, group in by_type.items()
    }
    ancestry = resolve_ancestry(
        item['snapshot'].get('module_catalogue_id') for item in items
    )

    pending = []
    for entity_type, group in by_type.items():
        previous_by_id = previous_by_type.get(entity_type) or {}
        for item in group:
            entry = build_revision(entity_type, item, previous_by_id, ancestry)
            if entry is not None:
                pending.append(entry)
    if not pending:
        return

    merged = merge_bursts(pending)
    pending = [item for item in pending if id(item) not in merged]
    if not pending:
        return

    inserted = insert_revisions(pending)
    trim_superseded(pending, inserted)
    for entity_type in by_type:
        rows = [item for item in pending if item['entity_type'] == entity_type]
        if rows:
            pin_versions(entity_type, rows, inserted, rows[0]['actor'])


def build_revision(entity_type, item, previous_by_id, ancestry):
    """One buffered entity as a revision row, or None when nothing changed."""
    actor = item['actor']
    kind = item['actor_type']
    entity_id = item['entity_id']
    snapshot = dict(item['snapshot'])
    previous = previous_by_id.get(entity_id)
    actual_before = item.get('before_snapshot')
    previous_snapshot = logged_view(
        entity_type, actual_before if actual_before is not None else (previous or {}).get('snapshot') or {},
    )
    has_before = actual_before is not None or previous is not None
    context = context_for(entity_type, snapshot, ancestry)
    if context:
        snapshot[CONTEXT_KEY] = context

    if item['deleted']:
        # A destroyed row has no "after". The snapshot is what it held, and the
        # action says the record is gone -- which is the whole point of taking
        # the snapshot before the statement rather than looking for the row
        # afterwards and finding nothing.
        changes = diff_snapshots(previous_snapshot, snapshot) if previous else []
        action = 'deleted'
    else:
        if has_before and strip_context(previous_snapshot) == strip_context(snapshot):
            return None  # nothing actually changed
        # A create has no "before", so it has no changed fields. Diffing the new
        # record against nothing listed every column it happens to hold as having
        # moved from empty, which reads as fourteen edits to a record that was
        # simply made. The snapshot is the whole answer to what was created.
        changes = diff_snapshots(previous_snapshot, snapshot) if has_before else []
        action = resolve_action(entity_type, snapshot, previous or ({'snapshot': previous_snapshot} if has_before else None), changes)
        # A record that existed before history did must not be reported as
        # having been created now. Its first revision says so honestly: this is
        # the state it was found in, not the moment it came into being. The row
        # itself is the only witness -- a record saved once has `updated_at`
        # equal to its `created_at`, which is the same rule the older derived
        # trail uses to tell a create from an edit.
        if action == 'created' and not looks_newly_created(item):
            action = 'recorded'
        # A declared action names the business event the write was part of, and
        # only for events the row cannot show by itself. It never overrides a
        # create, an archive or a restore: those are facts about the row, and a
        # caller that could talk over them could hide a deletion behind "file
        # replaced".
        declared = clean(item.get('action_override'))
        if declared in DECLARABLE_ACTIONS and action == 'updated':
            action = declared
        # Only ignored keys moved (an edit stamp, a Teams id): nothing to say.
        if action == 'updated' and not changes:
            return None

    reason = clean(item['reason'] or snapshot.get('deleted_by'))
    source = clean(item['source'])
    triggered_by = item.get('triggered_by') or dict(NO_ACTOR)
    # Whatever the caller declared, plus what the row itself says about a file
    # it gained or lost. The row wins on the keys it owns: it is evidence, and
    # the declaration is a claim.
    metadata = {**(item.get('metadata') or {}), **file_metadata(action, snapshot, previous_snapshot)}
    if not metadata_columns_available():
        # Before the owner runs the Phase 2 SQL there are no columns for any of
        # this, so it rides in `reason` the way it always did: the handler code
        # with tagged pairs after it. `quality.py` reads the columns first and
        # falls back to parsing this, so both shapes are legible to one reader
        # and no row has to be rewritten when the columns arrive.
        tags = [f'source={source}'] if source else []
        if kind != ACTOR_USER:
            tags.append(f'actor={kind}')
        if triggered_by['email']:
            tags.append(f'by={triggered_by["email"]}')
        if tags:
            reason = '; '.join([reason] + tags) if reason else '; '.join(tags)

    return {
        'entity_type': entity_type,
        'entity_id': entity_id,
        'revision_no': int(previous.get('revision_no') or 0) + 1 if previous else 1,
        'action': action,
        'snapshot': snapshot,
        'changed_fields': changes,
        'actor_email': actor['email'],
        'actor_name': actor['name'],
        'reason': reason[:255],
        'actor_type': kind,
        'triggered_by_email': triggered_by['email'][:255],
        'triggered_by_name': triggered_by['name'][:255],
        'source': source[:64],
        'metadata': metadata,
        'previous': previous,
        'actor': actor,
        **entity_facts(entity_type, snapshot),
    }


def _aware(stamp):
    if not isinstance(stamp, datetime):
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def burst_target(entry, now):
    """The revision this save extends, when it is the same person editing on."""
    previous = entry.get('previous')
    if not previous or entry.get('action') not in MERGEABLE_ACTIONS:
        return None
    if clean(previous.get('action')) not in MERGEABLE_ACTIONS:
        return None
    email = clean(entry.get('actor_email')).lower()
    if not email or email != clean(previous.get('actor_email')).lower():
        return None
    stamp = _aware(previous.get('created_at'))
    if stamp is None or not timedelta(0) <= now - stamp <= MERGE_WINDOW:
        return None
    # A save that names a new version is a line of its own.
    if version_label_for(entry['entity_type'], entry):
        return None
    return previous


def merge_bursts(pending):
    """Fold saves into the revision they continue. Returns the folded entries' ids.

    Never raises: a save that cannot be folded is simply inserted as its own line.
    """
    now = datetime.now(timezone.utc)
    candidates = [(entry, burst_target(entry, now)) for entry in pending]
    candidates = [(entry, previous) for entry, previous in candidates if previous is not None]
    if not candidates:
        return set()
    folded = set()
    try:
        with transaction.atomic():
            ids = [previous['id'] for _, previous in candidates]
            with connection.cursor() as cursor:
                # A revision a named version points at is frozen.
                cursor.execute(
                    f'select revision_id from {qualified(VERSIONS_TABLE)} '
                    f'where revision_id in ({", ".join(["%s"] * len(ids))})',
                    ids,
                )
                pinned = {row[0] for row in cursor.fetchall()}
                with_metadata = metadata_columns_available()
                for entry, previous in candidates:
                    if previous['id'] in pinned:
                        continue
                    changes = merge_changes(as_list(previous.get('changed_fields')), entry['changed_fields'])
                    sets = ['snapshot = %s', 'changed_fields = %s', 'title = %s', 'content_status = %s']
                    params = [
                        json.dumps(entry['snapshot'], ensure_ascii=False),
                        json.dumps(changes, ensure_ascii=False),
                        entry.get('title'), entry.get('content_status'),
                    ]
                    if with_metadata:
                        earlier = as_dict(previous.get('metadata'))
                        metadata = safe_metadata({
                            **entry.get('metadata', {}), **earlier,
                            'merged_saves': int(earlier.get('merged_saves') or 1) + 1,
                            'last_saved_at': now.isoformat(),
                        })
                        sets.append('metadata = %s')
                        params.append(json.dumps(metadata, ensure_ascii=False))
                    cursor.execute(
                        f'update {qualified(REVISIONS_TABLE)} set {", ".join(sets)} where id = %s',
                        [*params, previous['id']],
                    )
                    if cursor.rowcount:
                        folded.add(id(entry))
    except Exception:
        logger.warning('Could not fold a burst of saves into one revision.', exc_info=True)
        return set()
    return folded


def trim_superseded(pending, inserted):
    """Keep one full copy per record: the newest. Never raises.

    The copy before it is only ever read to work out the next diff, which now
    reads the new row instead. What stays on it is where the record sat.
    """
    if connection.vendor != 'postgresql' or not inserted:
        return
    ids = [
        entry['previous']['id'] for entry in pending
        if entry.get('previous') and (entry['entity_type'], entry['entity_id']) in inserted
    ]
    if not ids:
        return
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                f'update {qualified(REVISIONS_TABLE)} r set snapshot = coalesce(('
                'select jsonb_object_agg(key, value) from jsonb_each(r.snapshot) where key = any(%s)'
                "), '{}'::jsonb) "
                'where r.id = any(%s) and r.action <> all(%s) '
                f'and not exists (select 1 from {qualified(VERSIONS_TABLE)} v where v.revision_id = r.id)',
                [list(SLIM_SNAPSHOT_KEYS), ids, list(FULL_SNAPSHOT_ACTIONS)],
            )
    except Exception:
        logger.warning('Could not trim superseded revision snapshots.', exc_info=True)


def looks_newly_created(item):
    """Whether this row came into existence now, or merely reached history now.

    Both stamps have to be readable for this to answer yes. An unreadable or
    missing stamp is treated as "cannot tell", and the honest answer to "cannot
    tell whether this was just created" is no -- claiming a create that did not
    happen is the worse of the two mistakes, because it puts a false origin
    story against a record nobody can correct afterwards.
    """
    created, updated = item.get('created_at'), item.get('updated_at')
    if created is None or updated is None:
        return False
    if isinstance(created, datetime) and isinstance(updated, datetime):
        # The two are written microseconds apart by the same statement, never
        # exactly equal, so this asks whether the row's whole life fits inside
        # this save rather than whether the stamps match to the microsecond.
        return abs((updated - created).total_seconds()) < 1
    return clean(created) == clean(updated)


def strip_context(snapshot):
    """A snapshot without its ancestry, for the did-anything-change comparison.

    Renaming a programme must not make every component under it look edited.
    """
    return {key: value for key, value in (snapshot or {}).items() if not key.startswith('_')}


REVISION_COLUMNS = (
    'entity_type', 'entity_id', 'revision_no', 'action', 'module_catalogue_id', 'parent_id',
    'title', 'version_label', 'content_status', 'snapshot', 'changed_fields',
    'actor_email', 'actor_name', 'reason',
)

# Added by 2026-09-16_curriculum_record_revisions_audit_metadata.sql. Written
# only once that has run, so a deployment whose database is still on the old
# shape keeps recording rather than failing every save on a missing column.
METADATA_COLUMNS = ('actor_type', 'triggered_by_email', 'triggered_by_name', 'source', 'metadata')

JSON_REVISION_COLUMNS = {'snapshot', 'changed_fields', 'metadata'}


def metadata_columns_available():
    """Whether ``record_revisions`` has the Phase 2 metadata columns yet.

    Probed the same way the tables themselves are, and for the same reason: this
    runs inside somebody's save, so it has to answer without raising. Cached on
    success only -- the columns are added by hand against Neon, and a process
    started before that SQL ran must begin using them once it has, without a
    restart. ``reset_availability`` clears it.
    """
    if _AVAILABLE.get('metadata'):
        return True
    try:
        with connection.cursor() as cursor:
            if connection.vendor == 'postgresql':
                cursor.execute(
                    'select count(*) from information_schema.columns '
                    'where table_schema = %s and table_name = %s and column_name = any(%s)',
                    ['curriculum', REVISIONS_TABLE, list(METADATA_COLUMNS)],
                )
            else:
                cursor.execute(f'pragma table_info("{REVISIONS_TABLE}")')
                names = {row[1] for row in cursor.fetchall()}
                _AVAILABLE['metadata'] = set(METADATA_COLUMNS) <= names
                return _AVAILABLE['metadata']
            row = cursor.fetchone()
    except Exception:
        return False
    if not (row and int(row[0] or 0) == len(METADATA_COLUMNS)):
        return False
    _AVAILABLE['metadata'] = True
    return True


def insert_revisions(pending):
    """Insert the revisions and return their ids, keyed by (entity type, entity id).

    Keyed by the pair, not by the id alone: a module and its ``module_details``
    row are two entities that share ``module_catalogue_id``, so an id-keyed map
    would hand one of them the other's revision.

    ``on conflict do nothing`` covers the one race this has: two requests saving
    the same record at once compute the same ``revision_no``. The loser drops
    its revision rather than failing, which is the right trade — the winner's
    row already records that the content changed.
    """
    # The single point every application revision passes through. An action
    # outside ACTIONS has no label, no tone and no meaning on the trail -- the
    # 2,599 `create` / `update` rows written straight into this table by one-off
    # scripts render as "Create" beside a real "Created" -- so one is refused
    # here, loudly, rather than stored as history nobody can read.
    canonical = [item for item in pending if item.get('action') in ACTIONS]
    for item in pending:
        if item.get('action') not in ACTIONS:
            logger.error(
                'Refused a %s revision with non-canonical action %r.',
                item.get('entity_type'), item.get('action'),
            )
    pending = canonical
    if not pending:
        return {}
    columns = REVISION_COLUMNS + (METADATA_COLUMNS if metadata_columns_available() else ())
    placeholder = f'({", ".join(["%s"] * len(columns))})'
    values = []
    for item in pending:
        for column in columns:
            value = item[column]
            values.append(json.dumps(value, ensure_ascii=False) if column in JSON_REVISION_COLUMNS else value)
    query = (
        f'insert into {qualified(REVISIONS_TABLE)} ({", ".join(columns)}) values '
        f'{", ".join([placeholder] * len(pending))} '
        f'on conflict (entity_type, entity_id, revision_no) do nothing '
        f'returning id, entity_type, entity_id'
    )
    with connection.cursor() as cursor:
        cursor.execute(query, values)
        return {
            (clean(entity_type), clean(entity_id)): revision_id
            for revision_id, entity_type, entity_id in cursor.fetchall()
        }


def pin_versions(entity_type, pending, inserted, actor):
    """Name the revisions the author declared a version of.

    A component says so by moving ``settings.version``; a module has no label of
    its own, so reaching ``published`` cuts one and it is numbered by how many
    times that has happened. A week declares nothing and is history-only — which
    is the honest answer, not an omission.
    """
    if not inserted:
        return
    rows = []
    for item in pending:
        revision_id = inserted.get((entity_type, item['entity_id']))
        if not revision_id:
            continue
        label = version_label_for(entity_type, item)
        if not label:
            continue
        rows.append((
            entity_type, item['entity_id'], label, revision_id,
            item['content_status'], actor['email'], actor['name'],
        ))
    if not rows:
        return
    placeholder = '(%s, %s, %s, %s, %s, %s, %s)'
    query = (
        f'insert into {qualified(VERSIONS_TABLE)} '
        f'(entity_type, entity_id, version_label, revision_id, content_status, actor_email, actor_name) values '
        f'{", ".join([placeholder] * len(rows))} '
        f'on conflict (entity_type, entity_id, version_label) do nothing'
    )
    with connection.cursor() as cursor:
        cursor.execute(query, [value for row in rows for value in row])


def version_label_for(entity_type, item):
    previous = item.get('previous') or {}
    if entity_type == 'component':
        label = clean(item.get('version_label'))
        # Cut on the label the author typed, and only when it moved. The first
        # revision counts as a move: 0.1 is a version, it just happens to be the
        # first one.
        if label and label != clean(previous.get('version_label')):
            return label[:32]
        return ''
    if entity_type == 'module':
        status = clean(item.get('content_status')).lower()
        was = clean(previous.get('content_status')).lower()
        if status == 'published' and status != was:
            return f'v{module_publish_count(item["entity_id"]) + 1}'[:32]
    return ''


def module_publish_count(entity_id):
    with connection.cursor() as cursor:
        cursor.execute(
            f'select count(*) from {qualified(VERSIONS_TABLE)} where entity_type = %s and entity_id = %s',
            ['module', entity_id],
        )
        row = cursor.fetchone()
    return int(row[0]) if row else 0


def provision_history_tables():
    """Create the history tables. Local/test only — production runs the SQL file.

    Mirrors ``backend/sql/2026-09-09_curriculum_record_versions.sql``; keep the
    two in step.
    """
    if not schema_gate.runtime_bootstrap_allowed():
        return False
    json_type = 'jsonb' if connection.vendor == 'postgresql' else 'text'
    serial = 'bigserial primary key' if connection.vendor == 'postgresql' else 'integer primary key autoincrement'
    stamp = 'timestamptz not null default now()' if connection.vendor == 'postgresql' else 'timestamp not null default current_timestamp'
    try:
        with connection.cursor() as cursor:
            if connection.vendor == 'postgresql':
                cursor.execute('create schema if not exists "curriculum"')
            cursor.execute(f'''
                create table if not exists {qualified(REVISIONS_TABLE)} (
                    id {serial},
                    entity_type varchar(32) not null,
                    entity_id varchar(128) not null,
                    revision_no integer not null,
                    action varchar(16) not null,
                    module_catalogue_id varchar(128) not null default '',
                    parent_id varchar(128) not null default '',
                    title text not null default '',
                    version_label varchar(32) not null default '',
                    content_status varchar(64) not null default '',
                    snapshot {json_type} not null,
                    changed_fields {json_type} not null default '[]',
                    actor_email varchar(255) not null default '',
                    actor_name varchar(255) not null default '',
                    reason varchar(255) not null default '',
                    actor_type varchar(32) not null default '',
                    triggered_by_email varchar(255) not null default '',
                    triggered_by_name varchar(255) not null default '',
                    source varchar(64) not null default '',
                    metadata {json_type} not null default '{{}}',
                    created_at {stamp},
                    unique (entity_type, entity_id, revision_no)
                )
            ''')
            # A database provisioned before Phase 2 has the table but not the
            # columns. Existing names are read first rather than relying on a
            # failed ALTER: in Postgres a raised statement aborts the whole
            # transaction, which would take the rest of this provisioning with
            # it. One ALTER at a time, because sqlite adds one column per
            # statement.
            if connection.vendor == 'postgresql':
                cursor.execute(
                    'select column_name from information_schema.columns '
                    'where table_schema = %s and table_name = %s',
                    ['curriculum', REVISIONS_TABLE],
                )
            else:
                cursor.execute(f'pragma table_info("{REVISIONS_TABLE}")')
            rows = cursor.fetchall()
            existing = {row[0] if connection.vendor == 'postgresql' else row[1] for row in rows}
            for column, ddl in (
                ('actor_type', "varchar(32) not null default ''"),
                ('triggered_by_email', "varchar(255) not null default ''"),
                ('triggered_by_name', "varchar(255) not null default ''"),
                ('source', "varchar(64) not null default ''"),
                ('metadata', f"{json_type} not null default '{{}}'"),
            ):
                if column not in existing:
                    cursor.execute(f'alter table {qualified(REVISIONS_TABLE)} add column {column} {ddl}')
            cursor.execute(f'''
                create table if not exists {qualified(VERSIONS_TABLE)} (
                    id {serial},
                    entity_type varchar(32) not null,
                    entity_id varchar(128) not null,
                    version_label varchar(32) not null,
                    revision_id bigint not null,
                    content_status varchar(64) not null default '',
                    note text not null default '',
                    actor_email varchar(255) not null default '',
                    actor_name varchar(255) not null default '',
                    created_at {stamp},
                    unique (entity_type, entity_id, version_label)
                )
            ''')
            cursor.execute(
                f'create index if not exists record_revisions_entity_idx on {qualified(REVISIONS_TABLE)} '
                f'(entity_type, entity_id, revision_no desc)'
            )
            cursor.execute(
                f'create index if not exists record_revisions_module_idx on {qualified(REVISIONS_TABLE)} '
                f'(module_catalogue_id, created_at desc)'
            )
            cursor.execute(
                f'create index if not exists record_revisions_created_idx on {qualified(REVISIONS_TABLE)} (created_at desc)'
            )
            # The three filters the audit trail offers over its date window.
            cursor.execute(
                f'create index if not exists record_revisions_actor_created_idx on {qualified(REVISIONS_TABLE)} '
                f'(actor_email, created_at desc)'
            )
            cursor.execute(
                f'create index if not exists record_revisions_type_created_idx on {qualified(REVISIONS_TABLE)} '
                f'(entity_type, created_at desc)'
            )
            cursor.execute(
                f'create index if not exists record_revisions_source_created_idx on {qualified(REVISIONS_TABLE)} '
                f"(source, created_at desc)"
            )
            cursor.execute(
                f'create index if not exists record_versions_entity_idx on {qualified(VERSIONS_TABLE)} '
                f'(entity_type, entity_id, created_at desc)'
            )
    except DatabaseError:
        logger.warning('Could not provision curriculum history tables.', exc_info=True)
        return False
    reset_availability()
    return True
