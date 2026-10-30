"""Narrow PostgreSQL inputs for programme KSB summary statistics."""
from . import views


# Match Python str.strip(), including tabs/newlines and Unicode whitespace.
_STRIP_CHARS = ''.join(chr(value) for value in range(0x3100) if chr(value).isspace())


def grouped_scope_rows(module_ids):
    """Lossless counts/weights for programme stats, before delivery deduplication."""
    flags = ('deleted_at', 'is_programme_deleted', 'is_deleted', 'isDeleted', 'isProgrammeDeleted')
    specs = (
        (views.AUTHORING_WEEKS_TABLE, ('module_catalogue_id', 'library_state', *flags), views.active_week_rows),
        # Match COMPONENT_KSB_SCOPE_COLUMNS: do not introduce additional legacy
        # deletion predicates the existing component reader does not select.
        (views.AUTHORING_COMPONENTS_TABLE, ('module_catalogue_id', 'type', 'deleted_at', 'is_programme_deleted', 'library_state'), views.active_component_rows),
        (views.AUTHORING_KSB_MAPPINGS_TABLE, ('module_catalogue_id', 'ksb_code', 'source_type', 'source_id', 'weight', *flags), views.active_mapping_rows),
    )
    ids = sorted(set(module_ids))
    results = []
    for table, fields, active in specs:
        fields = [views.quote_ident(field) for field in fields if views.has_column(table, field)]
        selections, groups, params = list(fields), list(fields), []
        if table == views.AUTHORING_KSB_MAPPINGS_TABLE:
            # Stats only test whether a mapping is component-owned; they never
            # display or resolve its component id. Preserve Python strip rules.
            expression = "case when length(btrim(component_id, %s)) > 0 then 'mapped' else '' end"
            selections.append(expression + ' as component_id')
            groups.append(str(len(selections)))
            params.append(_STRIP_CHARS)
        results.append(active(views.fetch_all(
            f'select {", ".join(selections)}, count(*) as _stats_count '
            f'from {views.authoring_table_name(table)} '
            f'where module_catalogue_id in ({", ".join(["%s"] * len(ids))}) '
            f'group by {", ".join(groups)}',
            [*params, *ids],
        )))
    return tuple(results)


def progress_totals(learner_ids, codes):
    """Select the same latest achieved attempt, transferring ordered weights.

    Python's authoritative completion rule evaluates the small kind/pass set.
    SQL then deduplicates eligible attempts and packs their weights by learner
    and code. Keeping weights ordered (rather than SUM/ROUND in SQL) preserves
    the existing per-row Decimal rounding and floating-point addition exactly.
    """
    outcomes = views.fetch_all('''
        select distinct kind, passed from "Learner"."learner_progress_entries"
        where learner_id = any(%s)
    ''', [learner_ids])
    eligible = [row for row in outcomes if views.progress_counts_as_achieved(
        kind=row['kind'], passed=row['passed'],
    )]
    if not eligible:
        return {}
    values = ', '.join(['(%s::text, %s::boolean)'] * len(eligible))
    params = [value for row in eligible for value in (row['kind'], row['passed'])]
    exclusion = views.and_not_excluded('k', views.connection)
    sql = f'''
        select learner_id, ksb_code,
               array_agg(weight order by submitted_at desc nulls last, progress_id desc) as weights
        from (
            select facts.*, row_number() over (
                partition by learner_id, component_key,
                             case when component_key is null then progress_id end, ksb_code
                order by submitted_at desc nulls last, progress_id desc
            ) as attempt_rank
            from (
                select p.learner_id, p.id as progress_id, p.submitted_at, k.ksb_code,
                       nullif(btrim(p.component_ref, %s), '') as component_key,
                       coalesce(max(k.weight), 0) as weight
                from "Learner"."learner_progress_entries" p
                join (values {values}) eligible(kind, passed)
                  on p.kind is not distinct from eligible.kind
                 and p.passed is not distinct from eligible.passed
                join "Learner"."learner_progress_ksbs" k on k.progress_id = p.id {exclusion}
                where p.learner_id = any(%s) and k.ksb_code = any(%s)
                group by p.learner_id, p.id, p.submitted_at, p.component_ref, k.ksb_code
            ) facts
        ) attempts
        where attempt_rank = 1
        group by learner_id, ksb_code
    '''
    rows = views.fetch_all(sql, [_STRIP_CHARS, *params, learner_ids, codes])
    totals = {}
    for row in rows:
        code = views.coverage_normalise_code(row['ksb_code'])
        total = 0.0
        for weight in row['weights']:
            total += views.float_weight(weight or 0)
        totals.setdefault(row['learner_id'], {})[code] = total
    return totals
