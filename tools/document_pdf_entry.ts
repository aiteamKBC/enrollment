import { previewPdf } from '../frontend/src/features/compliance-documents/previewPdf';
async function main() {
let input = '';
for await (const chunk of process.stdin) input += chunk;
try {
  const value = JSON.parse(input);
  // Rendering input is supplied only by Django's existing serializers.
  delete value.pdfUrl; delete value.pdfBase64;
  const pdf = await previewPdf(value);
  process.stdout.write(Buffer.from(await pdf.arrayBuffer()));
} catch (error) {
  process.stderr.write(error instanceof Error ? error.message : 'Document rendering failed');
  process.exitCode = 1;
}
}
void main();
