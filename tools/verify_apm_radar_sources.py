"""Read-only APM radar comparison with the supplied JSON and revised workbook."""
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
    canonical = Path(__file__).resolve().parents[1] / 'frontend/src/features/programme-suitability/skills-radar/associate_project_manager_l4_ksb_full_mapping.json'
    assert canonical.read_bytes() == args.reference_json.read_bytes(), 'Canonical JSON differs from the supplied file.'
    data = json.loads(canonical.read_text(encoding='utf-8-sig'))
    workbook = load_workbook(args.workbook, read_only=True, data_only=True)
    source = {row[0]: row for row in workbook['KSB source'].iter_rows(min_row=9, values_only=True) if re.fullmatch(r'[KSB]\d+', str(row[0]))}
    coverage = {row[0]: row for row in workbook['Coverage'].iter_rows(min_row=9, values_only=True) if re.fullmatch(r'[KSB]\d+', str(row[0]))}
    expected = {f'{prefix}{number}' for prefix, count in [('K', 31), ('S', 26), ('B', 5)] for number in range(1, count + 1)}
    assert len(data['ksbs']) == data['ksb_total'] == 62
    assert set(source) == set(coverage) == {item['code'] for item in data['ksbs']} == expected
    owners = {}
    for module in data['modules']:
        assert len(module['ksb_codes']) == module['expected_ksbs']
        for code in module['ksb_codes']:
            assert code not in owners, f'{code}: duplicate assignment.'
            owners[code] = module['mapping_key']
    assert set(owners) == expected
    for item in data['ksbs']:
        code = item['code']
        assert (item['type'], item['full_description'], item['module_name']) == tuple(source[code][1:4]), f'{code}: original source wording/type/owner mismatch.'
        assert (item['type'], item['module_name'], item['full_description']) == tuple(coverage[code][1:4]), f'{code}: revised coverage mismatch.'
        assert owners[code] == item['module_mapping_key'], f'{code}: source mapping key mismatch.'
    rows = list(workbook['Module map'].iter_rows(min_row=10, max_row=11, values_only=True))
    for module, row in zip(data['modules'], rows):
        assert (module['name'], module['expected_ksbs']) == (row[0], row[4])
        counts = Counter(item['type'] for item in data['ksbs'] if item['module_mapping_key'] == module['mapping_key'])
        assert tuple(counts[kind] for kind in ['Knowledge', 'Skill', 'Behaviour']) == tuple(row[1:4])
        assert dict(counts) == module['type_counts']
    types = Counter(item['type'] for item in data['ksbs'])
    assert dict(types) == data['type_counts']
    workbook.close()
    print(json.dumps({'ksbs': 62, 'types': dict(types),
                      'moduleCounts': [module['expected_ksbs'] for module in data['modules']], 'jsonByteIdentical': True,
                      'allDescriptionsAndAssignmentsMatchRevisedWorkbook': True,
                      'canonicalSha256': hashlib.sha256(canonical.read_bytes()).hexdigest(),
                      'workbookSha256': hashlib.sha256(args.workbook.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
