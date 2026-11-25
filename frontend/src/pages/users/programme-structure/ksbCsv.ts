import type { CurriculumModule, Ksb } from './catalogApi';

export type KsbFields = Pick<Ksb, 'code' | 'description'>;

export function normalizeKsbFields(codeValue: unknown, descriptionValue: unknown): KsbFields {
  const code = String(codeValue ?? '').trim().toUpperCase();
  const description = String(descriptionValue ?? '').trim();
  if (!code || code.length > 40) throw new Error('Enter a KSB code of 1 to 40 characters.');
  if (!description || description.length > 5000) throw new Error('Enter a KSB description of 1 to 5,000 characters.');
  return { code, description };
}

// Prefix spreadsheet formulas with a text marker. Additional apostrophes let our own exports round-trip.
const formulaLike = /^'*\s*[=+\-@＝＋－＠]/u;
const decodeCell = (value: string) => value.startsWith("'") && formulaLike.test(value) ? value.slice(1) : value;
const encodeCell = (value: string) => `"${(formulaLike.test(value) ? "'" + value : value).replaceAll('"', '""')}"`;

export function makeKsbCsv(rows: KsbFields[]) {
  return ['code,description', ...rows.map(({ code, description }) =>
    [code, description].map(encodeCell).join(','))].join('\r\n') + '\r\n';
}

export function makeKsbMappingCsv(ksbs: Ksb[], modules: CurriculumModule[]) {
  const names = new Map<string, number>();
  for (const { name } of modules) names.set(name, (names.get(name) ?? 0) + 1);
  const links = modules.map(module => new Set(module.ksb_ids));
  const headings = modules.map(({ id, name }) => encodeCell(names.get(name) === 1 ? name : `${name} (${id})`));
  const header = ['code', 'description', ...headings].join(',');
  const rows = ksbs.map(ksb => [ksb.code, ksb.description, ...links.map(ids => ids.has(ksb.id) ? 'Yes' : 'No')]
    .map(encodeCell).join(','));
  return [header, ...rows].join('\r\n') + '\r\n';
}

export function downloadKsbCsv(programmeCode: string, kind: 'draft' | 'mapping', csv: string) {
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = `${programmeCode.replace(/[^a-z0-9_-]+/gi, '-')}-ksb-${kind}.csv`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function parseKsbCsv(csv: string, existing: Pick<Ksb, 'code'>[] = []): KsbFields[] {
  const text = csv.replace(/^\uFEFF/, '');
  const field = /(?:"((?:[^"]|"")*)"|([^",\r\n]*))(,|\r\n|[\r\n]|$)/y;
  const codes = new Set(existing.map(({ code }) => code.trim().toUpperCase()));
  const rows: KsbFields[] = [];
  let header = false;
  let rowNumber = 0;
  while (field.lastIndex < text.length) {
    rowNumber += 1;
    const rowStart = field.lastIndex;
    const columns: string[] = [];
    let delimiter: string;
    do {
      const match = field.exec(text);
      if (!match) throw new Error(`Row ${rowNumber} has invalid CSV quoting.`);
      columns.push(match[1] === undefined ? match[2] : match[1].replaceAll('""', '"'));
      delimiter = match[3];
    } while (delimiter === ',');
    if (columns.length === 1 && !text.slice(rowStart, field.lastIndex).trim()) continue;
    if (!header) {
      if (columns.length !== 2 || columns[0].trim() !== 'code' || columns[1].trim() !== 'description') {
        throw new Error('The first row must contain exactly two columns: code,description.');
      }
      header = true;
      continue;
    }
    if (columns.length !== 2) throw new Error(`Row ${rowNumber} must contain a code and a description.`);
    try {
      const row = normalizeKsbFields(decodeCell(columns[0]), decodeCell(columns[1]));
      if (codes.has(row.code)) throw new Error(`Duplicate KSB code ${row.code}.`);
      codes.add(row.code);
      rows.push(row);
    } catch (error) {
      throw new Error(`Row ${rowNumber}. ${(error as Error).message}`);
    }
  }
  if (!rows.length) throw new Error('Add at least one KSB below the code,description header.');
  return rows;
}
