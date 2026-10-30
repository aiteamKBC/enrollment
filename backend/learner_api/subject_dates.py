"""Activity scheduling dates, separate from import and completion timestamps."""

from datetime import date, datetime, timedelta
import html
import re
import unicodedata

MONTHS = {name: number for number, names in enumerate((
    ('jan', 'january'), ('feb', 'february'), ('mar', 'march'), ('apr', 'april'),
    ('may',), ('jun', 'june'), ('jul', 'july'), ('aug', 'august'),
    ('sep', 'sept', 'september'), ('oct', 'october'), ('nov', 'november'), ('dec', 'december'),
), 1) for name in names}
MONTH = '(?:' + '|'.join(sorted(MONTHS, key=len, reverse=True)) + ')'
# Owner-designated sections outside the calendar, shared by every learner on the
# course. Source IDs keep this independent of names, upload dates and progress.
SECTION_PLACEMENTS = {
    (125600, '2043'): 'extra_activity',  # Commercial Intelligence, Lec13
}
YMD = re.compile(r'(?<!\d)(\d{4})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*(\d{1,2})(?!\d)')
DMY = re.compile(r'(?<!\d)(\d{1,2})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*(\d{4}|\d{2})(?!\d)')
OVERLAPPING_DMY = re.compile(r'(?=(' + DMY.pattern + r'))')
NAMED_DMY = re.compile(r'(?<!\d)(\d{1,2})\s*(?:st|nd|rd|th)?[\s,./-]+(' + MONTH + r')[\s,./-]+(\d{4}|\d{2})(?!\d)', re.I)
NAMED_MDY = re.compile(r'\b(' + MONTH + r')[\s,./-]+(\d{1,2})\s*(?:st|nd|rd|th)?[\s,./-]+(\d{4}|\d{2})(?!\d)', re.I)
PARTIAL = re.compile(r'(?<!\d)\d{1,2}\s*(?:st|nd|rd|th)?[\s,./-]+' + MONTH + r'\b|\b' + MONTH + r'[\s,./-]+\d{1,2}(?!\d)', re.I)


def as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or '')[:10])
    except ValueError:
        return None


def apply_section_placement(item, group_id, section_id):
    placement = SECTION_PLACEMENTS.get((group_id, str(section_id)))
    if placement:
        item.update(date=None, month='undated', week_start=None, week_end=None,
                    date_source=placement, date_needs_review=False)
    return item


def is_introduction_section(title):
    text = unicodedata.normalize('NFKC', html.unescape(str(title or '')))
    text = ''.join(char for char in text if unicodedata.category(char) != 'Cf')
    text = ' '.join(re.sub(r'[:.\-–—]+', ' ', text).casefold().split())
    # Match a whole section heading, never "Introduction to ..." in a lesson
    # title. Dated lecture headings retain their calendar placement.
    return text in {'introduction', 'intro', 'course introduction', 'module introduction',
                    'introduction session', 'first day introduction'}


def activity_schedule(title, stored_date=None, original_created_at=None, *, section_title=None, section_source='section_title'):
    text = unicodedata.normalize('NFKC', html.unescape(str(title or '')))
    text = text.translate(str.maketrans({'–': '-', '—': '-', '−': '-', '\u200b': ''}))
    text = ''.join(str(unicodedata.digit(char)) if char.isdecimal() else char for char in text)
    candidates = []
    for pattern, order in ((YMD, 'ymd'), (DMY, 'dmy'), (NAMED_DMY, 'named_dmy'), (NAMED_MDY, 'named_mdy')):
        matches = (pattern.match(text, found.start()) for found in OVERLAPPING_DMY.finditer(text)) if pattern is DMY else pattern.finditer(text)
        for match in matches:
            parts = match.groups()
            if order == 'ymd':
                year, month, day = map(int, parts)
            elif order == 'dmy':
                day, month, year = map(int, parts)
                # Legacy lecture titles mix UK dates with unambiguous US dates.
                # Keep day-first for ambiguous values such as 8/9/2026.
                if month > 12 and 1 <= day <= 12:
                    day, month = month, day
            elif order == 'named_dmy':
                day, month, year = int(parts[0]), MONTHS[parts[1].lower()], int(parts[2])
            else:
                month, day, year = MONTHS[parts[0].lower()], int(parts[1]), int(parts[2])
            if year < 100:
                year += 2000
            try:
                parsed = date(year, month, day) if 2020 <= year <= 2035 else None
            except ValueError:
                parsed = None
            candidates.append((match.start(), match.end(), parsed))
    # A group/part number can form an invalid date fragment that overlaps the
    # real date: "Group 2 - 20/02/26". Keep the complete valid date instead of
    # consuming its beginning. Distinct invalid/conflicting dates still require
    # review; they must never fall back silently to a cloned creation date.
    selected = []
    for candidate in sorted(candidates, key=lambda item: (item[2] is None, -(item[1] - item[0]), item[0])):
        start, end, _ = candidate
        if not any(start < other_end and other_start < end for other_start, other_end, _ in selected):
            selected.append(candidate)
    candidates = selected
    parsed_dates = {value for _, _, value in candidates}
    if candidates:
        chosen = next(iter(parsed_dates)) if len(parsed_dates) == 1 else None
        source = 'title' if chosen else 'title_needs_review'
    elif PARTIAL.search(text):
        # A cloned upload date cannot safely supply the year missing in a title.
        chosen, source = None, 'partial_title_needs_review'
    elif is_introduction_section(section_title):
        # Applies equally to live legacy sections, retained exports and newly
        # created Module Builder weeks, independently of course/learner IDs.
        chosen, source = None, 'introduction'
    else:
        # Lesson titles often contain no date. The parent lecture/section title
        # is a scheduling source; a cloned/uploaded timestamp is not one.
        if section_title:
            section = activity_schedule(section_title)
            if section['date_source'] != 'undated':
                return {**section,
                        'source_date': as_date(stored_date).isoformat() if as_date(stored_date) else None,
                        'date_source': section_source + ('_needs_review' if section['date_needs_review'] else '')}
        chosen = as_date(original_created_at) or as_date(stored_date)
        source = 'original_created_at' if as_date(original_created_at) else ('source_date' if chosen else 'undated')
    monday = chosen - timedelta(days=chosen.weekday()) if chosen else None
    return {
        'date': chosen.isoformat() if chosen else None,
        'source_date': as_date(stored_date).isoformat() if as_date(stored_date) else None,
        'date_source': source,
        'date_needs_review': source.endswith('needs_review') or source in {'original_created_at', 'source_date'},
        'month': chosen.strftime('%Y-%m') if chosen else 'undated',
        'week_start': monday.isoformat() if monday else None,
        'week_end': (monday + timedelta(days=6)).isoformat() if monday else None,
    }
