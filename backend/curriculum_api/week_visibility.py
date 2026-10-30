"""Which deleted curriculum rows a learner-facing read may still show.

A running module shows only its live weeks, components and KSB mappings. An
archived module keeps showing the rows that were archived WITH it -- the
learner's history of a module that was withdrawn under them -- and nothing
else.

"Archived with it" is two facts, not one. The row was stamped by the same
cascade as its module (a module delete stamps its children with the module's
own id; a programme delete stamps module and children alike with the programme
id), and it was stamped in the same moment. ``deleted_via_parent`` alone is not
enough: a module that is deleted and later brought back by a save keeps the
children of that earlier deletion stamped with its id, and a module rebuilt
after that carries two sets of weeks per number. Read on the parent stamp alone,
the stale set outranks the live one and the learner sees a previous version of
the course.

The five-minute window is the one ``recordings_library`` already uses for the
same question: one archive writes all of its rows inside a single transaction.
"""

ARCHIVE_EVENT_WINDOW = "interval '5 minutes'"


def visible_child_sql(alias):
    """SQL predicate: this module child row may be shown.

    For any ``curriculum`` table whose rows carry ``module_catalogue_id``,
    ``deleted_at`` and ``deleted_via_parent`` -- weeks, components, KSB
    mappings. ``alias`` is the name the row has in the calling query -- its
    alias, or the bare table name for an unaliased ``FROM curriculum.<table>``.
    It is required: an unqualified column inside the subquery would bind to the
    module row.
    """
    w = alias
    return (
        f"({w}.deleted_at IS NULL OR EXISTS ("
        f"SELECT 1 FROM curriculum.modules archived_module "
        f"WHERE archived_module.module_catalogue_id={w}.module_catalogue_id "
        f"AND archived_module.deleted_at IS NOT NULL "
        f"AND COALESCE({w}.deleted_via_parent, '') <> '' "
        f"AND {w}.deleted_via_parent IN (archived_module.module_catalogue_id, archived_module.deleted_via_parent) "
        f"AND {w}.deleted_at BETWEEN archived_module.deleted_at - {ARCHIVE_EVENT_WINDOW} "
        f"AND archived_module.deleted_at + {ARCHIVE_EVENT_WINDOW}))"
    )


def visible_week_sql(alias):
    """SQL predicate: this ``curriculum.weeks`` row may be shown."""
    return visible_child_sql(alias)
