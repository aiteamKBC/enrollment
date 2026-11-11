// Both browser and Django use the same renderer and exact supplied PNG bytes.
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { build } from '../frontend/node_modules/rolldown/dist/index.mjs';
import { deepPurpleVariant, imageMetadata } from './document_logo_variant.mjs';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
// Read the actual existing palette export rather than inventing/duplicating a shade.
const theme = await build({input:path.join(root,'frontend/src/features/compliance-documents/brandedPurpleTheme.ts'),platform:'node',write:false,output:{format:'esm'}});
const {BRANDED_PURPLE}=await import('data:text/javascript;base64,'+Buffer.from(theme.output[0].code).toString('base64'));
const originalKbc=await readFile(path.join(root,'frontend/public/assets/document-branding/kbc-logo.png'));
const variant=deepPurpleVariant(originalKbc,BRANDED_PURPLE.heading);
await writeFile(path.join(root,'frontend/public/assets/document-branding/kbc-logo-deep-purple.png'),variant.bytes);
const assets = {};
for (const [name,file] of [['kbc','kbc-logo.png'],['dfe','dfe-logo.png'],['kbcDeepPurple','kbc-logo-deep-purple.png']]) {
  const bytes = await readFile(path.join(root, `frontend/public/assets/document-branding/${file}`));
  if (bytes.subarray(0, 8).toString('hex') !== '89504e470d0a1a0a') throw Error(`Invalid ${name} PNG`);
  assets[name] = { data: `data:image/png;base64,${bytes.toString('base64')}`, sha256: createHash('sha256').update(bytes).digest('hex'),...imageMetadata(bytes) };
}
await writeFile(path.join(root, 'frontend/src/features/compliance-documents/brandingAssets.json'), JSON.stringify(assets));
const output = path.join(root, 'backend/enrolment_api/document_templates/pdf_runtime');
await mkdir(output, { recursive: true });
await build({ input: path.join(root, 'tools/document_pdf_entry.ts'), platform: 'node',
  resolve: { alias: { '@': path.join(root, 'frontend/src'), jspdf: path.join(root, 'frontend/node_modules/jspdf/dist/jspdf.es.min.js') } },
  output: { file: path.join(output, 'renderer.cjs'), format: 'cjs', codeSplitting: false, sourcemap: false } });
console.log('Built shared KBC/DfE PDF runtime and verified supplied logo assets.');
