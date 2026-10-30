"""Recording what changed, in every workspace.

Curriculum Studio has recorded its saves for a while: ``curriculum_api``'s write
helpers hand the rows they wrote to ``versioning.record_rows``, which buffers
them, waits for the commit and writes one revision per record that genuinely
moved. That machinery is sound and hard-won -- it is rollback-safe, it collapses
a save cycle that touches a row five times into one revision, and it diffs
against the stored history rather than against a re-read of the table.

None of it is curriculum-specific. What was curriculum-specific was the list of
tables it knew about. This module widens that list, and adds the two things the
rest of the LMS needs that curriculum did not:

* **A workspace per entity.** The Changes feed can then say which workspace a
  save belongs to, and the Audit Trail can stop hard-coding ``['curriculum']``
  as the answer to "what can you speak for?".

* **Redaction.** Curriculum's authoring tables hold no personal detail, so an
  allowlist of columns was enough. ``enrolment."Created_users"`` holds dates of
  birth, addresses and national insurance numbers. Those must remain auditable
  -- somebody changing a learner's date of birth is exactly the kind of thing an
  audit exists to show -- without the audit log becoming a second copy of the
  data. So a redacted column records a digest: the change is visible, the value
  is not.

Three capture mechanisms, because no one of them sees everything:

* **Signals** (``post_save``/``post_delete``/``m2m_changed``) for ORM writes,
  which is how ``learner_api`` and ``enrolment_api`` mostly write.
* **:class:`AuditedQuerySet`** for ``update()``, ``bulk_create()`` and
  ``bulk_update()``, which emit no signals at all.
* **Explicit calls** to :func:`record_table_rows` for raw SQL, which is how
  ``curriculum_api`` and ``coach_api`` mostly write.

Nothing here can fail a save. Every entry point swallows its own errors, and the
revision write happens inside its own SAVEPOINT after the caller's transaction
has committed.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from django.db import DEFAULT_DB_ALIAS, connection, models, transaction
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_save

from curriculum_api import versioning

logger = logging.getLogger(__name__)

#: entity_type -> workspace key, as ``system_audit.pages`` names workspaces.
WORKSPACE_BY_ENTITY: dict[str, str] = {}

#: entity_type -> (label, href). What a reader should see instead of the raw
#: type, and where that kind of record lives. Curriculum keeps its own copy in
#: ``curriculum_api.quality.ENTITY_LABELS``; this is for everything else.
ENTITY_LABELS: dict[str, tuple[str, str]] = {}

#: entity_type -> the path to ONE record, with ``{id}`` standing for its id.
#: Where a record has a page of its own, a trail row should open that record and
#: not the list it sits in -- an auditor reading "who changed this learner's end
#: date" wants the learner, not the directory. Absent for the record types that
#: genuinely have no page of their own: a staff account is edited in a dialog on
#: the directory, so the directory is the honest destination.
RECORD_HREFS: dict[str, str] = {}

#: entity_type -> (created attribute, updated attribute) on the model. Read into
#: the row's ``created_at`` / ``updated_at`` keys, which is where
#: ``versioning.looks_newly_created`` looks, and deliberately NOT into
#: ``columns``: those are the snapshot, and a pair of stamps that moves on every
#: save would turn each one into a reported change. Curriculum keeps the same
#: separation by leaving timestamps out of ``SNAPSHOT_COLUMNS`` while its raw
#: rows still carry them.
TIMESTAMP_ATTRS: dict[str, tuple[str, str]] = {}

#: Curriculum registered its own tables before this module existed, and its
#: entity types all belong to one workspace.
for _config in versioning.VERSIONED_TABLES.values():
    WORKSPACE_BY_ENTITY.setdefault(_config['entity_type'], 'curriculum')


def register(
    table,
    *,
    workspace,
    entity_type,
    key,
    title='',
    title_join=' ',
    title_fallback='',
    parent='',
    status='',
    context=(),
    parents=(),
    json_columns=(),
    columns=(),
    redact=(),
    using=DEFAULT_DB_ALIAS,
    label='',
    href='',
    record_href='',
):
    """Declare a table as audited.

    ``columns`` is an allowlist and the only thing that reaches history: a
    column not named here cannot arrive by accident, however the write path
    changes later. ``redact`` narrows it further -- those columns record that
    they changed without recording what to.

    The rest is what makes a recorded change *readable*, and is the difference
    between a workspace being in the log and a workspace being in the Audit
    Trail. Curriculum declares all of it in ``versioning``; everything else
    declares it here, at the point of registration, so a newly-wired record type
    arrives with a name and a place rather than as a primary key on a blank row.

    * ``title`` / ``title_join`` / ``title_fallback`` -- the record's own name.
      One column or several: a coaching meeting is named by whose it is and what
      kind it is, and neither alone identifies it.
    * ``parent`` -- the column the indexed ``parent_id`` is taken from.
    * ``status`` -- the column whose value is the record's state, which the
      trail shows beside it.
    * ``context`` -- the columns that place the record, in reading order. They
      become the line under the title: "Ahmed Ali › Level 3 Business ›
      Sept 2026".
    * ``parents`` -- the columns that name where the record *sits*. A save that
      touches only these is reported as a move rather than as an edit.
    * ``json_columns`` -- columns to parse before diffing, so a change inside
      one reads as the field that moved rather than as "the whole blob changed".
    """
    # Checked before anything is registered. A registration that fails halfway
    # is worse than one that is refused: the entity type would already be
    # filterable and already be collecting columns, while belonging to no
    # workspace -- so its changes would appear in every workspace's feed.
    missing = set(redact) - set(columns)
    if missing:
        # A redaction naming a column that is not collected is not a redaction
        # -- it is a line of configuration that looks like one. Loud, because
        # the failure it hides is a value reaching history.
        raise ValueError(
            f'{entity_type}: redacted columns are not in the snapshot: {sorted(missing)}'
        )
    if not workspace:
        raise ValueError(f'{entity_type}: an audited record must belong to a workspace')
    # Same reasoning as the redaction check, one step further out: a title,
    # context or parent column that is not collected is a line of configuration
    # that looks like it names the record and silently names nothing.
    declared = set(columns)
    named = {column for column in _as_columns(title) if column}
    named |= {column for column in _as_columns(title_fallback) if column}
    named |= set(context) | set(parents)
    if parent:
        named.add(parent)
    if status:
        named.add(status)
    unknown = named - declared
    if unknown:
        raise ValueError(
            f'{entity_type}: these columns are read but not collected: {sorted(unknown)}'
        )

    versioning.VERSIONED_TABLES[table] = {
        'entity_type': entity_type,
        'key': key,
        'title': title,
        'using': using,
    }
    versioning.SNAPSHOT_COLUMNS[entity_type] = tuple(columns)
    versioning.ENTITY_TYPES.add(entity_type)
    if redact:
        versioning.REDACTED_COLUMNS[entity_type] = frozenset(redact)
    versioning.ENTITY_FACT_FIELDS[entity_type] = {
        'title': tuple(_as_columns(title)),
        'title_join': title_join,
        'title_fallback': tuple(_as_columns(title_fallback)),
        'parent': parent,
        'status': status,
    }
    if context:
        versioning.ENTITY_CONTEXT_FIELDS[entity_type] = tuple(context)
    if parents:
        versioning.PARENT_COLUMNS[entity_type] = tuple(parents)
    if json_columns:
        versioning.JSON_SNAPSHOT_COLUMNS.update(json_columns)
    WORKSPACE_BY_ENTITY[entity_type] = workspace
    if label or href:
        ENTITY_LABELS[entity_type] = (
            label or entity_type.replace('_', ' ').capitalize(),
            href,
        )
    if record_href:
        RECORD_HREFS[entity_type] = record_href


def _as_columns(value):
    """One column name or several, always as a tuple."""
    if not value:
        return ()
    return (value,) if isinstance(value, str) else tuple(value)


def workspace_for_entity(entity_type):
    """Which workspace a recorded change belongs to, or '' when unregistered."""
    owner = WORKSPACE_BY_ENTITY.get(entity_type, '')
    if owner == 'admin' and entity_type != 'staff_record':
        return 'enrolment'
    return owner


def entity_types_for_workspace(workspace):
    """Every record type that belongs to one workspace, or all of them for ''."""
    if not workspace:
        return sorted(WORKSPACE_BY_ENTITY)
    return sorted(
        entity for entity in WORKSPACE_BY_ENTITY if workspace_for_entity(entity) == workspace
    )


#: An edit whose saves cancelled each other out once folded together.
EMPTY_EDIT_CLAUSE = "not (action = 'updated' and cast(changed_fields as text) in ('[]', 'null'))"


def revision_workspace_clause(workspace):
    """Scope by the recorded source page; older rows use their record owner.

    A tutor can change a coaching record from the tutor workspace. Record type
    alone cannot answer where that edit was made. An empty scope must never
    silently become the entire system's history.
    """
    clause, params = _workspace_scope(workspace)
    return f'({clause}) and {EMPTY_EDIT_CLAUSE}', params


def _workspace_scope(workspace):
    if not workspace or workspace == 'all':
        return '1 = 1', []
    owned = entity_types_for_workspace(workspace)
    fallback = 'entity_type in (' + ','.join(['%s'] * len(owned)) + ')' if owned else '1 = 0'
    if not versioning.metadata_columns_available():
        return fallback, owned
    source = "metadata ->> 'page_workspace'" if connection.vendor == 'postgresql' else "json_extract(metadata, '$.page_workspace')"
    return f"({source} = %s or (coalesce({source}, '') = '' and ({fallback})))", [workspace, *owned]


def record_href(entity_type, entity_id, fallback=''):
    """Where one recorded record lives, or the list it belongs to.

    An id that cannot be put in a URL falls back to the list rather than
    producing a broken link -- a trail row that 404s is worse than one that
    opens the directory.
    """
    template = RECORD_HREFS.get(entity_type)
    entity_id = str(entity_id or '').strip()
    if not template or not entity_id:
        return fallback
    try:
        return template.format(id=quote(entity_id, safe=''))
    except Exception:
        return fallback


def change_workspaces():
    """The workspaces whose saves the Changes feed can actually speak for.

    Derived from what is registered rather than listed by hand, so the Audit
    Trail starts telling the truth about a newly-wired workspace on the day it
    is wired, not on the day somebody remembers to edit a list.
    """
    return sorted({workspace_for_entity(entity) for entity in WORKSPACE_BY_ENTITY if workspace_for_entity(entity)})


# --------------------------------------------------------------------- rows

def record_table_rows(table, rows, *, reason='', deleted=False, using=None, before_rows=None):
    """Record rows a raw-SQL write left behind. Never raises.

    The alias defaults to the one the table was registered against, because a
    raw-SQL caller usually has no opinion about which connection it used and
    getting that wrong is what decides whether a rolled-back write can leave a
    revision claiming it succeeded.
    """
    config = versioning.VERSIONED_TABLES.get(table)
    alias = using or (config or {}).get('using') or DEFAULT_DB_ALIAS
    versioning.record_rows(table, rows, reason=reason, deleted=deleted, using=alias, before_rows=before_rows)


# ------------------------------------------------------------------- models

def row_from_instance(instance, columns, timestamps=()):
    """The allowlisted columns of a model instance, as a plain row.

    ``timestamps`` is a ``(created, updated)`` attribute pair. Their values ride
    along under ``created_at`` / ``updated_at`` for the create-or-recorded
    decision and never enter the snapshot, which is built from the registered
    columns alone.
    """
    row = {}
    for column in columns:
        try:
            value = getattr(instance, column)
        except Exception:
            # A deferred field, or a relation whose target has gone. Recorded as
            # absent rather than skipped, so the snapshot keeps its shape and an
            # unreadable column does not read as a field that was removed.
            value = None
        if isinstance(value, models.Model):
            value = getattr(value, 'pk', None)
        row[column] = value
    if timestamps:
        created_attr, updated_attr = timestamps
        row['created_at'] = getattr(instance, created_attr, None)
        row['updated_at'] = getattr(instance, updated_attr, None)
    return row


def _timestamp_pair(model, entity_type, timestamps):
    """Validate a ``timestamps=`` declaration against the model it names.

    Refused loudly, like an uncollected title column: a pair naming a field the
    model does not have would read as ``None`` on every save, and every create
    would quietly go back to reading as "first recorded".
    """
    if not timestamps:
        return ()
    pair = tuple(timestamps)
    if len(pair) != 2:
        raise ValueError(f'{entity_type}: timestamps must be a (created, updated) pair, got {pair!r}')
    fields = {field.name for field in model._meta.concrete_fields}
    missing = [attr for attr in pair if attr not in fields]
    if missing:
        raise ValueError(f'{entity_type}: timestamp fields are not on {model.__name__}: {missing}')
    return pair


def register_model(
    model,
    *,
    workspace,
    entity_type,
    key='pk',
    title='',
    title_join=' ',
    title_fallback='',
    parent='',
    status='',
    context=(),
    parents=(),
    json_columns=(),
    columns=(),
    redact=(),
    table=None,
    using=None,
    label='',
    href='',
    record_href='',
    timestamps=(),
):
    """Audit every ORM save and delete of this model.

    ``columns`` names model attributes, not database columns -- the instance is
    what is read, so the attribute names are what the snapshot is built from.

    ``timestamps`` names the model's ``(created, updated)`` attribute pair, so a
    save that inserted the row can be reported as a create rather than as
    "first recorded". It is kept out of ``columns`` on purpose: the stamps are
    evidence about the row, not content of it, and must not show up in diffs.
    Only a pair the model genuinely stores belongs here -- never a stamp the
    caller would have to invent.

    Connected with ``dispatch_uid`` so registering twice (a module imported
    under two names, a test reloading the app) connects one handler rather than
    recording every save twice.
    """
    table = table or model._meta.db_table
    alias = using or DEFAULT_DB_ALIAS
    fields = tuple(columns)
    if key not in fields:
        fields = (key,) + fields
    stamps = _timestamp_pair(model, entity_type, timestamps)

    register(
        table,
        workspace=workspace,
        entity_type=entity_type,
        key=key,
        title=title,
        title_join=title_join,
        title_fallback=title_fallback,
        parent=parent,
        status=status,
        context=context,
        parents=parents,
        json_columns=json_columns,
        columns=fields,
        redact=redact,
        using=alias,
        label=label,
        href=href,
        record_href=record_href,
    )
    if stamps:
        TIMESTAMP_ATTRS[entity_type] = stamps
    else:
        TIMESTAMP_ATTRS.pop(entity_type, None)

    uid = f'system_audit:{entity_type}'

    def before_save(sender, instance, raw=False, using=None, **kwargs):
        instance._audit_before_row = None
        if raw or instance.pk is None:
            return
        try:
            old = sender._base_manager.using(using or alias).filter(pk=instance.pk).first()
            if old is not None:
                instance._audit_before_row = row_from_instance(old, fields)
        except Exception:
            logger.warning('Could not read the previous %s state.', entity_type, exc_info=True)

    def on_save(sender, instance, created=False, raw=False, using=None, **kwargs):
        # `raw` is a fixture load: the rows are being installed, not changed by
        # anybody, and there is no actor to attribute them to.
        if raw:
            return
        try:
            # update_fields and F() expressions make the in-memory object an
            # unreliable account of what was actually persisted.
            saved = sender._base_manager.using(using or alias).get(pk=instance.pk)
            before = getattr(instance, '_audit_before_row', None)
            # The stamps are offered only for an INSERT. They are evidence that
            # has to agree with the insert, not a substitute for it: an UPDATE
            # that leaves `updated_at` alone (`update_fields` without it) on a
            # row never edited since creation would otherwise read as created.
            record_table_rows(table, [row_from_instance(saved, fields, stamps if created else ())],
                              using=using or alias,
                              before_rows=[before] if before is not None else None)
        except Exception:
            logger.warning('Could not record a %s save.', entity_type, exc_info=True)
        finally:
            instance.__dict__.pop('_audit_before_row', None)

    def on_delete(sender, instance, using=None, **kwargs):
        try:
            record_table_rows(
                table,
                [row_from_instance(instance, fields)],
                reason='deleted',
                deleted=True,
                using=using or alias,
            )
        except Exception:
            logger.warning('Could not record a %s delete.', entity_type, exc_info=True)

    def on_m2m(sender, instance, action, reverse=False, using=None, **kwargs):
        # Only the settled actions. `pre_*` fires before the link table has
        # moved, so recording then would describe a change that may not happen.
        if action not in {'post_add', 'post_remove', 'post_clear'}:
            return
        if reverse or not isinstance(instance, model):
            return
        try:
            record_table_rows(table, [row_from_instance(instance, fields)], using=using or alias)
        except Exception:
            logger.warning('Could not record a %s link change.', entity_type, exc_info=True)

    senders = [model, *(candidate for candidate in model._meta.apps.get_models()
                       if candidate._meta.proxy and candidate._meta.concrete_model is model)]
    for sender in senders:
        pre_save.connect(before_save, sender=sender, weak=False, dispatch_uid=uid)
        post_save.connect(on_save, sender=sender, weak=False, dispatch_uid=uid)
        post_delete.connect(on_delete, sender=sender, weak=False, dispatch_uid=uid)
    for field in model._meta.many_to_many:
        m2m_changed.connect(
            on_m2m,
            sender=getattr(model, field.name).through,
            weak=False,
            dispatch_uid=f'{uid}:{field.name}',
        )


def attach_bulk_capture(model, entity_type=''):
    """Route this model's managers through :class:`AuditedQuerySet`.

    Called by the app that registers a model, not by :func:`register_model`, so
    that switching bulk capture on is a decision each workspace makes for
    itself. It is not free -- ``update()`` gains a key read and a read-back, and
    ``delete()`` materialises the rows it is about to remove -- and turning that
    on for every registered model at once would change the cost of 74
    ``update()`` calls in ``learner_api`` as a side effect of wiring up a
    different workspace.

    Signals see ``save()``, ``create()`` and ``delete()`` on an instance and
    nothing else. ``QuerySet.update()`` is the hole that matters here: a coach
    meeting moving to ``awaiting-signature`` is written as
    ``CoachCalendarEvent.objects.filter(...).update(status=...)``, which emits
    no signal at all -- so the status changes people most want to trace were
    the ones the trail could not see.

    Only a manager still using the plain ``QuerySet`` is swapped. A model with
    a hand-written queryset class has behaviour of its own in these methods, and
    silently re-basing it to gain an audit entry would be trading a working
    feature for a log line. That case is reported rather than forced.
    """
    # `get_queryset` instantiates `_queryset_class` on every call, so swapping
    # the attribute is enough and needs no cache to be cleared.
    for manager in model._meta.local_managers:
        if manager._queryset_class is models.QuerySet:
            manager._queryset_class = AuditedQuerySet
        elif not issubclass(manager._queryset_class, AuditedQuerySet):
            logger.warning(
                'Bulk writes of %s via %s are not recorded: it uses a custom '
                'queryset (%s), so update()/bulk_*() bypass the audit trail.',
                entity_type or model.__name__, manager.name, manager._queryset_class.__name__,
            )


# --------------------------------------------------------------- bulk writes

class AuditedQuerySet(models.QuerySet):
    """A QuerySet whose bulk writes are recorded.

    ``update()``, ``bulk_create()``, ``bulk_update()`` and the fast path inside
    ``delete()`` emit no ``post_save`` or ``post_delete`` at all -- Django goes
    straight to SQL. A model audited only through signals therefore has a hole
    in its history exactly where the largest changes go through, which is the
    worst possible place for one.

    The rows are read back rather than assumed, because an ``update()`` can
    match rows the caller never enumerated and a database default or trigger can
    leave a row holding something the caller did not send.

    Each method checks the registry *before* doing any extra reading. Both
    ``update()`` and ``delete()`` have to read rows that the plain queryset
    never would -- the affected keys, and the rows about to go -- and on an
    unregistered model that read buys nothing. A model can lose its
    registration (a failed import in ``records``) while its manager keeps this
    class, and the query it would then add to every bulk delete is exactly the
    kind of cost an audit trail must not impose when it is switched off.
    """

    def update(self, **fields):
        if not self._config():
            return super().update(**fields)
        columns = versioning.SNAPSHOT_COLUMNS[self._config()['entity_type']]
        with transaction.atomic(using=self.db):
            before = list(self.select_for_update())
            updated = super().update(**fields)
            self._record_pks([row.pk for row in before], before_rows=[row_from_instance(row, columns) for row in before])
        return updated

    update.alters_data = True

    def bulk_create(self, objs, *args, **kwargs):
        created = super().bulk_create(objs, *args, **kwargs)
        # A conflict-tolerant bulk_create may have updated or skipped rows that
        # were already there, so it is no evidence of an insert and its rows
        # carry no stamps -- they go down as edits or as "first recorded".
        upsert = kwargs.get('ignore_conflicts') or kwargs.get('update_conflicts')
        self._record_pks([obj.pk for obj in created if obj.pk is not None], inserted=not upsert)
        return created

    bulk_create.alters_data = True

    def bulk_update(self, objs, fields, *args, **kwargs):
        result = super().bulk_update(objs, fields, *args, **kwargs)
        # The objects may contain unsaved values in fields outside `fields`.
        self._record_pks([obj.pk for obj in objs])
        return result

    bulk_update.alters_data = True

    def delete(self):
        if not self._config():
            return super().delete()
        # Read before the delete: afterwards there is nothing left to read, and
        # a snapshot of what went is the whole point of recording a deletion.
        doomed = list(self)
        result = super().delete()
        self._record_instances(doomed, deleted=True)
        return result

    delete.alters_data = True

    # -- internals

    def _config(self):
        return versioning.VERSIONED_TABLES.get(self.model._meta.db_table)

    def _record_instances(self, instances, deleted=False, before_rows=None, inserted=False):
        config = self._config()
        if not config or not instances:
            return
        columns = versioning.SNAPSHOT_COLUMNS.get(config['entity_type'], ())
        # Stamps only for rows this call inserted. `update()` and
        # `bulk_update()` do not touch an `auto_now` field, so a never-edited
        # pre-history row would still hold a matching pair after them.
        stamps = TIMESTAMP_ATTRS.get(config['entity_type'], ()) if inserted else ()
        try:
            record_table_rows(
                self.model._meta.db_table,
                [row_from_instance(instance, columns, stamps) for instance in instances],
                reason='bulk-delete' if deleted else '',
                deleted=deleted,
                using=self.db,
                before_rows=before_rows,
            )
        except Exception:
            logger.warning('Could not record a bulk write of %s.', self.model.__name__, exc_info=True)

    def _record_pks(self, pks, before_rows=None, inserted=False):
        if not pks:
            return
        try:
            # A fresh queryset: `self` has already been consumed by the update
            # and its filters may no longer match the rows that were changed.
            self._record_instances(list(self.model._base_manager.using(self.db).filter(pk__in=pks)),
                                   before_rows=before_rows, inserted=inserted)
        except Exception:
            logger.warning('Could not read back a bulk update of %s.', self.model.__name__, exc_info=True)
