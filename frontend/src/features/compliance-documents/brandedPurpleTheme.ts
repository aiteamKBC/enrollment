/** Versioned geometry estimated from the user's KBC/DfE ILR image (A4 mm). */
export const BRAND_ID = 'kbc-dfe-purple-v3';
export const CURRENT_BRAND_ID = 'kbc-dfe-purple-v4';
export const isBrandedPresentation = (theme?:string) => theme === BRAND_ID || theme === CURRENT_BRAND_ID;
export const BRANDED_PURPLE = {
  heading: [77, 24, 111], secondary: [100, 39, 131], accent: [139, 44, 177],
  section: [239, 225, 250], label: [255, 255, 255], border: [185, 169, 201],
  text: [22, 18, 30], muted: [91, 80, 108], white: [255, 255, 255],
  font: { title: 25, section: 15, subheading: 12, body: 10, table: 10, caption: 8.5 },
  lineHeight: 1.2, padding: 3, tablePadding: .95,
  margins: { top: 29, right: 10, bottom: 25, left: 10 },
} as const;
export const BRAND_WAVES = [
  { colour: [235, 215, 248], path: 'M 76 0 L 169 0 C 149 7 138 13 126 12 C 108 11 95 5 76 0 Z' },
  { colour: [211, 160, 233], path: 'M 80 0 L 154 0 C 141 6 128 11 116 10 C 104 9 95 4 80 0 Z' },
  { colour: [179, 69, 217], path: 'M 71 0 L 116 0 C 126 2 130 5 135 7 C 120 14 110 11 98 7 C 89 4 81 2 71 0 Z' },
  { colour: [90, 32, 121], path: 'M 94 0 L 144 0 C 133 4 125 7 117 10 C 109 7 103 3 94 0 Z' },
  { colour: [229, 205, 246], path: 'M 0 273 C 34 278 59 289 89 297 L 0 297 Z' },
  { colour: [175, 121, 214], path: 'M 0 280 C 31 283 44 292 68 297 L 0 297 Z' },
  { colour: [91, 33, 126], path: 'M 0 280 C 23 283 35 290 49 297 L 0 297 Z' },
] as const;
