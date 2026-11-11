// Measured/estimated from all five image-only pages of the supplied reference.
// Geometry contains decoration only: no reference text, personal data or marks.
export const REFERENCE_PURPLE = {
  heading: [47, 12, 79], secondary: [69, 20, 101], accent: [143, 26, 235],
  section: [231, 215, 252], label: [255, 255, 255], border: [195, 155, 228],
  text: [24, 22, 31], muted: [91, 80, 108], white: [255, 255, 255],
  font: { title: 22, section: 14, subheading: 12, body: 10, table: 9.5, caption: 8 },
  lineHeight: 1.35, padding: 3, tablePadding: 1.6,
  margins: { top: 12, right: 12, bottom: 27, left: 12 },
} as const;

export const REFERENCE_WAVES = [
  { zone: 'top', colour: [239, 226, 252], path: 'M 136 0 L 210 0 L 210 29 C 187 27 176 16 156 9 C 146 5 141 3 136 0 Z' },
  { zone: 'top', colour: [220, 186, 246], path: 'M 155 0 L 210 0 L 210 25 C 194 20 183 13 173 6 C 166 2 162 1 155 0 Z' },
  { zone: 'top', colour: [186, 121, 232], path: 'M 176 0 L 210 0 L 210 13 C 197 10 190 13 184 7 C 180 3 178 1 176 0 Z' },
  { zone: 'footer', colour: [239, 226, 252], path: 'M 0 278 C 34 274 53 292 92 295 C 133 300 168 283 210 282 L 210 297 L 0 297 Z' },
  { zone: 'footer', colour: [218, 180, 245], path: 'M 0 284 C 26 281 49 299 90 301 C 137 306 174 286 210 286 L 210 297 L 0 297 Z' },
  { zone: 'footer', colour: [184, 113, 232], path: 'M 0 285 C 17 285 35 294 55 297 L 0 297 Z' },
] as const;

export function referenceDecorationSvg(zone: 'top' | 'footer'): string {
  const viewBox = zone === 'top' ? '0 0 210 32' : '0 275 210 22';
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${viewBox}">${REFERENCE_WAVES.filter(w=>w.zone===zone).map(w=>`<path d="${w.path}" fill="rgb(${w.colour.join(',')})"/>`).join('')}</svg>`;
}
