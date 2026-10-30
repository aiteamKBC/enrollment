from django.apps import AppConfig


class QuizApiConfig(AppConfig):
    name = 'quiz_api'

    def ready(self):
        # Records quizzes, questions and answers in the Audit Trail, under the
        # Curriculum workspace their tables belong to. Here rather than in
        # system_audit because the models have to be loaded before their signals
        # can be connected. Never fails a start-up: it logs and moves on.
        from system_audit.records import register_quiz_records
        register_quiz_records()
