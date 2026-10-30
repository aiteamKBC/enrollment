"""The LMS-wide audit trail.

Three things live here, and they are deliberately separate:

* ``pages`` -- the route table. One place that knows what every SPA URL in the
  LMS is called, which workspace it belongs to, and which segment of it is a
  record id. Every page name in the trail is the server's own reading of the
  URL, never a label the browser supplied.
* ``activity`` -- the reading half. Who opened which page, when, and what they
  did there. Recorded by the browser because nothing else can see it: somebody
  opening a caseload, searching it and leaving touches no record at all.
* ``writes`` -- the writing half, app-agnostic. ``curriculum.record_revisions``
  already logs every curriculum save with a before and an after, but it is
  curriculum's own table written by curriculum's own helpers. This is the shared
  log the other workspaces write into as each is wired up, read alongside it so
  the Changes feed is one feed rather than one per app.

This is a plain package rather than a Django app on purpose: it owns no models
and no migrations. Both of its tables are created by hand against Neon, like the
rest of the curriculum's own history tables, and every read probes for them
first -- a missing table is a page that says what it cannot show, never a 500.
"""
