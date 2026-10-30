"""Lossless dictionary transport for repeated authoring text over remote SQL.

The builders still receive the original rows and types. PostgreSQL sends long
repeated values once per statement; ordinary columns retain their native types.
Both the dictionary and rows come from one snapshot, with the caller's ordering.
"""
from copy import deepcopy


def compact_authoring_rows(table, where_sql, params, order_sql, *, columns=None, reuse_unchanged=False):
    from . import views

    if views.connection.vendor != 'postgresql':
        return views.authoring_fetch_all(table, where_sql, params, order_sql, columns=columns)
    views.ensure_module_authoring_tables()
    available = views.column_names(table)
    selected = [c for c in columns if c in available] if columns else sorted(available)
    if reuse_unchanged and 'id' in selected:
        from .authoring_row_cache import read_verified_rows
        return read_verified_rows(table, where_sql, params, order_sql, selected)
    return _compact_rows(table, where_sql, params, order_sql, selected)


def _compact_rows(table, where_sql, params, order_sql, selected, *, fingerprint_sql=None):
    from . import views

    shared = [c for c in ('description', views.REFLECTION_QUESTION_COLUMN, 'ksb_description', 'settings_json', 'ksb_mappings') if c in selected]
    if not shared and not fingerprint_sql:
        return views.authoring_fetch_all(table, where_sql, params, order_sql, columns=selected)
    quote = views.quote_ident
    ranks = {column: f'__text_rank_{index}' for index, column in enumerate(shared)}
    rank_sql = ', '.join(
        f'dense_rank() over (order by to_jsonb({quote(column)})::text collate "C") as {quote(rank)}'
        for column, rank in ranks.items()
    )
    result_fields = ', '.join(
        (f'case when row_number() over (partition by to_jsonb({quote(c)})::text collate "C" '
         f'order by {order_sql}) = 1 then {quote(c)} end as {quote(c)}')
        if c in ranks else quote(c) for c in selected
    )
    # Integer ranks avoid hashing/collisions; bytewise comparison keeps Unicode,
    # whitespace, null and empty values distinct regardless of database collation.
    # Send the native value only on its first occurrence in the result ordering.
    # In particular JSON columns keep the connection's normal JSON decoding.
    extras = [value for value in (rank_sql, fingerprint_sql) if value]
    query = (
        f'select {result_fields}{", " + ", ".join(extras) if extras else ""} '
        f'from {views.authoring_table_name(table)} where {where_sql} order by {order_sql}'
    )
    with views.connection.cursor() as cursor:
        cursor.execute(query, params)
        rows = views.rows_as_dicts(cursor)
    if not rows:
        return []
    values = {c: {} for c in shared}
    for row in rows:
        for column in shared:
            rank = row.pop(ranks[column])
            value = values[column].setdefault(rank, row[column])
            row[column] = deepcopy(value) if isinstance(value, (dict, list)) else value
    return rows
