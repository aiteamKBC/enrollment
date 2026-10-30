import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import crypto from 'node:crypto';

const destination = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const source = path.dirname(destination);
const require = createRequire(path.join(source, 'frontend/package.json'));
const ts = require('typescript');
const tracked = new Set(execFileSync('git', ['ls-files'], { cwd: source, encoding: 'utf8' }).trim().split(/\r?\n/));
const manifestFile = path.join(destination, 'source-manifest.json');
const manifest = JSON.parse(fs.readFileSync(manifestFile, 'utf8'));
const queue = [];
const seen = new Set();
const overridden = new Set(['frontend/src/router/config.tsx']);
const areas = ['users', 'apprenticeship-application', 'login', 'forgot-password', 'set-password',
  'access-required', 'learner/onboarding', 'learner/welcome', 'learner/compliance',
  'learner/first-session', 'admin/coach-directory', 'coach-booking', 'lms-introduction'];
for (const file of tracked) {
  if (areas.some(area => file.startsWith(`frontend/src/pages/${area}/`)) ||
      ['frontend/src/pages/employer/EmployerDocumentsPage.tsx', 'frontend/src/pages/employer/EmployerLearnerPage.tsx'].includes(file)) queue.push(file);
}
queue.push('frontend/src/main.tsx', 'frontend/src/test/setup.ts',
  'frontend/src/hooks/useAuth.tsx', 'frontend/src/hooks/useTheme.tsx',
  'frontend/src/hooks/useAiSettings.tsx', 'frontend/src/hooks/useToast.tsx',
  'frontend/src/components/feature/ToastContainer.tsx', 'frontend/src/components/feature/AppIcon.tsx');
const pkg = JSON.parse(fs.readFileSync(path.join(source, 'frontend/package.json'), 'utf8'));
for (const part of pkg.scripts['test:teams'].split(' ')) {
  if (part.startsWith('src/')) queue.push('frontend/' + part);
}
function resolveLocal(from, specifier) {
  let base;
  if (specifier.startsWith('@/')) base = 'frontend/src/' + specifier.slice(2);
  else if (specifier.startsWith('.')) base = path.posix.normalize(path.posix.join(path.posix.dirname(from), specifier));
  else return null;
  return [base, ...['.ts', '.tsx', '.js', '.jsx', '.json', '.css', '/index.ts', '/index.tsx'].map(ext => base + ext)]
    .find(candidate => tracked.has(candidate));
}
while (queue.length) {
  const file = queue.pop();
  if (seen.has(file) || overridden.has(file) || (!tracked.has(file) && !fs.existsSync(path.join(destination, file)))) continue;
  seen.add(file);
  const content = fs.readFileSync(fs.existsSync(path.join(destination, file)) ? path.join(destination, file) : path.join(source, file), 'utf8');
  for (const imported of ts.preProcessFile(content, true, true).importedFiles) {
    const resolved = resolveLocal(file, imported.fileName);
    if (resolved) queue.push(resolved);
  }
  for (const match of content.matchAll(/(?:url\(\s*['"]?|@import\s*['"])([^'"\)\s;]+)/g)) {
    const resolved = resolveLocal(file, match[1]);
    if (resolved) queue.push(resolved);
  }
}
const configs = ['package.json', 'package-lock.json', 'index.html', 'vite.config.ts', 'vitest.config.ts',
  'tsconfig.json', 'tsconfig.app.json', 'tsconfig.node.json', 'vite-env.d.ts', 'tailwind.config.ts',
  'postcss.config.ts', 'eslint.config.ts'];
for (const file of tracked) {
  if (file.startsWith('frontend/eslint-rules/') || file.startsWith('frontend/public/') ||
      configs.some(name => file === `frontend/${name}`) ||
      (file.startsWith('frontend/src/') && file.endsWith('.d.ts'))) seen.add(file);
}
for (const file of [...seen].sort()) {
  const target = path.join(destination, file);
  if (fs.existsSync(target)) continue;
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.copyFileSync(path.join(source, file), target);
  manifest.files[file] = crypto.createHash('sha256').update(fs.readFileSync(target)).digest('hex');
}
fs.writeFileSync(manifestFile, JSON.stringify(manifest, null, 2) + '\n');
console.log(`Exported ${seen.size} frontend source, configuration and asset files.`);
