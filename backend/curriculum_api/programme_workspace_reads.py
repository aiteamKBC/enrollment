"""Shared, ordered facts for the programme workspace's two module builders."""
from collections import defaultdict


# Component text the workspace response never carries: its weekStructure is an
# outline (see programme_modules_workspace_payload), and no module-level field
# it keeps is derived from a component's title, description or reflection
# question. The one place that text still counts is curriculum_item_richness(),
# which measures each candidate's JSON length when unique_curriculum_items()
# picks between two representations of the same module.
def workspace_component_text_columns():
    from . import views
    return ('title', 'description', views.REFLECTION_QUESTION_COLUMN)


def programme_structure_child_rows(module_ids, *, component_text=True):
    """The child rows both module builders read, for the union of their ids.

    ``component_text=False`` reads the components without their title,
    description and reflection question -- on a large programme the bulk of
    what the read moves -- and gives every row the same empty value for each.
    Only a caller that has checked component_text_cancels() on what it built
    may keep the result; see there.
    """
    from . import views
    from .authoring_read_transport import compact_authoring_rows

    ids = sorted({views.clean_str(value) for value in module_ids if views.clean_str(value)})
    if not ids:
        return {'components': [], 'mappings': [], 'modules': [], 'weeks': []}
    scope = f'module_catalogue_id in ({", ".join(["%s"] * len(ids))})'

    def read(table, order, columns=None):
        # These SQL filters exclude only rows the existing active_* predicates
        # already reject. Keep those Python predicates in the builders as well.
        live, params = views.live_authoring_rows_sql(table, ('deleted_at', 'is_programme_deleted'))
        where = f'({scope}) and ({live})' if live else scope
        return compact_authoring_rows(table, where, [*ids, *params], order, columns=columns, reuse_unchanged=True)

    # The module and week reads at the end, sent together in one round trip.
    views.prefetch_authoring_reads([
        ((views.AUTHORING_MODULES_TABLE, scope, ids), {}),
        ((views.AUTHORING_WEEKS_TABLE, scope, ids, 'display_order, week_number, id'), {}),
    ])
    component_columns = views.CATALOGUE_SUMMARY_COMPONENT_COLUMNS
    if not component_text:
        text = workspace_component_text_columns()
        component_columns = tuple(column for column in component_columns if column not in text)
    components = read(views.AUTHORING_COMPONENTS_TABLE, 'display_order, id', component_columns)
    if not component_text:
        for row in components:
            for column in workspace_component_text_columns():
                row[column] = ''

    return {
        'components': components,
        'mappings': read(views.AUTHORING_KSB_MAPPINGS_TABLE, 'created_at, id'),
        # Both builders read these too, each for its own overlapping id list:
        # two module reads and two week reads of the same programme. Read once
        # for the union, unfiltered, exactly as get_authoring_structure_payloads
        # reads them; it takes its own ids from here. Weeks are in a total order
        # (id is unique), so each caller's slice is the sequence it read before.
        'modules': views.authoring_fetch_all(views.AUTHORING_MODULES_TABLE, scope, ids),
        'weeks': views.authoring_fetch_all(views.AUTHORING_WEEKS_TABLE, scope, ids, 'display_order, week_number, id'),
    }


def _component_layout(item):
    """Which components, week by week, a candidate's JSON carries."""
    if isinstance(item.get('components'), list) and item.get('components'):
        # Top-level components are not built from the shared child rows.
        return None
    weeks = item.get('weekStructure') or item.get('week_structure') or []
    if not isinstance(weeks, list):
        return ()
    return tuple(
        tuple(
            str(component.get('id')) if isinstance(component, dict) else repr(component)
            for component in (week.get('components') or [])
        ) if isinstance(week, dict) else repr(week)
        for week in weeks
    )


def component_text_cancels(items, identity_fields):
    """Whether unique_curriculum_items() decides the same with or without text.

    Its richness is ``len(json.dumps(item))`` plus counts that do not involve
    text. Where every candidate sharing a key carries the very same components
    -- the same rows, built by the same code -- each component's text adds the
    same length to both sides of every comparison it makes, so blanking it
    changes no decision. Any key whose candidates carry different components,
    or components not built from the shared rows, makes this False, and the
    caller reads the text after all. Keys mirror unique_curriculum_items(); an
    item it gives a row key of its own is never compared.
    """
    from . import views

    layouts = defaultdict(set)
    for item in items or []:
        if not isinstance(item, dict):
            continue
        key = next((views.clean_str(item.get(field)) for field in identity_fields if views.clean_str(item.get(field))), '')
        if not key:
            key = views.normalise(item.get('name') or item.get('title'))
        if not key:
            continue
        layout = _component_layout(item)
        if layout is None:
            return False
        layouts[key].add(layout)
    return all(len(found) == 1 for found in layouts.values())
