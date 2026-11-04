"""Strict UTF-8 complete-snapshot CSV import and reversible Excel-safe export."""
import csv
import io
import re

from .catalogue import CatalogueError, MAX_RECORDS, PROGRAMMES, validate

MAX_BYTES = 1024 * 1024
FIXED = ['ksb_code', 'ksb_type', 'description', 'module_key']
PCP = ['ksb_code', 'ksb_type', 'description', 'operational_owner_key', 'strategic_owner_key', 'chartered_owner_key']
PREFIX = '# skills-radar-v1;programme='


def _safe(value):
    # A metadata row makes this reversible: hand-authored CSV text is never
    # silently stripped, including genuine apostrophes in descriptions.
    return "'" + value if value.startswith(("'", '=', '+', '-', '@', ' ', '\t', '\r', '\n')) else value


def export_csv(catalogue, template=False):
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    pid = catalogue['programmeId']
    writer.writerow([f'{PREFIX}{pid};escape=apostrophe'])
    writer.writerow(PCP if pid == 'PCP_L6' else FIXED)
    if not template:
        for record in catalogue['records']:
            values = [record['code'], record['kind'], record['description']]
            values.extend(record['owners'][path] for path in ('operational', 'strategic', 'chartered')) if pid == 'PCP_L6' else values.append(record['moduleKey'])
            writer.writerow([_safe(value) for value in values])
    return '\ufeff' + output.getvalue()


def parse_csv(content, programme, modules, prior):
    if len(content) > MAX_BYTES:
        raise CatalogueError('CSV exceeds the 1 MB limit.')
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise CatalogueError('Save the CSV as UTF-8 (UTF-8 BOM is supported).') from exc
    reader = csv.reader(io.StringIO(text, newline=''), strict=True)
    escaped = False
    try:
        header = next(reader, [])
        if len(header) == 1 and header[0].startswith(PREFIX):
            match = re.fullmatch(re.escape(PREFIX) + r'(PCP_L6|MM_L6|ME_L4|APM_L4);escape=apostrophe', header[0])
            if not match or match[1] != programme:
                raise CatalogueError('This CSV export belongs to another programme or uses unsupported metadata.')
            escaped = True
            header = next(reader, [])
        expected = PCP if programme == 'PCP_L6' else FIXED
        if programme not in PROGRAMMES or header != expected:
            raise CatalogueError(f'Required columns in order: {",".join(expected)}.')
        records, errors = [], []
        for values in reader:
            if not values or not any(values):
                continue
            row = reader.line_num
            if len(values) != len(expected):
                errors.append({'row': row, 'message': f'Expected {len(expected)} columns, found {len(values)}. Quote commas and multiline text.'})
                continue
            if escaped:
                values = [v[1:] if len(v) > 1 and v[0] == "'" and v[1] in "'=+-@ \t\r\n" else v for v in values]
            record = {'code': values[0].strip(), 'kind': values[1].strip(), 'description': values[2], '_row': row}
            if programme == 'PCP_L6':
                record['owners'] = dict(zip(('operational', 'strategic', 'chartered'), [v.strip() for v in values[3:]]))
            else:
                record['moduleKey'] = values[3].strip()
            records.append(record)
            if len(records) > MAX_RECORDS:
                raise CatalogueError(f'CSV must contain at most {MAX_RECORDS} KSBs.')
        if errors:
            raise CatalogueError('Invalid CSV rows. Nothing was imported.', errors)
    except csv.Error as exc:
        raise CatalogueError(f'Invalid CSV near line {reader.line_num}: {exc}.') from exc
    return validate(programme, {'programmeId': programme, 'records': records}, modules, prior)
