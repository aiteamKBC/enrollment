"""Read-time compatibility for published layouts; never rewrites layout history."""
SLUG = 'programme-suitability'
LABEL = 'Programme Suitability & Modules'


def with_suitability_step(layout):
    if not isinstance(layout, dict) or not isinstance(layout.get('steps'), list):
        return layout
    steps = list(layout['steps'])
    existing = next((s for s in steps if s.get('slug') == SLUG), None)
    if existing is None:
        step = {'slug': SLUG, 'label': LABEL, 'builtin': True, 'hidden': False, 'items': []}
        cv = next((i for i, s in enumerate(steps) if s.get('slug') == 'cv-job'), None)
        ilr = next((i for i, s in enumerate(steps) if s.get('slug') == 'ilr'), None)
        index = cv if cv is not None else ilr + 1 if ilr is not None else len(steps)
        steps.insert(index, step)
    elif (layout.get('programmeSuitability') or {}).get('enabled') and existing.get('hidden'):
        steps[steps.index(existing)] = {**existing, 'hidden': False}
    return {**layout, 'steps': steps}
