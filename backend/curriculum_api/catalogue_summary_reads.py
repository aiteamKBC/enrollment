"""Lossless grouped inputs for the compact module catalogue only."""
from . import views


def grouped_catalogue_content_rows():
    """Return repeated component facts once, and duplicate mapping facts once.

    Hours remain unrounded input values: the caller applies the same per-item
    rounding before multiplying by row_count. Keep live titles and week ids so
    repeated titles, missing weeks and multiple sessions per week remain exact.
    Optional deletion/library fields stay available to the Python predicates.
    """
    table = views.AUTHORING_COMPONENTS_TABLE
    flags = ('deleted_at', 'is_programme_deleted', 'library_state')
    where, params = views.live_authoring_rows_sql(table, flags)
    fields = [field for field in ('module_catalogue_id', 'week_id', 'type', 'expected_otjh', *flags)
              if views.has_column(table, field)]
    selections = [views.quote_ident(field) for field in fields]
    # normalise_component_type only changes '-' to '_'; no case/space folding.
    title = "case when type in ('live_session', 'live-session') then title else null end"
    query = (
        f'select {", ".join(selections)}, {title} as title, count(*) as row_count '
        f'from {views.authoring_table_name(table)}'
        + (f' where {where}' if where else '')
        + f' group by {", ".join(selections)}, {title}'
    )
    components = views.active_component_rows(views.fetch_all(query, params))

    table = views.AUTHORING_KSB_MAPPINGS_TABLE
    fields = [field for field in (
        'module_catalogue_id', 'ksb_code', 'deleted_at', 'is_programme_deleted',
        'is_deleted', 'isDeleted', 'isProgrammeDeleted',
    ) if views.has_column(table, field)]
    # Distinct raw keys first; the existing trim/deletion logic still runs in
    # Python, preserving optional legacy flags without SQL truthiness changes.
    mappings = views.active_mapping_rows(views.fetch_all(
        f'select distinct {", ".join(views.quote_ident(field) for field in fields)} '
        f'from {views.authoring_table_name(table)}',
    ))
    return components, mappings
