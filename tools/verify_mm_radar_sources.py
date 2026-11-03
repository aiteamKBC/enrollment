"""Read-only comparison of the MM radar JSON with the supplied JSON/workbook."""
import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from openpyxl import load_workbook


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference-json', required=True, type=Path)
    parser.add_argument('--workbook', required=True, type=Path)
    args = parser.parse_args()
    canonical = Path(__file__).resolve().parents[1] / 'frontend/src/features/programme-suitability/skills-radar/marketing_manager_l6_ksb_full_mapping.json'
    assert canonical.read_bytes() == args.reference_json.read_bytes(), 'Canonical JSON differs from the supplied file.'
    data = json.loads(canonical.read_text(encoding='utf-8-sig'))
    workbook = load_workbook(args.workbook, read_only=True, data_only=True)
    source = {row[0]: row for row in workbook['KSB source'].iter_rows(min_row=9, values_only=True) if re.fullmatch(r'[KSB]\d+', str(row[0]))}
    coverage = {row[0]: row for row in workbook['Coverage'].iter_rows(min_row=9, values_only=True) if re.fullmatch(r'[KSB]\d+', str(row[0]))}
    expected = {f'{prefix}{number}' for prefix, count in [('K', 14), ('S', 19), ('B', 9)] for number in range(1, count + 1)}
    assert len(data['ksbs']) == 42 and set(source) == set(coverage) == {item['code'] for item in data['ksbs']} == expected
    owners = {}
    for module in data['modules']:
        assert len(module['ksb_codes']) == module['ksb_count']
        for code in module['ksb_codes']:
            assert code not in owners, f'{code}: duplicate assignment.'
            owners[code] = module['name']
    assert set(owners) == expected
    for item in data['ksbs']:
        code = item['code']
        assert (item['category'], item['full_description'], item['module_name']) == tuple(source[code][1:4]), f'{code}: source text/category/owner mismatch.'
        assert (item['full_description'], item['module_name'], item['category']) == (coverage[code][1], coverage[code][2], coverage[code][5]), f'{code}: coverage mismatch.'
        assert owners[code] == item['module_name'], f'{code}: assignment mismatch.'
    map_rows = list(workbook['Module map'].iter_rows(min_row=7, max_row=10, values_only=True))
    for module, row in zip(data['modules'], map_rows):
        assert (module['name'], module['ksb_count']) == (row[0], row[4])
        assert module['ksb_codes'] == [code.strip() for cell in row[1:4] for code in cell.split(',')], f"{module['name']}: module map mismatch."
    workbook.close()
    print(json.dumps({'ksbs': 42, 'types': dict(Counter(item['category'] for item in data['ksbs'])),
                      'moduleCounts': [module['ksb_count'] for module in data['modules']], 'jsonByteIdentical': True,
                      'allDescriptionsAndAssignmentsMatchWorkbook': True,
                      'canonicalSha256': hashlib.sha256(canonical.read_bytes()).hexdigest(),
                      'workbookSha256': hashlib.sha256(args.workbook.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
