from django.db import models

class QuizPackage(models.Model):
    title = models.CharField(max_length=255)
    programme_id = models.CharField(max_length=128, blank=True, default="")
    module = models.CharField(max_length=160, blank=True)
    programme = models.CharField(max_length=160, blank=True)
    version = models.CharField(max_length=32, default="v1.0")
    questions = models.PositiveIntegerField(default=0)
    default_question_type = models.CharField(max_length=40, default="single_choice")
    assessment_type = models.CharField(max_length=40, default="quiz")
    week_id = models.CharField(max_length=128, blank=True, default="")
    status = models.CharField(max_length=20, default="draft")
    package_type = models.CharField(max_length=20, default="xml")
    uploaded_file = models.FileField(upload_to="quiz_packages/", max_length=500, blank=True, null=True)
    file_name = models.CharField(max_length=255, blank=True)
    file_size = models.PositiveIntegerField(default=0)
    schema_valid = models.BooleanField(default=True)
    validation_message = models.TextField(blank=True)
    mapped_components = models.PositiveIntegerField(default=0)
    author = models.CharField(max_length=120, blank=True, default="Curriculum Team")
    linked_courses = models.PositiveIntegerField(default=0)
    short_description = models.TextField(blank=True)
    lesson_content = models.TextField(blank=True)
    duration = models.PositiveIntegerField(default=60)
    time_unit = models.CharField(max_length=20, default="minutes")
    quiz_style = models.CharField(max_length=40, default="default")
    randomize_questions = models.BooleanField(default=True)
    randomize_answers = models.BooleanField(default=False)
    show_correct_answer = models.BooleanField(default=True)
    attempt_history = models.BooleanField(default=False)
    retake_after_pass = models.BooleanField(default=False)
    limit_attempts = models.BooleanField(default=False)
    passing_grade = models.PositiveIntegerField(default=65)
    retake_points_cut = models.PositiveIntegerField(default=5)
    published_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = '"curriculum"."quizzes"'
        ordering = ["-updated_at", "title"]

    def __str__(self):
        return self.title


class QuizQuestion(models.Model):
    quiz = models.ForeignKey(QuizPackage, on_delete=models.CASCADE, related_name="quiz_questions")
    question_text = models.TextField()
    image_url = models.TextField(blank=True, default="")
    question_type = models.CharField(max_length=40, default="multiple_choice")
    points = models.PositiveIntegerField(default=1)
    sort_order = models.PositiveIntegerField(default=0)
    explanation = models.TextField(blank=True)
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    # Nullable: rows from before the column existed have no honest value, and
    # inventing one would tell the Audit Trail they were never edited.
    updated_at = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        managed = False
        db_table = '"curriculum"."quiz_questions"'
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.question_text[:80]


class QuizAnswer(models.Model):
    question = models.ForeignKey(QuizQuestion, on_delete=models.CASCADE, related_name="answers")
    answer_text = models.TextField()
    is_correct = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    # See QuizQuestion.updated_at.
    updated_at = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        managed = False
        db_table = '"curriculum"."quiz_answers"'
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.answer_text[:80]
