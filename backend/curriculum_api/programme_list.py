"""Programme cards without building the overview's unrelated content trees."""
import json
from collections import Counter

from . import views


_DELETION_FIELDS = ('deleted_at', 'is_programme_deleted', 'is_deleted', 'isDeleted', 'isProgrammeDeleted')
_MODULE_FIELDS = (
    'module_catalogue_id', 'title', 'programme_id', 'programme_name', 'cohort_id', 'cohort_name',
    'group_id', 'group_name', 'sessions_number', 'weeks_number', 'status', 'tutor_name',
    'start_date', 'end_date', 'weekly_schedule', 'weeklySchedule', 'deliveryMetadata', 'session_week_day', 'weekDays',
    *_DELETION_FIELDS,
)
_GROUP_FIELDS = (
    'group_id', 'group_name', 'programme_id', 'programme_name', 'cohort_id', 'cohort_name',
    'coach_name', 'status', *_DELETION_FIELDS,
)
_COHORT_FIELDS = (
    'cohort_id', 'cohort_name', 'programme_id', 'programme_name', 'status', 'start_date', 'end_date',
    *_DELETION_FIELDS,
)
# Match the authoritative structure counter's projection, including legacy
# optional-column semantics. No week descriptions, content or schedules.
_WEEK_FIELDS = ('module_catalogue_id', 'deleted_at', 'is_programme_deleted', 'library_state')


def programme_list_sources():
    sources = [
        ('modules', views.AUTHORING_MODULES_TABLE, _MODULE_FIELDS, ''),
        ('groups', views.GROUPS_TABLE, _GROUP_FIELDS, ''),
        ('cohorts', views.COHORT_AUTHORING_DETAILS_TABLE, _COHORT_FIELDS, ''),
        ('weeks', views.AUTHORING_WEEKS_TABLE, _WEEK_FIELDS, ''),
    ]
    if views.connection.vendor != 'postgresql':
        return {
            **{name: views.authoring_fetch_all(table, columns=fields) for name, table, fields, _ in sources},
            'program_configs': views.get_program_config_rows(), 'ksb_profiles': views.get_ksb_profile_rows(),
        }
    views.ensure_module_authoring_tables()
    sources.append(('program_configs', 'programmes', None,
                    "coalesce((to_jsonb(source_row)->>'display_order')::numeric, 0), name"))
    has_profiles = views.table_exists('ksb_profiles')
    if has_profiles:
        sources.append(('ksb_profiles', 'ksb_profiles', None, 'is_active desc, name'))
    selections, params = [], []
    for name, table, fields, ordering in sources:
        expression = 'to_jsonb(source_row)'
        if fields is not None:
            expression = '(select jsonb_object_agg(key, value) from jsonb_each(to_jsonb(source_row)) where key = any(%s))'
            params.append(list(fields))
        order_sql = f' order by {ordering}' if ordering else ''
        selections.append(
            f'(select coalesce(jsonb_agg({expression}{order_sql}), \'[]\'::jsonb) '
            f'from {views.authoring_table_name(table)} source_row) as {views.quote_ident(name)}'
        )
    selections.append('to_regclass(\'"Learner"."learners"\') is not null as "has_learner_table"')
    # One round trip and statement snapshot; names are server constants, keys
    # are parameters. JSON key selection tolerates optional legacy columns.
    row = views.fetch_all('select ' + ', '.join(selections), params)[0]
    result = {name: json.loads(value) if isinstance(value, str) else value for name, value in row.items()}
    if not has_profiles:
        result['ksb_profiles'] = []
    return result


def programme_list_learner_counts(source):
    if 'has_learner_table' not in source:
        return views.active_learner_programme_counts()
    if not source['has_learner_table']:
        return {}, {}
    # Match active_learner_programme_counts exactly: named learners, by explicit
    # id when present, otherwise normalized name. That existing rule does not
    # filter lifecycle_status. Group in SQL instead of transferring every row;
    # JSON key lookup supports installations without the optional programme_id.
    rows = views.fetch_all('''
        select programme, coalesce(to_jsonb(learner)->>'programme_id', '') as programme_id,
               count(*) as learner_count
        from "Learner"."learners" learner
        where coalesce(btrim(programme), '') <> ''
        group by programme, coalesce(to_jsonb(learner)->>'programme_id', '')
    ''')
    by_id, by_name = Counter(), Counter()
    for row in rows:
        programme_id = views.clean_str(row['programme_id'])
        name = views.normalise(row['programme'])
        if programme_id:
            by_id[programme_id] += row['learner_count']
        elif name:
            by_name[name] += row['learner_count']
    return dict(by_id), dict(by_name)


def programme_list_needs_module_enrichment(programmes, source, counts, *, include_archived):
    # Legacy catalogue rows and programmes represented only by unattached
    # modules retain the existing ranking, deduplication and reconstruction.
    if views.table_exists('Modules'):
        return True
    known = {
        views.normalise(programme.get(key)) for programme in programmes
        for key in ('id', 'sourceId', 'name', 'standard')
    }
    if not include_archived:
        known.update(
            views.normalise(config.get(key)) for config in source['program_configs']
            if views.is_archived_program_config(config)
            for key in ('programme_id', 'program_id', 'id', 'name')
        )
    if any(
        views.normalise(row.get('programme_name')) not in known | {'', 'unassigned', 'unassignedprogramme'}
        for row in source['modules']
    ):
        return True
    for programme in programmes:
        if views.programme_structure_counts(programme, *counts):
            continue
        # Enrichment leaves authoritative counts alone. A truly empty programme
        # also stays empty; ambiguous/archived ownership keeps the full path.
        if programme['modules'] or any(
            views.module_matches_programme(programme, {
                'programmeId': row.get('programme_id'), 'programme': row.get('programme_name'),
            }) for row in source['modules']
        ):
            return True
    return False


def build_programme_list(visibility, *, force=False):
    with views.curriculum_read_scope():
        source = programme_list_sources()
        training = views.authoring_modules_as_training_rows(
            module_rows=source['modules'], group_rows=source['groups'], cohort_rows=source['cohorts'],
        )
        profiles = source['ksb_profiles']
        if visibility != 'all':
            training = [row for row in training if views.is_operational_training_row(row)]
            profiles = [row for row in profiles if row.get('is_active')]
        counts = views.authoring_programme_structure_counts(source=source)
        programmes = views.build_programmes(
            training, source['program_configs'], profiles, include_config_only=visibility == 'all',
            only_stats_for_ids=frozenset(), structure_counts=counts,
            learner_counts=programme_list_learner_counts(source),
        )
        if not programme_list_needs_module_enrichment(
            programmes, source, counts, include_archived=visibility == 'all',
        ):
            return programmes
        # Keep the established response for legacy and module-only programmes.
        # This fallback is cached as the same list, never as a partial overview.
        overview = views.build_curriculum_payload_from_rows(
            views.get_curriculum_rows(compact=True), visibility, compact=True, programme_list=True,
        )
        modules = views.enrich_modules_with_authoring(
            overview['modules'], include_programme_deleted=visibility == 'all', include_content=False,
        )
        return views.enrich_programmes_with_module_counts(
            overview['programmes'], modules, modules_enriched=True, include_archived=visibility == 'all',
        )
