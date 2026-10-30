"""Read the selected learner's existing points and available reward catalogue."""
import logging

from django.db import DatabaseError
from django.db.models import F
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from engagement_api.models import Reward
from engagement_api.services import points_summary
from login.permissions import learner_self_or_staff
from .learner_detail import SOURCE_MODELS

log = logging.getLogger(__name__)


@require_GET
@learner_self_or_staff(kwarg='pk')
def learner_rewards_summary(request, kind, pk):
    model = SOURCE_MODELS.get(kind)
    if model is None:
        return JsonResponse({'error': 'Learner not found.'}, status=404)
    try:
        if not model.all_learners.filter(pk=pk).exists():
            return JsonResponse({'error': 'Learner not found.'}, status=404)
        points = points_summary(str(pk))
        rewards = Reward.objects.filter(active=True, stock__gt=F('total_claimed')).only(
            'id', 'name', 'description', 'points', 'category', 'stock', 'total_claimed',
        ).order_by('points', 'name')[:3]
        payload = {'points': points, 'rewards': [
            {'id': str(reward.pk), 'name': reward.name, 'description': reward.description,
             'points': reward.points, 'category': reward.category,
             'remaining': max(0, reward.stock - reward.total_claimed)} for reward in rewards
        ]}
    except DatabaseError:
        log.warning('Rewards summary unavailable for learner %s', pk, exc_info=True)
        return JsonResponse({'error': 'Could not load your rewards and points. Please try again.'}, status=503)
    response = JsonResponse(payload)
    response['Cache-Control'] = 'private, no-store'
    return response
