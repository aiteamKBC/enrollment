"""Standard Django CSRF bootstrap for the Coach frontend."""

from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET


@ensure_csrf_cookie
@require_GET
def coach_csrf_token(request):
    """Issue Django's masked CSRF token without granting authentication."""

    return JsonResponse({"csrfToken": get_token(request)})
