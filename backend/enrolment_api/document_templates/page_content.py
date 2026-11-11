"""Allowlisted rich text stored as runs, never executable HTML."""
from .registry import TemplateError, check_tokens, text


def runs(raw, key):
    if not isinstance(raw, list) or len(raw) > 1000: raise TemplateError('Provide at most 1,000 text runs per paragraph/cell.')
    result = []
    for run in raw:
        if not isinstance(run, dict) or set(run) - {'text', 'bold', 'italic', 'underline', 'fontSize'}: raise TemplateError('Unsupported rich text formatting.')
        item = {'text': text(run.get('text'), 'Rich text', empty=True)}
        check_tokens(item['text'], key)
        for flag in ('bold', 'italic', 'underline'):
            if flag in run:
                if type(run[flag]) is not bool: raise TemplateError('Rich text marks must be boolean.')
                item[flag] = run[flag]
        if 'fontSize' in run:
            if type(run['fontSize']) not in (int, float) or not 7 <= run['fontSize'] <= 24: raise TemplateError('Font size must be between 7 and 24.')
            item['fontSize'] = run['fontSize']
        result.append(item)
    if sum(len(r['text']) for r in result) > 16000: raise TemplateError('A paragraph or table cell is too long.')
    return result


def layout(raw):
    if not isinstance(raw, dict) or set(raw) - {'size', 'margins', 'header', 'footer', 'pageNumbers'}: raise TemplateError('Unsupported page configuration.')
    if raw.get('size', 'a4') != 'a4': raise TemplateError('The current document workflow supports A4 pages.')
    margins = raw.get('margins', {'top': 20, 'right': 18, 'bottom': 20, 'left': 18})
    if not isinstance(margins, dict) or set(margins) != {'top', 'right', 'bottom', 'left'} or any(type(v) not in (int, float) or not 10 <= v <= 35 for v in margins.values()): raise TemplateError('Page margins must be between 10 and 35 mm.')
    if type(raw.get('pageNumbers', False)) is not bool: raise TemplateError('Page number setting must be boolean.')
    return {'size': 'a4', 'margins': margins, 'header': text(raw.get('header', ''), 'Header', empty=True, maximum=300),
            'footer': text(raw.get('footer', ''), 'Footer', empty=True, maximum=300), 'pageNumbers': raw.get('pageNumbers', False)}
