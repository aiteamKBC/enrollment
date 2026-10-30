"""Permission carrier for the Actual Hours review.

``audit_api`` reads Neon schemas through raw SQL and owns no tables of its own,
but Django needs a model to hang custom permissions on. This one is
``managed = False`` with ``default_permissions = ()``: no table is ever created
for it in any database — the migration exists purely so ``post_migrate`` creates
the two ``auth_permission`` rows on the default database, where Django's auth
tables live.
"""

from django.db import models


class ActualHoursReview(models.Model):
    class Meta:
        managed = False
        default_permissions = ()
        permissions = [
            ("propose_actual_hours", "Can propose Learner Journal actual hours"),
            ("approve_actual_hours", "Can approve or reject proposed actual hours"),
        ]
        verbose_name = "actual hours review"
