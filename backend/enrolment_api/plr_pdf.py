"""Position-aware LRS text PDF reader. No OCR, network calls or document retention.

Only horizontally written text participates; table columns are located from their
headers and rows from reference/type cells, never from arbitrary text lines.
Uncertain layouts and partial reports fail closed before any persistence.
"""
import datetime as dt
import re
from collections import defaultdict

MAX_BYTES = 8 * 1024 * 1024
MAX_PAGES = 25
MAX_WORDS = 100000
MAX_RECORDS = 2000
ULN_ERROR = 'Unable to extract a valid ULN from this PDF. Please upload the original Personal Learning Record report.'


class PlrError(ValueError):
    def __init__(self, message, code='invalid_pdf', status=400):
        super().__init__(message)
        self.code, self.status = code, status


def date_value(value):
    value = re.sub(r'\s+', ' ', str(value or '')).strip()
    if not value:
        return None
    for fmt in ('%d %b %Y', '%d %B %Y', '%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
        try:
            return dt.datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    raise PlrError('A date in the report could not be read reliably. No records were saved.')


def _lines(words):
    lines = []
    for word in sorted(words, key=lambda w: (w[1], w[0])):
        line = next((line for line in reversed(lines[-4:]) if abs(line[0][1] - word[1]) < 2.5), None)
        if line is None:
            lines.append([word])
        else:
            line.append(word)
    return [sorted(line, key=lambda w: w[0]) for line in lines]


def _text(line):
    return ' '.join(w[4] for w in line)


def _columns(lines, section):
    # First-line anchors are unambiguous even when headers wrap onto two lines.
    anchors = [('provider', 'Provider'), ('awardingBody', 'Awarding'), ('qualificationType', 'Type')]
    if section == 'Learning Events':
        anchors += [('referenceNumber', 'Reference'), ('subject', 'Aim'), ('startDate', 'Start'), ('endDate', 'End'), ('grade', 'Grade'), ('completionStatus', 'Completion'), ('outcomeStatus', 'Outcome'), ('awardDate', 'Award')]
    else:
        anchors += [('level', 'Qualification'), ('subject', 'Title'), ('referenceNumber', 'Reference'), ('awardDate', 'Achieved'), ('grade', 'Achieved'), ('language', 'Language')]
    first = lines[0]
    columns, previous = [], -1
    for key, word in anchors:
        found = next((w for w in first if w[4] == word and w[0] > previous + 2), None)
        if found is None:
            raise PlrError('The PLR table headings could not be located reliably. No records were saved.')
        columns.append((key, found[0] - 2))
        previous = found[0]
    # Optional explicit columns; never infer levels or credits from titles/grades.
    for w in first:
        if w[4] == 'Credits' or (w[4] == 'Level' and section == 'Learning Events'):
            columns.append(('credits' if w[4] == 'Credits' else 'level', w[0] - 2))
    return sorted(columns, key=lambda c: c[1])


def parse_pdf(data):
    import pymupdf
    if not data or len(data) > MAX_BYTES or not data.startswith(b'%PDF-'):
        raise PlrError('Select a valid PDF no larger than 8 MB.')
    try:
        document = pymupdf.open(stream=data, filetype='pdf')
    except Exception:
        raise PlrError('The PDF is damaged or unreadable. Please upload the original text PDF.') from None
    with document:
        if document.needs_pass or not 1 <= len(document) <= MAX_PAGES:
            raise PlrError('Use an unencrypted PLR PDF containing no more than 25 pages.')
        identities, records, expected = [], [], {}
        headed_sections, empty_sections = set(), set()
        section, columns, current = None, None, None
        last_data_bottom = None
        word_count = 0
        for page in document:
            horizontal = [line['bbox'] for block in page.get_text('dict', flags=pymupdf.TEXTFLAGS_TEXT)['blocks'] if 'lines' in block
                          for line in block['lines'] if abs(line.get('dir', (1, 0))[1]) < .03]
            words = [w for w in page.get_text('words') if any(r[0] - 1 <= w[0] and w[2] <= r[2] + 1 and r[1] - 1 <= w[1] and w[3] <= r[3] + 1 for r in horizontal)]
            word_count += len(words)
            if word_count > MAX_WORDS:
                raise PlrError('This PDF exceeds the text processing limit.')
            lines = _lines(words)
            title_index = next((i for i, line in enumerate(lines) if 'Personal Learning Record' in _text(line)), None)
            section_index = next((i for i, line in enumerate(lines) if re.match(r'^(Learning Events|Achievements)(?:\s*\(|$)', _text(line))
                                  or (_text(line).startswith('Provider') and 'Awarding' in _text(line))), len(lines))
            if title_index is not None:
                title = lines[title_index]
                header = [[w for w in line if w[0] >= title[0][0] - 25] for line in lines[title_index + 1:section_index]]
                header = [line for line in header if line]
                name, dob, candidates = '', None, []
                for line in header:
                    text = _text(line)
                    try:
                        parsed_date = date_value(re.sub(r'^(Date of birth|DOB)\s*:?\s*', '', text, flags=re.I))
                    except PlrError:
                        parsed_date = None
                    if parsed_date:
                        dob = parsed_date
                    elif re.fullmatch(r'(?:ULN\s*:?\s*|Unique Learner Number\s*:?\s*)?[0-9]{10}', text, re.I) and (dob or re.match(r'ULN|Unique Learner Number', text, re.I)):
                        candidates.append(re.search(r'\d{10}', text).group())
                    elif not name and not re.search(r'\d|ULN|Learner Number', text, re.I):
                        name = re.sub(r'^(Learner name|Name)\s*:?\s*', '', text, flags=re.I)
                if not candidates and not name and dob is None and identities:
                    # A continuation page may repeat only the report title.
                    pass
                else:
                    if len(set(candidates)) != 1:
                        raise PlrError(ULN_ERROR, 'invalid_uln')
                    identities.append({'uln': candidates[0], 'name': name, 'dob': dob})
            i = section_index if title_index is not None else 0
            while i < len(lines):
                line, text = lines[i], _text(lines[i])
                match = re.fullmatch(r'(Learning Events|Achievements)(?:\s*\(\s*(\d+)\s*(?:/\s*(\d+)\s*)?\))?', text)
                if match:
                    previous_section = section
                    section = match[1]
                    displayed = int(match[2]) if match[2] is not None else None
                    total = int(match[3]) if match[3] is not None else displayed
                    if displayed != total:
                        raise PlrError('The report displays only part of the available records. Export a complete PLR report.')
                    if section in expected and expected[section] is not None and total is not None and expected[section] != total:
                        raise PlrError('The repeated section counts disagree. No records were saved.')
                    expected[section] = total if total is not None else expected.get(section)
                    columns = None
                    if section != previous_section:
                        current = None
                        last_data_bottom = None
                    i += 1
                    continue
                if section and text.startswith('Provider') and 'Awarding' in text:
                    columns = _columns(lines[i:i+2], section)
                    headed_sections.add(section)
                    bottom = max(w[3] for w in line)
                    i += 1
                    # Wrapped heading labels belong to the header, not a record.
                    if i < len(lines) and lines[i][0][1] < bottom + 8 and re.search(r'Number|Status|Grade|Body|Assessment|Level', _text(lines[i])):
                        i += 1
                    continue
                if re.match(r'^No records', text, re.I) and section:
                    empty_sections.add(section)
                if not columns or re.match(r'^(No records|Created on|\(PROTECT|Page \d|SYNTHETIC TEST FIXTURE\b|Learning Records Service$)', text, re.I):
                    i += 1
                    continue
                cells = defaultdict(list)
                for w in line:
                    key = next((key for key, x in reversed(columns) if w[0] >= x), None)
                    if key:
                        cells[key].append(w[4])
                cells = {key: ' '.join(value) for key, value in cells.items()}
                # A wrapped type/reference cell on its own is a continuation,
                # not a second qualification. Counts still validate boundaries.
                gap = line[0][1] - last_data_bottom if last_data_bottom is not None else float('inf')
                anchor = cells.get('referenceNumber') or cells.get('qualificationType') or (cells.get('provider') and cells.get('subject'))
                marker = anchor and (current is None or gap > 4
                                     or (gap < 0 and cells.get('referenceNumber')))
                if current and cells.get('referenceNumber') and 0 <= gap <= 4:
                    raise PlrError('A table row boundary could not be verified reliably.')
                if marker:
                    current = {'sourceSection': section, **cells}
                    records.append(current)
                elif current is not None:
                    if gap > 4:
                        raise PlrError('A table row boundary could not be verified reliably.')
                    for key, value in cells.items():
                        current[key] = (current.get(key, '') + ' ' + value).strip()
                elif cells:
                    raise PlrError('A table row could not be reconstructed reliably. No records were saved.')
                last_data_bottom = max(w[3] for w in line)
                if len(records) > MAX_RECORDS:
                    raise PlrError('This report exceeds the 2,000 record limit.')
                i += 1
        if not identities or len({x['uln'] for x in identities}) != 1:
            raise PlrError(ULN_ERROR, 'invalid_uln')
        if any(x['name'] != identities[0]['name'] or x['dob'] != identities[0]['dob'] for x in identities):
            raise PlrError('The learner identification headers disagree. No records were saved.')
        if not expected:
            raise PlrError('No supported Learning Events or Achievements section was found.')
        for section, count in expected.items():
            actual = sum(r['sourceSection'] == section for r in records)
            if count is not None and actual != count:
                raise PlrError(f'The {section} record count does not match the report. No records were saved.')
            if count is None and section not in headed_sections and section not in empty_sections:
                raise PlrError('A report section could not be read reliably.')
        for row in records:
            for field in ('startDate', 'endDate', 'awardDate'):
                row[field] = date_value(row.get(field))
            if row.get('credits') and not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', row['credits']):
                raise PlrError('A credits value could not be read reliably.')
        return {**identities[0], 'records': records, 'reportCounts': expected}


def parse_bounded(data):
    """Hard time limit; PDF bytes stay in process pipes, never on disk or logs."""
    import json
    import subprocess
    import sys
    from pathlib import Path
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve())], input=data,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20, check=False)
        output = json.loads(result.stdout)
        if not isinstance(output, dict):
            raise ValueError('Invalid worker response')
    except (subprocess.TimeoutExpired, ValueError, OSError):
        raise PlrError('The PDF could not be processed within the safety limits. No records were saved.') from None
    if 'error' in output:
        raise PlrError(output['error'], output.get('code', 'invalid_pdf'))
    return output


if __name__ == '__main__':
    import json
    import sys
    try:
        # Linux application workers additionally cap the parser's address space.
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
        except ImportError:
            pass
        result = parse_pdf(sys.stdin.buffer.read(MAX_BYTES + 1))
    except PlrError as exc:
        result = {'error': str(exc), 'code': exc.code}
    except Exception:
        result = {'error': 'The PDF is unreadable or uses an unsupported layout.'}
    sys.stdout.write(json.dumps(result))
