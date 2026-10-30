"""Authentication tables — the ``login`` schema on the enrolment (Neon) database.

Why a separate schema and not ``django.contrib.auth``
-----------------------------------------------------
Django's own auth tables live on the ``default`` database (SQLite here — see
``learner_api.routers.EnrolmentRouter``). The people who need to sign in are
rows in the Neon ``enrolment`` schema, created by the three creation forms:

    enrolment."Created_users"  -> learners      (Learner_type: apprenticeship | commercial)
    enrolment."Employers"      -> employers
    enrolment."Staff_users"    -> staff/admins  (Position: Caseowner | Admin | ...)

Putting credentials on a different database from the identities they belong to
means no join and no referential integrity. So the credential store lives beside
them on Neon, in its own ``login`` schema — separate from ``enrolment`` because a
password hash has a different blast radius than an address field, and schema-level
GRANTs are the cheapest way to keep it that way.

The schema is named ``login`` and not ``auth``: this Neon project already has an
``auth`` schema owned by ``cloud_admin`` (Neon's own platform tooling), which
``neondb_owner`` cannot create tables in.

The link back to a person is (``subject_type``, ``subject_id``) rather than three
nullable foreign keys: exactly one of the three tables owns any given account, and
a three-way exclusive-arc FK is more constraint machinery than it earns. A partial
unique index on the pair keeps one account per person.

``managed = False`` throughout, matching every other Neon-backed model in this
project: the DDL is applied by ``python manage.py apply_login_tables``, not by
Django migrations (the router refuses to migrate this database at all).

Secrets in these tables are never stored in a form that can be replayed:
``Login_accounts."Password_hash"`` is a Django password hash, and the invitation
and reset tables store only a SHA-256 of the token that was emailed. A read of
this schema does not let the reader sign in as anyone.
"""
from django.db import models


# --- subject_type values: which creation form / table owns the account ---------
SUBJECT_LEARNER = "learner"
SUBJECT_EMPLOYER = "employer"
SUBJECT_STAFF = "staff"

SUBJECT_CHOICES = (SUBJECT_LEARNER, SUBJECT_EMPLOYER, SUBJECT_STAFF)

#: Table each subject_type resolves to, for the identity lookups in ``identity.py``.
SUBJECT_TABLES = {
    SUBJECT_LEARNER: 'enrolment."Created_users"',
    SUBJECT_EMPLOYER: 'enrolment."Employers"',
    SUBJECT_STAFF: 'enrolment."Staff_users"',
}


# --- role values ---------------------------------------------------------------
# The role drives what the SPA shows after login and what the API authorises.
# Staff positions collapse into two roles: "Admin" is the only position that gets
# ROLE_ADMIN; every other position is staff-level. Learners and employers get
# their own role regardless of any column on their row.
ROLE_ADMIN = "admin"
ROLE_STAFF = "staff"
ROLE_EMPLOYER = "employer"
ROLE_LEARNER = "learner"

ROLE_CHOICES = (ROLE_ADMIN, ROLE_STAFF, ROLE_EMPLOYER, ROLE_LEARNER)


class AdvancedAdminLearnerScope(models.Model):
    """Aptem IDs explicitly approved for the Advanced Admin workspace."""

    aptem_id = models.TextField(primary_key=True)
    programme_code = models.CharField(max_length=3)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'login"."Advanced_admin_learner_scope'


class LoginAccount(models.Model):
    """One sign-in identity — login."Login_accounts".

    A row exists as soon as somebody is invited; ``password_hash`` stays empty
    until they complete the invitation and choose their first password. An
    account with no password can never authenticate (see ``check_password``),
    so an un-actioned invitation is not a way in.
    """

    id = models.BigAutoField(primary_key=True, db_column="id")

    # --- who this account belongs to (see module docstring) ---
    subject_type = models.TextField(db_column="Subject_type")
    subject_id = models.BigIntegerField(db_column="Subject_id")

    # Normalised (lowercased, trimmed) at write time by the serializers; the
    # unique index is on the raw column, so writers must normalise consistently.
    email = models.TextField(db_column="Email")
    display_name = models.TextField(db_column="Display_name", null=True, blank=True)

    role = models.TextField(db_column="Role")

    # Django password hash (``django.contrib.auth.hashers``), not a raw password.
    # Empty string = invited but never set a password = cannot sign in.
    password_hash = models.TextField(db_column="Password_hash", blank=True, default="")
    password_set_at = models.DateTimeField(db_column="Password_set_at", null=True, blank=True)

    is_active = models.BooleanField(db_column="Is_active", default=True)

    # --- lockout state (see ``security.py``) ---
    # Reset to 0 on any successful sign-in. ``Locked_until`` in the future means
    # the account refuses passwords even if correct.
    failed_attempts = models.IntegerField(db_column="Failed_attempts", default=0)
    locked_until = models.DateTimeField(db_column="Locked_until", null=True, blank=True)
    last_login_at = models.DateTimeField(db_column="Last_login_at", null=True, blank=True)
    last_login_ip = models.TextField(db_column="Last_login_ip", null=True, blank=True)

    created_at = models.DateTimeField(db_column="Created_at", auto_now_add=True)
    updated_at = models.DateTimeField(db_column="Updated_at", auto_now=True)

    class Meta:
        managed = False
        # Emitted by Django as "login"."Login_accounts".
        db_table = 'login"."Login_accounts'

    def __str__(self):
        return f"{self.email} [{self.role}]"

    @property
    def has_password(self):
        return bool(self.password_hash)


class LoginSession(models.Model):
    """A signed-in browser session — login."Login_sessions".

    Server-side sessions rather than a self-contained JWT: sign-out, "this
    account was disabled", and password-change-revokes-everything all have to
    take effect immediately, and a stateless token cannot be withdrawn before it
    expires. The cost is one indexed lookup per request.

    Only the SHA-256 of the session token is stored, so a leaked database dump
    does not yield usable session cookies.
    """

    id = models.BigAutoField(primary_key=True, db_column="id")

    account = models.ForeignKey(
        LoginAccount,
        on_delete=models.DO_NOTHING,
        db_column="Account_id",
        related_name="sessions",
    )

    token_hash = models.CharField(db_column="Token_hash", max_length=64, unique=True)

    expires_at = models.DateTimeField(db_column="Expires_at")
    revoked_at = models.DateTimeField(db_column="Revoked_at", null=True, blank=True)

    # Recorded for the "your recent sign-ins" surface and incident review.
    ip_address = models.TextField(db_column="Ip_address", null=True, blank=True)
    user_agent = models.TextField(db_column="User_agent", null=True, blank=True)

    created_at = models.DateTimeField(db_column="Created_at", auto_now_add=True)
    last_seen_at = models.DateTimeField(db_column="Last_seen_at", null=True, blank=True)

    #: Whether "remember me" was ticked at sign-in. Persisted because renewal
    #: happens long after login and has to know which policy governs this
    #: session — see ``security.session_policy``. Defaults False, which is also
    #: what the backfill gives rows created before the column existed: the safer
    #: of the two policies is the right guess when the answer is unknown.
    remember = models.BooleanField(db_column="Remember", default=False)

    class Meta:
        managed = False
        db_table = 'login"."Login_sessions'

    def __str__(self):
        return f"session for {self.account_id}"


class _EmailedToken(models.Model):
    """Shared shape for the two single-use emailed-token tables.

    Both an invitation and a reset are the same object: a high-entropy token
    emailed to an address, redeemable once, before an expiry. Only its SHA-256
    is kept — the plaintext exists in the email and nowhere else, so neither
    table can be read to obtain a working link.
    """

    id = models.BigAutoField(primary_key=True, db_column="id")

    token_hash = models.CharField(db_column="Token_hash", max_length=64, unique=True)
    email = models.TextField(db_column="Email")

    expires_at = models.DateTimeField(db_column="Expires_at")
    used_at = models.DateTimeField(db_column="Used_at", null=True, blank=True)

    created_at = models.DateTimeField(db_column="Created_at", auto_now_add=True)
    created_ip = models.TextField(db_column="Created_ip", null=True, blank=True)

    class Meta:
        abstract = True

    @property
    def is_pending(self):
        return self.used_at is None


class Invitation(models.Model):
    """A "set your first password" invitation — login."Invitations".

    Issued by the three creation forms when "Invite to platform" is ticked, and
    re-issuable from the account list. Carries the account it will activate;
    redeeming it sets that account's first password.
    """

    id = models.BigAutoField(primary_key=True, db_column="id")

    account = models.ForeignKey(
        LoginAccount,
        on_delete=models.DO_NOTHING,
        db_column="Account_id",
        related_name="invitations",
    )

    token_hash = models.CharField(db_column="Token_hash", max_length=64, unique=True)
    email = models.TextField(db_column="Email")

    expires_at = models.DateTimeField(db_column="Expires_at")
    used_at = models.DateTimeField(db_column="Used_at", null=True, blank=True)

    # Who triggered the invite, for the audit trail. Free text (an email or
    # "system"), not an FK: the sender may be a staff account, a management
    # command, or the seeding step.
    invited_by = models.TextField(db_column="Invited_by", null=True, blank=True)
    sent_at = models.DateTimeField(db_column="Sent_at", null=True, blank=True)
    # Null until an email is actually dispatched; records the transport result
    # so an invitation that was created but never delivered is visible.
    send_error = models.TextField(db_column="Send_error", null=True, blank=True)

    # An administrator has seen this failure and dealt with it (re-sent the
    # invitation, corrected the address, or judged it not worth chasing). The row
    # stays — the send really was attempted and this table is the record of it —
    # but an acknowledged failure stops counting as something needing attention
    # on the Super Admin dashboard. Null means nobody has acknowledged it.
    acknowledged_at = models.DateTimeField(db_column="Acknowledged_at", null=True, blank=True)
    acknowledged_by = models.TextField(db_column="Acknowledged_by", null=True, blank=True)

    created_at = models.DateTimeField(db_column="Created_at", auto_now_add=True)
    created_ip = models.TextField(db_column="Created_ip", null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'login"."Invitations'

    @property
    def is_pending(self):
        return self.used_at is None

    def __str__(self):
        return f"invitation for {self.email}"


class PasswordReset(models.Model):
    """A "forgotten password" token — login."Password_resets".

    Separate from Invitation despite the near-identical shape: the two have
    different lifetimes and different meanings in an audit ("never onboarded"
    vs "lost their password"), and merging them would need a discriminator
    column that every query then has to remember to filter on.
    """

    id = models.BigAutoField(primary_key=True, db_column="id")

    account = models.ForeignKey(
        LoginAccount,
        on_delete=models.DO_NOTHING,
        db_column="Account_id",
        related_name="password_resets",
    )

    token_hash = models.CharField(db_column="Token_hash", max_length=64, unique=True)
    email = models.TextField(db_column="Email")

    expires_at = models.DateTimeField(db_column="Expires_at")
    used_at = models.DateTimeField(db_column="Used_at", null=True, blank=True)

    sent_at = models.DateTimeField(db_column="Sent_at", null=True, blank=True)
    send_error = models.TextField(db_column="Send_error", null=True, blank=True)

    # An administrator has seen this failure and dealt with it (re-sent the
    # invitation, corrected the address, or judged it not worth chasing). The row
    # stays — the send really was attempted and this table is the record of it —
    # but an acknowledged failure stops counting as something needing attention
    # on the Super Admin dashboard. Null means nobody has acknowledged it.
    acknowledged_at = models.DateTimeField(db_column="Acknowledged_at", null=True, blank=True)
    acknowledged_by = models.TextField(db_column="Acknowledged_by", null=True, blank=True)

    created_at = models.DateTimeField(db_column="Created_at", auto_now_add=True)
    created_ip = models.TextField(db_column="Created_ip", null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'login"."Password_resets'

    @property
    def is_pending(self):
        return self.used_at is None

    def __str__(self):
        return f"password reset for {self.email}"


class LoginAudit(models.Model):
    """Append-only record of authentication events — login."Login_audit".

    Every sign-in attempt, invitation, reset and password change lands here,
    successful or not. Kept deliberately denormalised (``Email`` as text, no FK
    to the account) so a failed attempt against an address that has no account —
    the exact thing worth noticing — is still recordable.
    """

    id = models.BigAutoField(primary_key=True, db_column="id")

    # One of the EVENT_* constants below.
    event = models.TextField(db_column="Event")
    email = models.TextField(db_column="Email", null=True, blank=True)
    account_id = models.BigIntegerField(db_column="Account_id", null=True, blank=True)
    succeeded = models.BooleanField(db_column="Succeeded", default=False)
    # Short machine-readable reason on failure ("bad_password", "locked", ...).
    reason = models.TextField(db_column="Reason", null=True, blank=True)

    ip_address = models.TextField(db_column="Ip_address", null=True, blank=True)
    user_agent = models.TextField(db_column="User_agent", null=True, blank=True)

    created_at = models.DateTimeField(db_column="Created_at", auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'login"."Login_audit'

    def __str__(self):
        return f"{self.event} {self.email} {'ok' if self.succeeded else 'fail'}"


# Event names used in LoginAudit.event.
EVENT_LOGIN = "login"
EVENT_LOGOUT = "logout"
EVENT_INVITE_SENT = "invite_sent"
EVENT_INVITE_ACCEPTED = "invite_accepted"
EVENT_RESET_REQUESTED = "reset_requested"
EVENT_RESET_COMPLETED = "reset_completed"
EVENT_PASSWORD_CHANGED = "password_changed"
