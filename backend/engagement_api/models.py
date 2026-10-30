import uuid

from django.db import models


class Reward(models.Model):
    """A single item in the rewards catalogue (points -> voucher/merchandise)."""

    DELIVERY_TYPE_CHOICES = [
        ('physical', 'Physical'),
        ('digital', 'Digital'),
    ]

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    points = models.IntegerField()
    category = models.CharField(max_length=100, blank=True, default='')
    delivery_type = models.CharField(max_length=20, choices=DELIVERY_TYPE_CHOICES)
    stock = models.IntegerField(default=0)
    total_claimed = models.IntegerField(default=0)
    image = models.TextField(blank=True, default='')
    popular = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        # Emitted by Django as "Engagement"."rewards".
        db_table = 'Engagement"."rewards'

    def __str__(self):
        return self.name


class VoucherClaim(models.Model):
    """A learner's request to redeem a Reward for points."""

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('fulfilled', 'Fulfilled'),
    ]

    # Learner records live in another team's app (not in scope here yet), so
    # we just store the id/name the frontend sends rather than a real FK.
    learner_id = models.CharField(max_length=100)
    learner_name = models.CharField(max_length=255)

    reward = models.ForeignKey(Reward, on_delete=models.CASCADE, related_name='claims', db_column='reward_id')
    points = models.IntegerField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    requested_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.CharField(max_length=255, null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    delivery_type = models.CharField(max_length=20, choices=Reward.DELIVERY_TYPE_CHOICES)
    delivery_method = models.CharField(max_length=20, blank=True, default='')
    delivery_detail = models.CharField(max_length=255, null=True, blank=True)
    delivery_instructions = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        # Emitted by Django as "Engagement"."voucher_claims".
        db_table = 'Engagement"."voucher_claims'

    def __str__(self):
        return f'{self.learner_name} -> {self.reward.name} ({self.status})'


class Recognition(models.Model):
    """A badge/certificate/spotlight/etc. awarded to a learner."""

    TYPE_CHOICES = [
        ('badge', 'Badge'),
        ('certificate', 'Certificate'),
        ('spotlight', 'Spotlight'),
        ('milestone', 'Milestone'),
        ('achievement', 'Achievement'),
    ]

    # Learner records live in another team's app (not in scope here yet), so
    # we just store the id/name the frontend sends rather than a real FK.
    learner_id = models.CharField(max_length=100)
    learner_name = models.CharField(max_length=255)
    avatar_img = models.TextField(null=True, blank=True)
    programme_code = models.CharField(max_length=50)
    programme = models.CharField(max_length=255)
    cohort = models.CharField(max_length=255)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    title = models.CharField(max_length=255)
    description = models.TextField()
    awarded_by = models.CharField(max_length=255)
    awarded_at = models.DateTimeField(auto_now_add=True)
    category = models.CharField(max_length=100)
    points = models.IntegerField(default=0)
    is_public = models.BooleanField(default=True)

    class Meta:
        managed = False
        # Emitted by Django as "Engagement"."recognitions".
        db_table = 'Engagement"."recognitions'

    def __str__(self):
        return f'{self.title} -> {self.learner_name}'


class Event(models.Model):
    """An online or offline engagement event."""

    TYPE_CHOICES = [('offline', 'Offline'), ('online', 'Online')]
    STATUS_CHOICES = [
        ('upcoming', 'Upcoming'),
        ('ongoing', 'Ongoing'),
        ('completed', 'Completed'),
    ]

    title = models.CharField(max_length=255)
    description = models.TextField()
    # Kept as free text (not DateField/TimeField) to match the frontend's
    # display strings, e.g. '13 Jun 2026' / '13:00 - 15:00'.
    date = models.CharField(max_length=100)
    time = models.CharField(max_length=100)
    event_date = models.DateField(null=True, blank=True)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    location = models.CharField(max_length=255)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    attendees = models.IntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='upcoming')
    organizer = models.CharField(max_length=255)
    check_in_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        # Emitted by Django as "Engagement"."events".
        db_table = 'Engagement"."events'

    def __str__(self):
        return self.title


class EventBooking(models.Model):
    """A learner RSVP for a community event."""

    STATUS_CHOICES = [
        ('booked', 'Booked'),
        ('cancelled', 'Cancelled'),
    ]

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='bookings', db_column='event_id')
    learner_id = models.CharField(max_length=100)
    learner_name = models.CharField(max_length=255)
    learner_email = models.EmailField(blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='booked')
    booked_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'Engagement"."event_bookings'
        constraints = [
            models.UniqueConstraint(fields=['event', 'learner_id'], name='engagement_event_booking_unique'),
        ]

    def __str__(self):
        return f'{self.learner_name} -> {self.event.title} ({self.status})'


class Club(models.Model):
    """A regional community club learners can join (London Club, Kent Club, etc.)."""

    name = models.CharField(max_length=255)
    location = models.CharField(max_length=255)
    description = models.TextField()
    ambassador = models.CharField(max_length=255)
    ambassador_role = models.CharField(max_length=255)
    members = models.IntegerField(default=0)
    # Array of initials strings for the joined-members indicator stack, e.g. ["SW", "OP"].
    sample_members = models.JSONField(default=list)
    active = models.BooleanField(default=True)

    class Meta:
        managed = False
        # Emitted by Django as "Engagement"."clubs".
        db_table = 'Engagement"."clubs'

    def __str__(self):
        return self.name


class ClubMeeting(models.Model):
    """A single meetup belonging to a Club — scheduled or awaiting a date."""

    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='meetings', db_column='club_id')
    title = models.CharField(max_length=255)
    scheduled = models.BooleanField(default=False)
    date = models.CharField(max_length=100, null=True, blank=True)
    time = models.CharField(max_length=100, null=True, blank=True)
    venue = models.CharField(max_length=255, null=True, blank=True)
    attendees = models.IntegerField(default=0)

    class Meta:
        managed = False
        # Emitted by Django as "Engagement"."club_meetings".
        db_table = 'Engagement"."club_meetings'

    def __str__(self):
        return f'{self.title} ({self.club.name})'


class ClubMembership(models.Model):
    """A learner's assignment to a Club. Staff assign learners — there is no
    learner-facing join/leave action. `status='removed'` is a soft delete
    (kept for history), matching PointsGrant/VoucherClaim never being
    hard-deleted."""

    STATUS_CHOICES = [
        ('active', 'Active'),
        ('removed', 'Removed'),
    ]

    club = models.ForeignKey(Club, on_delete=models.CASCADE, related_name='memberships', db_column='club_id')
    learner_id = models.CharField(max_length=100)
    learner_name = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    assigned_by = models.CharField(max_length=255, null=True, blank=True)
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'Engagement"."club_memberships'
        constraints = [
            # Partial in the DB (WHERE status = 'active') so a removed-then
            # reassigned learner doesn't collide with their own old row.
            models.UniqueConstraint(fields=['club', 'learner_id'], name='engagement_club_membership_active_unique'),
        ]

    def __str__(self):
        return f'{self.learner_name} @ {self.club.name} ({self.status})'


class ClubMeetingAttendance(models.Model):
    """A learner's attendance mark for a single ClubMeeting, set by staff.

    One row per (meeting, learner) — re-marking updates status in place
    (current state, not an append-only log), matching ClubMembership.
    """

    STATUS_CHOICES = [
        ('present', 'Present'),
        ('absent', 'Absent'),
    ]

    meeting = models.ForeignKey(ClubMeeting, on_delete=models.CASCADE, related_name='attendance', db_column='meeting_id')
    learner_id = models.CharField(max_length=100)
    learner_name = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    marked_by = models.CharField(max_length=255, null=True, blank=True)
    marked_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = 'Engagement"."club_meeting_attendance'
        constraints = [
            models.UniqueConstraint(fields=['meeting', 'learner_id'], name='engagement_club_meeting_attendance_unique'),
        ]

    def __str__(self):
        return f'{self.learner_name} @ meeting {self.meeting_id} ({self.status})'


class EventAttendance(models.Model):
    """A learner's attendance mark for an Event, set by staff.

    Distinct from EventBooking (an RSVP/intent-to-attend): this is whether
    they actually showed up. One row per (event, learner).
    """

    STATUS_CHOICES = [
        ('present', 'Present'),
        ('absent', 'Absent'),
    ]

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='attendance', db_column='event_id')
    learner_id = models.CharField(max_length=100)
    learner_name = models.CharField(max_length=255)
    attendee_email = models.EmailField(max_length=320, blank=True, default='')
    attendee_type = models.CharField(
        max_length=20, choices=[('learner', 'Learner'), ('guest', 'Guest')], default='learner',
    )
    attendance_source = models.CharField(max_length=30, default='manual')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    marked_by = models.CharField(max_length=255, null=True, blank=True)
    marked_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = 'Engagement"."event_attendance'
        constraints = [
            models.UniqueConstraint(fields=['event', 'learner_id'], name='engagement_event_attendance_unique'),
        ]

    def __str__(self):
        return f'{self.learner_name} @ event {self.event_id} ({self.status})'


class AttendanceIntervention(models.Model):
    """A staff-logged follow-up action for a learner's attendance risk —
    what the attendance-risk page's "Take Action" button writes to."""

    learner_id = models.CharField(max_length=100)
    learner_name = models.CharField(max_length=255)
    action = models.TextField()
    employer_notified = models.BooleanField(default=False)
    intervention_date = models.DateField(null=True, blank=True)
    created_by = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved = models.BooleanField(default=False)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'Engagement"."attendance_interventions'

    def __str__(self):
        return f'{self.learner_name}: {self.action[:40]}'


class PointsRule(models.Model):
    """A configured rule for how learners earn engagement points."""

    name = models.CharField(max_length=255)
    description = models.TextField()
    points = models.IntegerField()
    category = models.CharField(max_length=100)
    frequency = models.CharField(max_length=100)
    trigger = models.CharField(max_length=255)
    active = models.BooleanField(default=True)
    # Stable identifier other apps use to call services.grant_points() —
    # e.g. 'session_attendance' — instead of matching on the editable
    # display name. Optional: manual-only rules (a human clicks "award")
    # don't need one. Immutable once set — see points_rule_detail in views.py,
    # which deliberately excludes 'key' from the editable fields; automated
    # hooks (services.grant_points) depend on it staying stable. Unique among
    # active rules — enforced by points_rules_key_active_uniq (see sql/).
    key = models.CharField(max_length=100, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        # Emitted by Django as "Engagement"."points_rules".
        db_table = 'Engagement"."points_rules'

    def __str__(self):
        return self.name


class PointsGrant(models.Model):
    """A single award of points to a learner under a PointsRule.

    Stores its own `points` snapshot (rather than looking it up from the
    rule at read time) so grant history stays accurate even if the rule's
    point value is edited later.
    """

    rule = models.ForeignKey(PointsRule, on_delete=models.CASCADE, related_name='grants', db_column='rule_id')
    # learner_id is the engagement-space learner identifier: it holds
    # str(enrolment."Created_users".id), i.e. the same integer LoginAccount
    # exposes as account.subject_id for a signed-in learner. Kept as
    # CharField (not a real FK) because Engagement is its own unmanaged
    # schema and learner records live in enrolment's tables.
    learner_id = models.CharField(max_length=100)
    learner_name = models.CharField(max_length=255)
    points = models.IntegerField()
    awarded_at = models.DateTimeField(auto_now_add=True)
    # Idempotency key: identifies the specific real-world occurrence that
    # earned this grant (e.g. an attendance record id). Lets
    # services.grant_points() recognise a repeat call for the same
    # occurrence and return the existing grant instead of double-granting.
    # NULL for manual grants, which are allowed to repeat freely.
    # Enforced by points_grants_rule_ref_uniq (partial unique index, see sql/).
    event_reference = models.CharField(max_length=255, null=True, blank=True)
    # Provenance — who/what/why. Populated from session identity (staff
    # display_name) or a source label; NULL on pre-existing history rows.
    awarded_by = models.CharField(max_length=255, null=True, blank=True)
    # 'hook' | 'manual' | 'recognition' | 'flashcard' | 'attendance' | 'evidence' | 'adjustment'
    source_type = models.CharField(max_length=20, null=True, blank=True)
    source_id = models.CharField(max_length=255, null=True, blank=True)
    reason = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        managed = False
        # Emitted by Django as "Engagement"."points_grants".
        db_table = 'Engagement"."points_grants'

    def __str__(self):
        return f'{self.learner_name} -> {self.rule.name} (+{self.points})'


class FlashCardDeck(models.Model):
    """One authored batch of flash cards, targeted at a programme/module/week.

    Mirrors the quiz builder's QuizPackage: a curriculum/engagement author
    builds a deck for a specific programme -> module -> week, and `week_id`
    uses the SAME format the quiz builder computes
    (week-training-module-{programme_id}-{week_number}) so a later delivery
    step can reach exactly the learners a quiz for that week reaches.

    Unlike a quiz, a deck is a points-only game: it is never injected into the
    training plan and never affects progress/OTJH.
    """

    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('published', 'Published'),
    ]

    title = models.CharField(max_length=255)
    # Curriculum programme/module identifier. Plain field, no FK, matching the
    # same rationale as learner_id elsewhere.
    programme_id = models.IntegerField(null=True, blank=True)
    programme = models.CharField(max_length=255, blank=True, default='')
    module = models.CharField(max_length=255, blank=True, default='')
    week_id = models.CharField(max_length=128, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    author = models.CharField(max_length=255, blank=True, default='')
    card_count = models.IntegerField(default=0)
    ai_generated = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        # Emitted by Django as "Engagement"."flash_card_decks".
        db_table = 'Engagement"."flash_card_decks'

    def __str__(self):
        return self.title


class FlashCard(models.Model):
    """One question/answer card belonging to a FlashCardDeck.

    `question` is the front (prompt), `answer` is the back (concise answer).
    Mirrors the quiz builder's QuizQuestion.
    """

    DIFFICULTY_CHOICES = [
        ('easy', 'Easy'),
        ('medium', 'Medium'),
        ('hard', 'Hard'),
    ]

    deck = models.ForeignKey(FlashCardDeck, on_delete=models.CASCADE, related_name='cards', db_column='deck_id')
    question = models.TextField()
    answer = models.TextField()
    category = models.CharField(max_length=100, blank=True, default='')
    difficulty = models.CharField(max_length=20, choices=DIFFICULTY_CHOICES, default='medium')
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        # Emitted by Django as "Engagement"."flash_cards".
        db_table = 'Engagement"."flash_cards'
        ordering = ['sort_order', 'id']

    def __str__(self):
        return f'[deck {self.deck_id}] {self.question[:40]}'


class FlashCardView(models.Model):
    """A record of a learner flipping a specific flash card.

    One row per (card, learner) — the DB UNIQUE(flash_card_id, learner_id)
    constraint is what enforces "points awarded once per card per learner,
    ever". Next week's deck has new card ids, so its cards award again.

    Learner records live in another team's app (not in scope here yet), so we
    store the id/name the frontend sends rather than a real FK.
    """

    flash_card = models.ForeignKey(FlashCard, on_delete=models.CASCADE, related_name='views', db_column='flash_card_id')
    learner_id = models.CharField(max_length=100)
    learner_name = models.CharField(max_length=255)
    # Snapshot of what this flip awarded (the rule's value at flip time).
    points_awarded = models.IntegerField(default=0)
    viewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        # Emitted by Django as "Engagement"."flash_card_views".
        db_table = 'Engagement"."flash_card_views'

    def __str__(self):
        return f'{self.learner_name} flipped card {self.flash_card_id}'


# Feedback forms intentionally live in their own database schema, and nothing
# in the form engine depends on an Engagement route. Like the rest of this
# app's Neon-backed tables these mappings are unmanaged; see the manual SQL in
# backend/sql/2026-09-20_feedback_forms.sql.
class FeedbackForm(models.Model):
    STATUS_CHOICES = [('draft', 'Draft'), ('published', 'Published'), ('closed', 'Closed')]
    TYPE_CHOICES = [
        ('general', 'General'), ('post_lecture', 'Live Sessions Feedback'),
        ('post_event', 'Post-event'), ('event_rsvp', 'Event RSVP'),
        ('post_mcm_review', 'MCM Feedback'),
        ('post_progress_review', 'Progress Review Feedback'),
        ('mid_module', 'Mid-module Feedback'),
        ('end_module', 'End-module Feedback'),
    ]
    DELIVERY_SCOPE_CHOICES = [('manual', 'Manual'), ('all_modules', 'All modules'), ('module', 'One module')]

    title = models.CharField(max_length=255)
    form_type = models.CharField(max_length=40, choices=TYPE_CHOICES, default='general')
    delivery_scope = models.CharField(max_length=20, choices=DELIVERY_SCOPE_CHOICES, default='manual')
    template_key = models.UUIDField(default=uuid.uuid4)
    version = models.PositiveIntegerField(default=1)
    is_current = models.BooleanField(default=True)
    previous_version = models.ForeignKey(
        'self', on_delete=models.PROTECT, related_name='next_versions',
        db_column='previous_version_id', null=True, blank=True,
    )
    description = models.TextField(blank=True, default='')
    instructions = models.TextField(blank=True, default='')
    programme_id = models.CharField(max_length=255, blank=True, default='')
    programme_name = models.CharField(max_length=255, blank=True, default='')
    cohort_id = models.CharField(max_length=255, blank=True, default='')
    cohort_name = models.CharField(max_length=255, blank=True, default='')
    group_id = models.CharField(max_length=255, blank=True, default='')
    group_name = models.CharField(max_length=255, blank=True, default='')
    module_catalogue_id = models.CharField(max_length=255, blank=True, default='')
    module_name = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    start_date = models.DateTimeField(null=True, blank=True)
    due_date = models.DateTimeField(null=True, blank=True)
    anonymous_responses = models.BooleanField(default=False)
    allow_save_continue = models.BooleanField(default=True)
    allow_edit_after_submission = models.BooleanField(default=False)
    created_by = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'Feedback"."feedback_forms'


class FeedbackSection(models.Model):
    form = models.ForeignKey(FeedbackForm, on_delete=models.CASCADE, related_name='sections', db_column='form_id')
    title = models.CharField(max_length=255)
    icon = models.CharField(max_length=80, blank=True, default='ri-file-list-3-line')
    description = models.TextField(blank=True, default='')
    questions_per_delivery = models.PositiveIntegerField(null=True, blank=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        managed = False
        db_table = 'Feedback"."feedback_form_sections'
        ordering = ['sort_order', 'id']


class FeedbackQuestion(models.Model):
    section = models.ForeignKey(FeedbackSection, on_delete=models.CASCADE, related_name='questions', db_column='section_id')
    question_type = models.CharField(max_length=30)
    question_text = models.TextField()
    required = models.BooleanField(default=False)
    help_text = models.TextField(blank=True, default='')
    # Options, rating bounds and endpoint labels are deliberately one JSON
    # configuration object so new question kinds do not require new columns.
    config = models.JSONField(default=dict)
    sort_order = models.IntegerField(default=0)

    class Meta:
        managed = False
        db_table = 'Feedback"."feedback_questions'
        ordering = ['sort_order', 'id']


class FeedbackAssignment(models.Model):
    form = models.ForeignKey(FeedbackForm, on_delete=models.CASCADE, related_name='assignments', db_column='form_id')
    target_type = models.CharField(max_length=30)
    target_id = models.CharField(max_length=100, null=True, blank=True)
    assigned_by = models.CharField(max_length=255)
    assigned_at = models.DateTimeField(auto_now_add=True)
    due_date = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'Feedback"."feedback_assignments'


class FeedbackDelivery(models.Model):
    """A versioned template delivered for a lecture or module checkpoint."""

    STATUS_CHOICES = [('open', 'Open'), ('closed', 'Closed'), ('cancelled', 'Cancelled')]

    form = models.ForeignKey(FeedbackForm, on_delete=models.PROTECT, related_name='deliveries', db_column='form_id')
    occurrence_key = models.CharField(max_length=255)
    attendance_source = models.CharField(max_length=80, default='attendance')
    attendance_reference = models.CharField(max_length=255, blank=True, default='')
    programme_id = models.CharField(max_length=255, blank=True, default='')
    programme_name = models.CharField(max_length=255, blank=True, default='')
    cohort_id = models.CharField(max_length=255, blank=True, default='')
    cohort_name = models.CharField(max_length=255, blank=True, default='')
    group_id = models.CharField(max_length=255, blank=True, default='')
    group_name = models.CharField(max_length=255, blank=True, default='')
    module_catalogue_id = models.CharField(max_length=255)
    module_name = models.CharField(max_length=500, blank=True, default='')
    session_title = models.CharField(max_length=500, blank=True, default='')
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    available_at = models.DateTimeField()
    question_set_pinned = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = 'Feedback"."feedback_deliveries'


class FeedbackDeliveryQuestion(models.Model):
    """The question set drawn once for a lecture, shared by its recipients."""

    delivery = models.ForeignKey(
        FeedbackDelivery, on_delete=models.CASCADE, related_name='selected_questions', db_column='delivery_id',
    )
    question = models.ForeignKey(FeedbackQuestion, on_delete=models.PROTECT, db_column='question_id')
    sort_order = models.IntegerField(default=0)

    class Meta:
        managed = False
        db_table = 'Feedback"."feedback_delivery_questions'


class FeedbackDeliveryRecipient(models.Model):
    """A learner eligible by lecture attendance or module assignment."""

    delivery = models.ForeignKey(FeedbackDelivery, on_delete=models.CASCADE, related_name='recipients', db_column='delivery_id')
    learner_id = models.CharField(max_length=100)
    learner_name = models.CharField(max_length=255)
    programme = models.CharField(max_length=255, blank=True, default='')
    attendance_reference = models.CharField(max_length=255, blank=True, default='')
    assigned_at = models.DateTimeField(auto_now_add=True)
    # Module checkpoints may move independently for learners who have not
    # started a response. Lecture recipients continue using the delivery date.
    available_at = models.DateTimeField(null=True, blank=True)
    due_date = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'Feedback"."feedback_delivery_recipients'


class FeedbackReviewDelivery(models.Model):
    """One future completed imported review pinned to its feedback template."""

    form = models.ForeignKey(
        FeedbackForm, on_delete=models.PROTECT, related_name='review_deliveries', db_column='form_id',
    )
    source_review_id = models.BigIntegerField(unique=True)
    aptem_review_id = models.TextField(blank=True, default='')
    learner_id = models.CharField(max_length=100)
    learner_name = models.CharField(max_length=255, blank=True, default='')
    review_type = models.CharField(max_length=255)
    review_name = models.CharField(max_length=500, blank=True, default='')
    completed_at = models.DateTimeField()
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'Feedback"."feedback_review_deliveries'


class FeedbackEventCampaign(models.Model):
    """A published post-event form made available for one Engagement event."""

    STATUS_CHOICES = [('open', 'Open'), ('closed', 'Closed'), ('cancelled', 'Cancelled')]

    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name='feedback_campaigns', db_column='event_id')
    form = models.ForeignKey(FeedbackForm, on_delete=models.PROTECT, related_name='event_campaigns', db_column='form_id')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    created_by = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = 'Feedback"."feedback_event_campaigns'


class FeedbackEventRecipient(models.Model):
    """One attendee granted token-scoped access to every open form for an event."""

    INVITE_STATUS_CHOICES = [('pending', 'Pending'), ('sent', 'Sent'), ('failed', 'Failed')]

    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name='feedback_recipients', db_column='event_id')
    attendee_name = models.CharField(max_length=255)
    attendee_email = models.EmailField(max_length=320)
    learner_id = models.CharField(max_length=100, blank=True, default='')
    attendance_source = models.CharField(max_length=80, default='spreadsheet')
    attendance_reference = models.CharField(max_length=255, blank=True, default='')
    token_hash = models.CharField(max_length=64, null=True, blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    invite_status = models.CharField(max_length=20, choices=INVITE_STATUS_CHOICES, default='pending')
    invitation_sent_at = models.DateTimeField(null=True, blank=True)
    invitation_error = models.TextField(blank=True, default='')
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = 'Feedback"."feedback_event_recipients'


class EventEmailTemplate(models.Model):
    """Reusable, staff-authored plain-text email copy for event workflows."""

    PURPOSE_CHOICES = [('event_rsvp', 'Event RSVP'), ('post_event', 'Post-event feedback')]

    name = models.CharField(max_length=255)
    purpose = models.CharField(max_length=30, choices=PURPOSE_CHOICES)
    subject = models.CharField(max_length=255)
    body = models.TextField()
    button_text = models.CharField(max_length=120)
    created_by = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = 'Feedback"."event_email_templates'


class EventEmailSetting(models.Model):
    """Per-event snapshot/override of reusable email copy."""

    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name='email_settings', db_column='event_id')
    purpose = models.CharField(max_length=30, choices=EventEmailTemplate.PURPOSE_CHOICES)
    template = models.ForeignKey(
        EventEmailTemplate, on_delete=models.SET_NULL, related_name='event_settings',
        db_column='template_id', null=True, blank=True,
    )
    subject = models.CharField(max_length=255)
    body = models.TextField()
    button_text = models.CharField(max_length=120)
    updated_by = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = 'Feedback"."event_email_settings'
        constraints = [
            models.UniqueConstraint(fields=['event', 'purpose'], name='event_email_setting_unique'),
        ]


class EventRsvpCampaign(models.Model):
    """One published RSVP form attached to one Engagement event."""

    event = models.OneToOneField(Event, on_delete=models.PROTECT, related_name='rsvp_campaign', db_column='event_id')
    form = models.ForeignKey(FeedbackForm, on_delete=models.PROTECT, related_name='rsvp_campaigns', db_column='form_id')
    status = models.CharField(max_length=20, default='open')
    created_by = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = 'Feedback"."event_rsvp_campaigns'


class EventRsvpRecipient(models.Model):
    """A learner or guest invited to declare intent to attend an event."""

    campaign = models.ForeignKey(EventRsvpCampaign, on_delete=models.CASCADE, related_name='recipients', db_column='campaign_id')
    recipient_name = models.CharField(max_length=255)
    recipient_email = models.EmailField(max_length=320)
    learner_id = models.CharField(max_length=100, blank=True, default='')
    token_hash = models.CharField(max_length=64, null=True, blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    invite_status = models.CharField(max_length=20, default='pending')
    rsvp_status = models.CharField(max_length=20, default='no_response')
    invitation_sent_at = models.DateTimeField(null=True, blank=True)
    invitation_error = models.TextField(blank=True, default='')
    responded_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = 'Feedback"."event_rsvp_recipients'


class FeedbackResponse(models.Model):
    STATUS_CHOICES = [('in_progress', 'In progress'), ('completed', 'Completed')]

    form = models.ForeignKey(FeedbackForm, on_delete=models.PROTECT, related_name='responses', db_column='form_id')
    delivery = models.ForeignKey(
        FeedbackDelivery, on_delete=models.PROTECT, related_name='responses',
        db_column='delivery_id', null=True, blank=True,
    )
    event_recipient = models.ForeignKey(
        FeedbackEventRecipient, on_delete=models.PROTECT, related_name='responses',
        db_column='event_recipient_id', null=True, blank=True,
    )
    rsvp_recipient = models.ForeignKey(
        EventRsvpRecipient, on_delete=models.PROTECT, related_name='responses',
        db_column='rsvp_recipient_id', null=True, blank=True,
    )
    review_delivery = models.ForeignKey(
        FeedbackReviewDelivery, on_delete=models.PROTECT, related_name='responses',
        db_column='review_delivery_id', null=True, blank=True,
    )
    learner_id = models.CharField(max_length=100, blank=True, default='')
    learner_name = models.CharField(max_length=255)
    programme = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='in_progress')
    started_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'Feedback"."feedback_responses'


class FeedbackAnswer(models.Model):
    response = models.ForeignKey(FeedbackResponse, on_delete=models.CASCADE, related_name='answers', db_column='response_id')
    question = models.ForeignKey(FeedbackQuestion, on_delete=models.PROTECT, related_name='answers', db_column='question_id')
    answer = models.JSONField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = 'Feedback"."feedback_answers'


class FeedbackUpload(models.Model):
    """Private image uploaded as the answer to a photo-upload question."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    response = models.ForeignKey(FeedbackResponse, on_delete=models.CASCADE, related_name='uploads', db_column='response_id')
    question = models.ForeignKey(FeedbackQuestion, on_delete=models.PROTECT, related_name='uploads', db_column='question_id')
    blob_name = models.TextField()
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100, default='image/jpeg')
    size = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'Feedback"."feedback_uploads'
