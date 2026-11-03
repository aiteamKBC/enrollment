"""Compare the checked-in PCP catalogue against the supplied JSON and workbook.

Read-only verification; no application settings, credentials or database access.
Requires openpyxl from the existing backend environment.
"""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from openpyxl import load_workbook


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference-json', required=True, type=Path)
    parser.add_argument('--workbook', required=True, type=Path)
    args = parser.parse_args()
    canonical = Path(__file__).resolve().parents[1] / 'frontend/src/features/programme-suitability/skills-radar/pcp_l6_ksb_full_descriptions_and_mapping.json'
    assert canonical.read_bytes() == args.reference_json.read_bytes(), 'Canonical file differs from the supplied JSON.'
    data = json.loads(canonical.read_text(encoding='utf-8-sig'))
    workbook = load_workbook(args.workbook, read_only=True, data_only=True)
    texts = {row[0]: row for row in workbook['KSB source'].iter_rows(min_row=9, max_row=79, values_only=True)}
    coverage = {row[0]: row for row in workbook['Coverage'].iter_rows(min_row=9, max_row=79, values_only=True)}
    expected = {f'{prefix}{n}' for prefix, total in [('K', 31), ('S', 29), ('B', 11)] for n in range(1, total + 1)}
    assert len(data['ksbs']) == 71 and {item['code'] for item in data['ksbs']} == expected
    for item in data['ksbs']:
        code = item['code']
        assert (item['type'], item['full_description']) == (texts[code][1], texts[code][2]), f'{code}: full text/type mismatch.'
        assert item['full_description'] == item['wording'], f'{code}: wording mismatch.'
        assert item['display_text'] == f"{code} {item['full_description']}", f'{code}: display text mismatch.'
        for path, column in [('operational', 10), ('strategic', 11), ('chartered', 12)]:
            assert item['original_owner_group_by_pathway'][path] == coverage[code][column], f'{code}: {path} owner mismatch.'
    workbook.close()
    print(json.dumps({'ksbs': 71, 'types': dict(Counter(item['type'] for item in data['ksbs'])),
        'allDescriptionsAndOwnersMatchWorkbook': True, 'jsonByteIdentical': True,
        'canonicalSha256': hashlib.sha256(canonical.read_bytes()).hexdigest(),
        'workbookSha256': hashlib.sha256(args.workbook.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
