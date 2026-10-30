"""Export tracked dependencies without modifying or checking out the LMS repository.

Only source files are exported. Credentials, uploads, database dumps and runtime
directories are never inputs. Run once before editing the standalone copy.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

DEST = Path(__file__).resolve().parents[1]
SOURCE = DEST.parent
TRACKED = set(subprocess.check_output(["git", "ls-files"], cwd=SOURCE, text=True).splitlines())
MANIFEST = json.loads((DEST / "source-manifest.json").read_text(encoding="utf-8"))["files"] if (DEST / "source-manifest.json").exists() else {}


def copy(relative):
    if relative not in TRACKED:
        raise RuntimeError(f"Untracked input refused: {relative}")
    source, target = SOURCE / relative, DEST / relative
    if not source.is_file():
        raise RuntimeError(f"Missing tracked input: {relative}")
    MANIFEST.setdefault(relative, hashlib.sha256(source.read_bytes()).hexdigest())
    if target.exists():
        return  # Existing exports may have intentional service-specific edits.
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    MANIFEST[relative] = hashlib.sha256(source.read_bytes()).hexdigest()


def export_backend():
    modules = {}
    for rel in TRACKED:
        if rel.startswith("backend/") and rel.endswith(".py"):
            parts = list(Path(rel).relative_to("backend").with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            modules[".".join(parts)] = rel
    apps = ["enrolment_api", "learner_api", "login", "coach_api", "curriculum_api",
            "audit_api", "manual_audit_api", "engagement_api", "quiz_api", "chat",
            "read_models", "old_otjh", "system_audit", "progress_reviews_api", "knowledge_base", "mobile_comms"]
    seeds = ["enrolment_api.urls", "learner_api.views", "learner_api.learner_import",
             "learner_api.employers", "learner_api.apprenticeship_agreement",
             "learner_api.ilr_document", "learner_api.training_plan_document",
             "learner_api.written_agreement", "learner_api.curriculum",
             "learner_api.review_form", "learner_api.first_login_details",
             "learner_api.first_session_bookings", "learner_api.calendar",
             "learner_api.coach_availability", "learner_api.learning_plan",
             "learner_api.attendance_mode", "learner_api.module_shift",
             "learner_api.learner_detail", "learner_api.employer_portal",
             "login.views", "login.middleware", "login.api_gate", "login.saved_signature",
             "login.access_requests", "login.coach_directory", "login.inclusion_sso",
             "login.safeguarding_sso", "config.settings", "config.batch",
             "config.observability", "config.performance", "config.cors", "config.db_release",
             "learner_api.routers", "curriculum_api.middleware", "login.test_runner",
             "curriculum_api.management.commands.sync_teams_meeting_artifacts",
             "curriculum_api.management.commands.process_session_results"]
    for app in apps:
        seeds.extend([app, app + ".apps", app + ".models"])
    for generated in (DEST / 'backend/enrollment_service').glob('*_urls.py'):
        for node in ast.walk(ast.parse(generated.read_text(encoding='utf-8'))):
            if isinstance(node, ast.ImportFrom) and node.module:
                seeds.append(node.module)
    # Existing regression suites, unchanged, including the expanded booking gate.
    for key in modules:
        name = key.rsplit(".", 1)[-1]
        if key.startswith("enrolment_api.") and (name.startswith("test") or ".management." in key):
            seeds.append(key)
        if key.startswith(("login.test", "coach_api.test", "curriculum_api.test", "learner_api.test")):
            if name in {"tests", "tests_unit", "tests_calendar_race", "tests_errors", "tests_security",
                        "tests_booking_calendar", "tests_first_session", "tests_cohort_schedule",
                        "tests_enrolment_schema", "tests_enrolment_create_once", "tests_first_login_details",
                        "tests_learning_plan", "tests_learner_progression", "tests_api_gate",
                        "tests_saved_signature", "tests_attendance_lectures", "tests_dashboard_metrics"}:
                seeds.append(key)
    queue, seen = list(seeds), set()
    while queue:
        key = queue.pop()
        if key in seen or key not in modules:
            continue
        seen.add(key)
        rel = modules[key]
        tree = ast.parse((SOURCE / rel).read_text(encoding="utf-8-sig"))
        package = key if rel.endswith("/__init__.py") else key.rpartition(".")[0]
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = importlib.util.resolve_name("." * node.level + (node.module or ""), package) if node.level else node.module or ""
                targets = [base] + [base + "." + a.name for a in node.names]
            for target in targets:
                if target in modules:
                    queue.append(target)
                parts = target.split(".")
                queue.extend(".".join(parts[:i]) for i in range(1, len(parts)))
    for key in sorted(seen):
        copy(modules[key])
    for rel in sorted(TRACKED):
        if rel in {"backend/manage.py", "backend/requirements.txt"}:
            copy(rel)
        elif rel.startswith("backend/") and any(part in rel for part in ("/templates/", "/assets/")):
            if rel.endswith((".html", ".png", ".svg", ".jpg", ".css", ".ttf")):
                copy(rel)
        elif rel == 'backend/old_otjh/data/coach_bookings.json':
            copy(rel)
    return len(seen)


if __name__ == "__main__":
    count = export_backend()
    (DEST / "source-manifest.json").write_text(json.dumps({
        "source_repository": "https://github.com/aiteamKBC/LMS_v2",
        "source_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=SOURCE, text=True).strip(),
        "files": MANIFEST,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"Exported {count} Python modules and required assets. Original files unchanged.")
