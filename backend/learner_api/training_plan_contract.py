"""Read the learning-plan table in an existing contract; never alter the PDF."""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from hashlib import sha256
import json
import re


def selected_contract(candidates):
    """Use an available duplicate of the same document version, never an older plan."""
    if not candidates:
        return None
    selected = candidates[0]
    if selected.get('azure_path') or not selected.get('date'):
        return selected

    def name(row):
        # An auditor's display-name edit does not change the document version.
        original = row.get('original_name') or row.get('document_name') or ''
        return re.sub(r'\.pdf$', '', str(original).strip(), flags=re.I).casefold()

    # Review-import placeholders can repeat an existing document but omit its
    # Azure path. Their timestamps differ only by subsecond export precision.
    same = [row for row in candidates[1:] if row.get('azure_path') and row.get('date')
            and row['date'].replace(microsecond=0) == selected['date'].replace(microsecond=0)
            and name(row) == name(selected)]
    return same[0] if len(same) == 1 else selected


def parse_contract(data, expected_total=None):
    import pymupdf as fitz

    rows = []
    active = False
    printed_total = None
    total_continues = False
    method_x = provider_x = hours_x = None
    with fitz.open(stream=data, filetype='pdf') as document:
        for page in document:
            textpage = page.get_textpage()
            words = sorted(textpage.extractWORDS(), key=lambda word: (round(word[1], 1), word[0]))
            text = textpage.extractText()
            header = next((w for w in words if w[4] == 'Activity/Unit'), None)
            if header and 'Planned' in text:
                method = next((w for w in words if w[4] == 'Method' and abs(w[1] - header[1]) < 20), None)
                provider = next((w for w in words if w[4] == 'Delivery' and abs(w[1] - header[1]) < 20), None)
                hours = next((w for w in words if w[4] == 'OTJ' and abs(w[1] - header[1]) < 30), None)
                if hours is None:
                    # A page break can separate "Planned" from "OTJ (hr)".
                    # Carry the rightmost Planned column to continuation pages.
                    hours = max((w for w in words if w[4] == 'Planned' and abs(w[1] - header[1]) < 20),
                                key=lambda w: w[0], default=None)
                if not all((method, provider, hours)):
                    return None
                method_x, provider_x, hours_x = method[0], provider[0], hours[0] - 2
                active = True
            if not active:
                continue
            # The learning-plan total can share a page with the following
            # review table. Read everything above that section before stopping.
            stop_at = min((rect.y0 for label in ('Progress Reviews', 'Review type', 'Off-The-Job Training Hours')
                           for rect in page.search_for(label, textpage=textpage)
                           if not header or rect.y0 > header[3]), default=None)
            if stop_at is not None:
                words = [word for word in words if word[1] < stop_at]
            total_markers = [w for w in words if w[4] == 'Total' and w[0] > method_x]
            if total_continues and stop_at is not None and not total_markers:
                # A PDF page break can separate the Total label from its value.
                # The final hours-column number before Reviews is that total;
                # preceding numbers may finish the previous activity row.
                continuation = [w for w in words if w[2] >= hours_x
                                and re.fullmatch(r'\d+(?:\.\d+)?', w[4])]
                if continuation:
                    printed_total = Decimal(continuation[-1][4])
                    total_continues = False
            for marker in total_markers:
                # Edited PDFs may shift the value by one text line or join it
                # to "(hr)". Use the right edge for centred numbers; the EM
                # column stays to the left of the OTJ header.
                totals = [match.group(1) for w in words
                          if abs(w[1] - marker[1]) <= marker[3] - marker[1] and w[2] >= hours_x
                          and (match := re.fullmatch(r'(?:\(hr\))?(\d+(?:\.\d+)?)', w[4]))]
                if len(totals) == 1:
                    printed_total = Decimal(totals[0])
                    total_continues = False
                elif not totals:
                    total_continues = True
            dates = []
            for word in words:
                # Some exported PDFs join the provider's final word and the
                # date ("Kent15/10/2025") into a single text token.
                match = re.search(r'(?<!\d)(\d{2}/\d{2}/\d{4})$', word[4])
                if match and word[2] > provider_x and word[0] < hours_x and (not header or word[1] > header[3]):
                    dates.append((*word[:4], match[1], *word[5:]))
            for index, marker in enumerate(dates):
                try:
                    date = datetime.strptime(marker[4], '%d/%m/%Y').date()
                except ValueError:
                    return None
                top = marker[1] - 1
                end = min([w[1] - 1 for w in total_markers if w[1] > marker[3]] +
                          [dates[index + 1][1] - 1 if index + 1 < len(dates) else (stop_at or page.rect.height - 60)])
                title = ' '.join(textpage.extractTextbox(fitz.Rect(0, top, method_x - 3, end)).split())
                method = ' '.join(textpage.extractTextbox(fitz.Rect(method_x - 1, top, provider_x - 1, end)).split())
                values = [w[4] for w in words if abs(w[1] - marker[1]) < 3 and w[2] >= hours_x
                          and re.fullmatch(r'\d+(?:\.\d+)?', w[4])]
                if len(values) > 1 or not title:
                    return None
                rows.append({'date': date.isoformat(), 'title': title, 'method': method,
                             'hours': float(values[0]) if values else 0.0})
            if stop_at is not None:
                break
    if not rows:
        return None
    total = sum(Decimal(str(row['hours'])) for row in rows)
    try:
        if printed_total is None or abs(total - printed_total) > Decimal('0.01'):
            return None
        # The PDF is the selected contract itself. Its printed total is
        # authoritative; the older DB extraction can belong to a stale plan.
    except InvalidOperation:
        return None
    return group_contract_rows(rows)


def group_contract_rows(rows):
    months = defaultdict(lambda: {'planned': 0.0, 'topics': [], 'activities': []})
    for row in rows:
        month = months[row['date'][:7]]
        month['planned'] = round(month['planned'] + row['hours'], 2)
        month['activities'].append(row)
    for month in months.values():
        teaching = [row for row in month['activities'] if row['hours'] > 0]
        # Assignment units usually name the monthly topic; attendance/LMS rows
        # often repeat it three times, while certificates carry zero hours.
        candidates = [row for row in teaching if 'Assignment' in row['method']] or teaching
        for row in candidates:
            topic = re.sub(r'\s*[-(–—]?\s*(?:Coach[ -]Led Assignment|LMS Activity|Attendance|Assignment|Additional Job Activity)\s*\)?$', '', row['title'], flags=re.I).strip(' -–—')
            if topic and not re.fullmatch(r'(?:LMS[- ]?Activity|Additional job activities|(?:January|February|March|April|May|June|July|August|September|October|November|December)[ -]*)', topic, re.I):
                if topic not in month['topics']:
                    month['topics'].append(topic)
    return dict(months)


def verified_planned_hours(data, verified_extract=None):
    """Return only a learning-table total verified against this exact PDF.

    ILR hours and published minimums describe different figures. Neither is a
    fallback for a missing or unreadable learning-plan table.
    """
    months = parse_contract(data) or read_verified_extract(data, verified_extract)
    if months is None:
        return None
    total = sum((Decimal(str(row['hours'])) for month in months.values()
                 for row in month['activities']), Decimal(0))
    return total.quantize(Decimal('0.01'))


def contract_extract_metadata(raw):
    """Read the optional reviewed extract from the existing document metadata."""
    try:
        record = json.loads(raw) if isinstance(raw, str) else raw
        extract = record.get('_verified_training_plan') if isinstance(record, dict) else None
        return json.dumps(extract, sort_keys=True) if isinstance(extract, dict) else None
    except (ValueError, TypeError):
        return None


def read_verified_extract(data, metadata):
    """Only reuse a reviewed table for the exact PDF bytes that were reviewed.

    Scans and reflowed PDF uploads cannot supply reliable column coordinates.
    Their reviewed rows can live in the existing contract's raw metadata, with
    no OCR calls or database writes during a learner request.
    """
    try:
        record = json.loads(metadata) if metadata else None
        if not isinstance(record, dict) or record.get('version') != 1 or record.get('pdf_sha256') != sha256(data).hexdigest():
            return None
        original = record.get('activities')
        if not isinstance(original, list) or not 0 < len(original) <= 10000:
            return None
        printed_total = Decimal(str(record['printed_total']))
        if not printed_total.is_finite() or printed_total < 0:
            return None
        rows, total = [], Decimal(0)
        for row in original:
            date = datetime.strptime(row['date'], '%Y-%m-%d').date().isoformat()
            title, method = row['title'], row['method']
            hours = Decimal(str(row['hours']))
            if date != row['date'] or not isinstance(title, str) or not title.strip() or not isinstance(method, str) or not hours.is_finite() or hours < 0:
                return None
            rows.append({'date': date, 'title': title.strip(), 'method': method.strip(), 'hours': float(hours)})
            total += hours
        if abs(total - printed_total) > Decimal('0.01'):
            return None
        return group_contract_rows(rows)
    except (ValueError, TypeError, KeyError, InvalidOperation, OverflowError):
        return None


@lru_cache(maxsize=512)
def read_contract(azure_path, expected_total, version, verified_extract=None):
    # version (the selected contract's version/date) invalidates cached extracts.
    # The storage client is already configured for the audited contract viewer.
    from audit_api.views import _azure_service_client, _parse_contract_azure_path
    from azure.core.exceptions import ResourceNotFoundError

    container, blob = _parse_contract_azure_path(azure_path)
    # Avoid the shared viewer helper's extra HEAD request and long SDK retry
    # delays. A slow/unreachable document must not occupy a worker indefinitely.
    options = {'connection_timeout': 5, 'read_timeout': 10, 'retry_total': 0}
    with _azure_service_client() as service:
        client = service.get_blob_client(container=container, blob=blob)
        try:
            properties = client.get_blob_properties(**options)
        except ResourceNotFoundError as error:
            if container.endswith('s') or error.error_code != 'ContainerNotFound':
                raise
            client = service.get_blob_client(container=f'{container}s', blob=blob)
            properties = client.get_blob_properties(**options)
        if properties.size > 30 * 1024 * 1024:
            return None
        data = client.download_blob(max_concurrency=1, **options).readall()
    return parse_contract(data, expected_total) or read_verified_extract(data, verified_extract)
