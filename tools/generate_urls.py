"""Generate a fixed route allowlist from the original URL declarations."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent / "backend"
DEST = ROOT / "backend" / "enrollment_service"
DEST.mkdir(exist_ok=True)


def wanted(app, route):
    if app == "login":
        return route in {
            "health/", "login/", "logout/", "me/", "me/signature/", "change-password/",
            "forgot-password/", "reset/", "reset-password/", "invitation/", "accept-invitation/",
            "accounts/invite/", "accounts/invitation-link/", "request-access/",
            "microsoft/start/", "microsoft/callback/", "inclusion/authorize/", "safeguarding/authorize/",
            "public/coaches/<slug:slug>/", "admin/coach-directory/", "admin/coach-directory/<int:pk>/", "public/lms-introduction/",
        }
    if app == "learner_api":
        prefixes = (
            "enrolment-users/", "commercial-users/", "staff-users/", "organisations/",
            "employers/", "apprenticeship-agreement/", "ilr-document/", "training-plan-document/",
            "written-agreement/", "first-login-details/", "first-session-bookings/",
            "learning-plan/", "curriculum/", "attendance-mode/", "module-shift/",
            "reviews/", "learner-detail/",
        )
        if route.startswith(prefixes):
            return True
        if route.startswith("employer-portal/"):
            return route.endswith('/documents/') or '/documents/<str:document_kind>/' in route or route.endswith('/reviews/<path:event_key>/')
        if route.startswith("calendar/"):
            return "/artifacts" not in route and "/events/" not in route
        return route in {"learners/<int:pk>/coach/", "detail/<str:kind>/<int:pk>/"} or "enrolment-review" in route
    if app == "curriculum_api":
        return route == "curriculum/free-programmes/<str:programme_id>/modules/"
    if app == "coach_api":
        return route in {"csrf", "coaches", "coaches/calendar"}
    return False


for app in ("login", "learner_api", "curriculum_api", "coach_api"):
    tree = ast.parse((SOURCE / app / "urls.py").read_text(encoding="utf-8-sig"))
    calls = []
    for statement in tree.body:
        if not isinstance(statement, ast.Assign) or not any(isinstance(t, ast.Name) and t.id == "urlpatterns" for t in statement.targets):
            continue
        for node in statement.value.elts:
            if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) and wanted(app, node.args[0].value):
                calls.append(node)
    names = {n.id for call in calls for n in ast.walk(call) if isinstance(n, ast.Name)} - {"path"}
    imports = []
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        aliases = [a for a in node.names if (a.asname or a.name) in names]
        if aliases:
            module = app + ("." + node.module if node.module else "") if node.level else node.module
            imports.append(ast.unparse(ast.ImportFrom(module=module, names=aliases, level=0)))
    output = '"""Enrolment-only API surface. Study/delivery endpoints remain in LMS."""\nfrom django.urls import path\n'
    output += "\n".join(imports) + "\n\nurlpatterns = [\n"
    if app == 'learner_api':
        output = output.replace(', module_shift,', ',')
        output = output.replace('\n\nurlpatterns', '\nfrom enrollment_service import study_boundary as module_shift\n\nurlpatterns')
    output += "".join("    " + ast.unparse(call) + ",\n" for call in calls) + "]\n"
    (DEST / (app + "_urls.py")).write_text(output, encoding="utf-8")
    print(app, len(calls), "routes")
