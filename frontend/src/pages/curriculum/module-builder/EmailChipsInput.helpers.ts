


/** Split a newline/comma/semicolon/space-separated string into trimmed emails. */
export function emailList(value: string): string[] {
  return value.split(/[\s,;]+/).map(item => item.trim()).filter(Boolean);
}
