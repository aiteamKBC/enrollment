from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import TextField, Value
from django.db.models.functions import Coalesce, Lower, Trim
from django.utils import timezone


# The production database already owns these tables. The test settings opt into
# ordinary SQLite table names so the chat suite can run without external schemas.
CHAT_TEST_MODE = getattr(settings, "CHAT_TEST_MODE", False)

#: The ``Staff_users.Access`` grant that makes a staff account a coach. Chat is
#: not the authority on this -- ``curriculum_api.views.STAFF_ACCESS_ROLES`` uses
#: the same value against the same column.
COACH_ACCESS = "coach"


def _table_name(test_name, production_name):
    return test_name if CHAT_TEST_MODE else production_name


class ChatCoachManager(models.Manager):
    def coaches(self):
        """Only the staff rows an administrator granted coach access to.

        ``Staff_users`` holds every non-learner account, so an unfiltered lookup
        here would resolve any signed-in staff member -- an enrolment officer, a
        tutor -- to a coach principal. The normalisation matches the directory's
        own reader (``curriculum_api.views.fetch_staff_users_by_access``) so the
        two never disagree about who counts as a coach.
        """
        return (
            self.get_queryset()
            .annotate(
                normalised_access=Lower(
                    Trim(
                        # output_field: the column is TextField and Value("")
                        # infers CharField, which Coalesce refuses to mix.
                        Coalesce("access", Value(""), output_field=TextField())
                    )
                )
            )
            .filter(normalised_access=COACH_ACCESS)
        )


class ChatCoach(models.Model):
    """Read-only view of the coaches in ``enrolment.Staff_users``.

    A coach is a staff user an administrator invited and granted coach access
    to, so this reads the staff directory rather than a table of its own. It
    used to map ``curriculum.coaches``, a second copy of the same people that
    ``curriculum_api`` migration 0052 drops; chat was that table's last reader.

    ``id`` is the directory's integer pk. ``Staff_users`` carries no unique index
    on the email, only ``enrolment_staff_users_email_idx`` on
    ``lower(btrim("Email"))``, so two rows can still share an address -- which is
    why the services layer resolves a principal to every matching row rather
    than one (see ``principal_owns_identity``).
    """

    id = models.AutoField(primary_key=True)
    name = models.TextField(db_column="Username")
    email = models.TextField(db_column="Email")
    job_title = models.TextField(db_column="Position")
    access = models.TextField(db_column="Access", null=True, blank=True)

    objects = ChatCoachManager()

    class Meta:
        managed = CHAT_TEST_MODE
        db_table = _table_name("chat_test_coaches", 'enrolment"."Staff_users')

    def __str__(self):
        return self.name


class ChatLearner(models.Model):
    """Read-only view of the existing ``Learner.learners`` identity table."""

    id = models.BigAutoField(primary_key=True)
    full_name = models.TextField()
    email = models.EmailField(max_length=320)

    class Meta:
        managed = CHAT_TEST_MODE
        db_table = _table_name("chat_test_learners", 'Learner"."learners')

    def __str__(self):
        return self.full_name


class Conversation(models.Model):
    """A private conversation between one external coach and one learner."""

    coach = models.ForeignKey(
        ChatCoach,
        db_column="coach_id",
        on_delete=models.CASCADE,
        related_name="chat_conversations_as_coach",
    )
    learner = models.ForeignKey(
        ChatLearner,
        db_column="learner_id",
        on_delete=models.CASCADE,
        related_name="chat_conversations_as_learner",
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        managed = CHAT_TEST_MODE
        db_table = _table_name("chat_test_conversations", 'chat"."conversations')
        ordering = ("-updated_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("coach", "learner"),
                name="uq_chat_coach_learner",
            ),
        ]
        indexes = [
            models.Index(fields=("coach", "-updated_at"), name="chat_conv_coach_upd_idx"),
            models.Index(fields=("learner", "-updated_at"), name="chat_conv_learner_upd_idx"),
        ]

    def clean(self):
        if self.coach_id and self.learner_id and str(self.coach_id) == str(self.learner_id):
            raise ValidationError({"learner": "A user cannot be both participants."})
        super().clean()

    def __str__(self):
        return f"Conversation {self.pk}: {self.coach_id} -> {self.learner_id}"


class Message(models.Model):
    """A message stored in the existing polymorphic chat table."""

    conversation = models.ForeignKey(
        Conversation,
        db_column="conversation_id",
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender_type = models.CharField(max_length=20, db_column="sender_type")
    sender_coach = models.ForeignKey(
        ChatCoach,
        db_column="sender_coach_id",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="chat_messages_as_coach",
    )
    sender_learner = models.ForeignKey(
        ChatLearner,
        db_column="sender_learner_id",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="chat_messages_as_learner",
    )
    body = models.TextField(max_length=5000)
    created_at = models.DateTimeField()
    edited_at = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        managed = CHAT_TEST_MODE
        db_table = _table_name("chat_test_messages", 'chat"."messages')
        ordering = ("created_at", "id")
        indexes = [
            models.Index(fields=("conversation", "created_at"), name="chat_msg_conv_created_idx"),
        ]

    @property
    def sender_id(self):
        return self.sender_coach_id if self.sender_type == "coach" else self.sender_learner_id

    @property
    def sender(self):
        return self.sender_coach if self.sender_type == "coach" else self.sender_learner

    def clean(self):
        if not self.body or not self.body.strip():
            raise ValidationError({"body": "Message body cannot be empty."})
        if len(self.body) > 5000:
            raise ValidationError({"body": "Message body cannot exceed 5000 characters."})
        if self.sender_type not in {"coach", "learner"}:
            raise ValidationError({"sender_type": "Sender type must be coach or learner."})
        if self.sender_type == "coach" and (not self.sender_coach_id or self.sender_learner_id):
            raise ValidationError({"sender_type": "A coach message requires a coach sender."})
        if self.sender_type == "learner" and (not self.sender_learner_id or self.sender_coach_id):
            raise ValidationError({"sender_type": "A learner message requires a learner sender."})
        super().clean()

    def __str__(self):
        return f"Message {self.pk} in conversation {self.conversation_id}"


class MessageDeletion(models.Model):
    """A per-participant hide marker for the chat's "delete for me" action."""

    message_id = models.BigIntegerField(db_column="message_id")
    participant_type = models.CharField(max_length=20)
    participant_id = models.CharField(max_length=255)
    deleted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        # This is a new table owned by this app in both production and tests.
        managed = True
        db_table = _table_name("chat_test_message_deletions", 'chat"."message_deletions')
        constraints = [
            models.UniqueConstraint(
                fields=("message_id", "participant_type", "participant_id"),
                name="uq_chat_message_delete_participant",
            ),
        ]
        indexes = [
            models.Index(
                fields=("participant_type", "participant_id"),
                name="chat_delete_participant_idx",
            ),
        ]

    def __str__(self):
        return f"Message {self.message_id} hidden for {self.participant_type}:{self.participant_id}"


class MessageReceipt(models.Model):
    """Read/delivery state in the existing polymorphic receipt table."""

    message = models.OneToOneField(
        Message,
        db_column="message_id",
        on_delete=models.CASCADE,
        related_name="message_receipt",
    )
    recipient_type = models.CharField(max_length=20, db_column="recipient_type")
    recipient_coach = models.ForeignKey(
        ChatCoach,
        db_column="recipient_coach_id",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="chat_receipts_as_coach",
    )
    recipient_learner = models.ForeignKey(
        ChatLearner,
        db_column="recipient_learner_id",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="chat_receipts_as_learner",
    )
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = CHAT_TEST_MODE
        db_table = _table_name("chat_test_message_receipts", 'chat"."message_receipts')
        indexes = [
            models.Index(fields=("recipient_type", "read_at"), name="chat_receipt_type_read_idx"),
        ]

    @property
    def recipient_id(self):
        return self.recipient_coach_id if self.recipient_type == "coach" else self.recipient_learner_id

    def clean(self):
        if self.recipient_type not in {"coach", "learner"}:
            raise ValidationError({"recipient_type": "Recipient type must be coach or learner."})
        if self.recipient_type == "coach" and (not self.recipient_coach_id or self.recipient_learner_id):
            raise ValidationError({"recipient_type": "A coach receipt requires a coach recipient."})
        if self.recipient_type == "learner" and (not self.recipient_learner_id or self.recipient_coach_id):
            raise ValidationError({"recipient_type": "A learner receipt requires a learner recipient."})
        super().clean()

    def __str__(self):
        return f"Receipt for message {self.message_id} -> {self.recipient_type}:{self.recipient_id}"
