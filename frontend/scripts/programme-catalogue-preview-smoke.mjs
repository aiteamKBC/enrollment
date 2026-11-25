import assert from 'node:assert/strict';
import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { chromium } from 'playwright';

const origin = 'http://localhost:3001';
const routeRoot = origin + '/users/programme-structure';
const screenshots = await mkdtemp(resolve(tmpdir(), 'enrolment-interactive-preview-'));
const executablePath = process.env.CATALOGUE_PREVIEW_CHROMIUM;
const mobile = process.env.CATALOGUE_PREVIEW_MOBILE === '1';
const phoneViewport = { width: 390, height: 844 };
const viewport = mobile ? phoneViewport : { width: 1440, height: 960 };
const layout = mobile ? 'mobile' : 'desktop';
const browser = await chromium.launch(executablePath ? { executablePath, headless: true } : { channel: 'chromium', headless: true });

try {
  const page = await browser.newPage({
    viewport, isMobile: mobile, hasTouch: mobile, locale: 'en-US', reducedMotion: 'reduce', serviceWorkers: 'block',
  });
  const catalogueRequests = [];
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  let collectionFixture = 'pending';
  let liveCatalogue;
  await page.route('**/*', route => {
    const address = new URL(route.request().url());
    if (address.origin !== origin) return route.abort();
    if (address.pathname === '/login_api/me/') return route.fulfill({ json: {
      user: {
        id: 1, email: 'fixture@example.test', displayName: 'Preview verifier', role: 'admin',
        access: 'super-admin', subjectType: 'staff', subjectId: 1, permissions: [], hasPassword: true,
      },
    } });
    if (address.pathname.startsWith('/enrolment_api/programme-structure/catalog/')) {
      if (liveCatalogue) return liveCatalogue(route, address.pathname);
      if (collectionFixture && address.pathname.endsWith('/catalog/programmes/')) {
        return collectionFixture === 'pending'
          ? route.fulfill({ status: 503, json: { code: 'catalog_schema_not_ready', error: 'Database setup required.' } })
          : route.fulfill({ status: 404, contentType: 'text/html', body: '<h1>Not Found</h1>' });
      }
      catalogueRequests.push(address.pathname);
      return route.abort();
    }
    if (liveCatalogue && address.pathname === '/learner_api/enrollment-csrf/') {
      return route.fulfill({ json: { csrfToken: 'fixture' } });
    }
    if (address.pathname.startsWith('/enrolment_api/wizard-layout')) return route.fulfill({ json: {
      version: 1, layout: { programmeStructure: { schemaVersion: 2, programmes: [] } },
    } });
    if (address.pathname === '/enrolment_api/programme-structure/draft/') return route.fulfill({
      json: { structure: null, baseVersion: 1, revision: 0 },
    });
    if (/^\/(?:\w+_api|api|ws|media)(?:\/|$)/.test(address.pathname)) return route.fulfill({ status: 404, json: {} });
    if (route.request().method() !== 'GET') return route.abort();
    return route.continue();
  });
  await page.routeWebSocket(/\/ws(?:\/|$)/, socket => socket.close());

  const activate = locator => mobile ? locator.tap() : locator.click();
  const click = name => activate(page.getByRole('button', { name, exact: true }).or(
    page.getByRole('navigation', { name: 'Breadcrumb', exact: true }).getByRole('link', { name, exact: true }),
  ));
  const fill = (name, value) => page.getByLabel(name, { exact: true }).fill(value);
  const select = (name, label) => page.getByLabel(name, { exact: true }).selectOption({ label });
  const chooseFromSearch = async (label, queryLabel, query, option) => {
    await activate(page.getByRole('combobox', { name: label, exact: true }));
    await fill(queryLabel, query);
    await activate(page.getByRole('option', { name: option }));
  };
  const openProgrammeStructure = async () => {
    if (mobile) await click('Open navigation');
    await activate(page.getByRole('navigation', { name: 'Enrolment navigation' }).getByRole('link', { name: 'Programme Structure' }));
  };
  const selectPreviewStaff = async (label, name, email) => {
    const role = label.toLowerCase();
    await click(`Add preview ${role}`);
    await fill(`Preview ${role} name`, name);
    await fill(`Preview ${role} email (optional)`, email);
    await click('Add to preview staff');
    const picker = page.getByRole('combobox', { name: label, exact: true });
    await picker.click();
    await fill(`Search ${role} by name or email`, email);
    await page.getByRole('option', { name: new RegExp(name) }).click();
    assert.match(await picker.textContent(), new RegExp(name));
  };
  const capture = (filename, fullPage = true) => page.screenshot({ path: resolve(screenshots, filename), fullPage, animations: 'disabled' });
  const verifyMobile = async () => {
    await page.waitForFunction(() => {
      const drawer = document.getElementById('enrolment-navigation');
      return !drawer || drawer.getBoundingClientRect().right <= 0;
    });
    const viewport = await page.evaluate(() => ({
      width: document.documentElement.clientWidth,
      page: document.documentElement.scrollWidth,
      body: document.body.scrollWidth,
      x: window.scrollX,
    }));
    assert.ok(viewport.page <= viewport.width && viewport.body <= viewport.width && viewport.x === 0,
      `Preview overflows on mobile: ${JSON.stringify(viewport)}`);
  };
  const checkLabels = async selector => {
    const unnamed = await page.locator(selector).evaluate(region =>
      [...region.querySelectorAll('input:not([type=hidden]), select, textarea')].filter(control =>
        control.getClientRects().length && !control.labels?.length && !control.getAttribute('aria-label') && !control.getAttribute('aria-labelledby'),
      ).map(control => control.id || control.getAttribute('name') || control.tagName),
    );
    assert.deepEqual(unnamed, [], 'Visible form controls must have accessible labels');
  };
  const checkContrast = async locator => {
    const ratio = await locator.evaluate(element => {
      const style = getComputedStyle(element);
      const luminance = colour => {
        const rgb = colour.match(/[\d.]+/g).slice(0, 3).map(channel => Number(channel) / 255)
          .map(channel => channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4);
        return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
      };
      const colours = [luminance(style.color), luminance(style.backgroundColor)].sort((left, right) => right - left);
      return (colours[0] + 0.05) / (colours[1] + 0.05);
    });
    assert.ok(ratio >= 4.5, `Expected at least 4.5:1 text contrast, got ${ratio.toFixed(2)}:1`);
  };

  await page.goto(origin + '/users');
  await openProgrammeStructure();
  const setupHeading = page.getByRole('heading', { name: 'Catalogue setup required' });
  const previewLink = page.getByRole('link', { name: 'Open interactive preview' });
  await setupHeading.waitFor();
  assert.equal(await previewLink.getAttribute('href'), '/users/programme-structure/preview');
  assert.equal(await page.getByRole('link', { name: /legacy programme structure/i }).count(), 0);
  assert.equal(await page.getByRole('button', { name: 'Add programme' }).count(), 0);
  assert.equal(await page.getByText(/No programmes to show/).count(), 0);
  await capture(`catalogue-setup-${layout}.png`);
  collectionFixture = 'missing';
  await click('Check again');
  await page.getByText(/Programme Catalogue API returned 404/).waitFor();
  assert.equal(await previewLink.count(), 0);
  assert.equal(await page.getByText(/No programmes to show/).count(), 0);
  collectionFixture = 'pending';
  await click('Try again');
  await previewLink.waitFor();
  collectionFixture = null;
  await activate(previewLink);
  await page.waitForURL(routeRoot + '/preview');
  await page.getByText(/No programmes to show/).waitFor({ timeout: 45000 });
  if (mobile) await verifyMobile();
  await capture(`empty-${layout}.png`, false);
  await click('Add programme');
  await checkLabels('#catalog-editor');
  assert.equal(await page.getByLabel(/EPA period/i).count(), 0);
  assert.equal(await page.getByLabel('Planned duration (months)', { exact: true }).count(), 0);
  await page.getByLabel('Programme name', { exact: true }).focus();
  await page.keyboard.press('Tab');
  const codeInput = page.getByLabel('Programme code', { exact: true });
  assert.ok(await codeInput.evaluate(control => control === document.activeElement && control.matches(':focus-visible') &&
    (getComputedStyle(control).boxShadow !== 'none' || getComputedStyle(control).outlineStyle !== 'none')), 'Keyboard focus must be visible');
  await fill('Programme name', 'Planning review');
  await fill('Programme code', 'REV');
  await select('Level', 'Level 4');
  await fill('EPA organisation', 'Preview assessment organisation');
  const totalCost = page.getByLabel('Total per apprentice (GBP)', { exact: true });
  await totalCost.fill('11000.25');
  assert.equal(await totalCost.evaluate(input => input.validity.patternMismatch), true);
  await fill('Total per apprentice (GBP)', '11000');
  await fill('EPA cost (GBP)', '1000');
  await fill('Funding band maximum (GBP)', '10500');
  page.once('dialog', dialog => dialog.dismiss());
  await click('Cancel');
  assert.equal(await page.getByLabel('Programme name', { exact: true }).inputValue(), 'Planning review');
  if (mobile) await verifyMobile();
  await capture(`programme-form-${layout}.png`);
  await click('Add to preview');
  await click('Pricing breakdown');
  assert.equal(await page.getByLabel('Total programme cost (GBP)', { exact: true }).inputValue(), '11000');
  await fill('Total programme cost (GBP)', '21000.50');
  await page.getByText(/Enter whole-pound amounts/).waitFor();
  await fill('Total programme cost (GBP)', '21000');
  await page.getByRole('table').getByText('£21,000', { exact: true }).waitFor();
  await page.getByText(/does not determine the employer balance/).waitFor();
  await click('Reset inputs');

  await click('Cohorts');
  await click('Add cohort');
  assert.equal(await page.getByLabel('Programme', { exact: true }).locator('option:checked').textContent(), 'Planning review · REV');
  await fill('Cohort name', 'January intake');
  await fill('Start date', '2027-01-01');
  const cohortDuration = page.getByLabel('Planned duration (months)', { exact: true });
  assert.equal(await cohortDuration.inputValue(), '');
  assert.ok(await cohortDuration.evaluate(field => field.required && field.validity.valueMissing));
  assert.equal(await page.getByLabel('Training end date', { exact: true }).inputValue(), '');
  await cohortDuration.fill('12');
  assert.equal(await page.getByLabel('Training end date', { exact: true }).inputValue(), '2027-12-31');
  assert.equal(await page.getByLabel('EPA period (months)', { exact: true }).inputValue(), '');
  await fill('EPA period (months)', '1');
  assert.equal(await page.getByLabel('Apprenticeship end date', { exact: true }).inputValue(), '2028-01-31');
  await page.getByText('13 months', { exact: true }).waitFor();
  if (mobile) await verifyMobile();
  await capture(`cohort-schedule-${layout}.png`);
  await fill('Training end date', '2028-01-31');
  assert.equal(await page.getByLabel('Apprenticeship end date', { exact: true }).inputValue(), '2028-02-29');
  await fill('Apprenticeship end date', '2028-03-15');
  await capture(`cohort-overrides-${layout}.png`);
  await click('Add to preview');
  await click('Edit January intake');
  const manualDates = page.getByText('Manually overridden', { exact: true });
  await manualDates.nth(1).waitFor();
  assert.equal(await manualDates.count(), 2);
  await cohortDuration.fill('14');
  assert.equal(await page.getByLabel('Training end date', { exact: true }).inputValue(), '2028-01-31');
  assert.equal(await page.getByLabel('Apprenticeship end date', { exact: true }).inputValue(), '2028-03-15');
  await click('Reset training end date to automatic');
  assert.equal(await page.getByLabel('Training end date', { exact: true }).inputValue(), '2028-02-29');
  assert.equal(await page.getByLabel('Apprenticeship end date', { exact: true }).inputValue(), '2028-03-15');
  await click('Reset apprenticeship end date to automatic');
  assert.equal(await page.getByLabel('Apprenticeship end date', { exact: true }).inputValue(), '2028-03-31');
  await click('Update preview');
  await click('Add group');
  assert.equal(await page.getByLabel('Cohort', { exact: true }).locator('option:checked').textContent(), 'January intake');
  await fill('Group name', 'Monday delivery');
  await selectPreviewStaff('Skills Coach', 'Temporary Coach', 'coach@preview.test');
  await select('Day of week', 'Monday');
  await click('Choose start time');
  await select('Choose start time hour', '00');
  await page.getByLabel('Start time minute', { exact: true }).pressSequentially('5');
  await checkLabels('#catalog-editor');
  if (mobile) await verifyMobile();
  await capture(`group-time-picker-${layout}.png`);
  await click('Set start time');
  assert.equal(await page.getByLabel('Start time', { exact: true }).inputValue(), '00:05');
  const endPicker = page.getByRole('button', { name: 'Choose end time', exact: true });
  await endPicker.press('Enter');
  const endHour = page.getByRole('textbox', { name: 'End time hour', exact: true });
  const endMinute = page.getByRole('textbox', { name: 'End time minute', exact: true });
  assert.ok(await endHour.evaluate(control => document.activeElement === control));
  await page.keyboard.type('23');
  await page.keyboard.press('Tab');
  await page.keyboard.press('Tab');
  assert.ok(await endMinute.evaluate(control => document.activeElement === control));
  await page.keyboard.type('60');
  assert.ok(await page.getByRole('button', { name: 'Set end time', exact: true }).isDisabled());
  await fill('End time minute', '59');
  await endMinute.press('Enter');
  assert.equal(await page.getByLabel('End time', { exact: true }).inputValue(), '23:59');
  await click('Add to preview');
  await page.getByText('Temporary Coach', { exact: true }).waitFor();
  await page.getByText('Monday · 00:05–23:59', { exact: true }).waitFor();
  await click('Edit Monday delivery');
  assert.equal(await page.getByLabel('Start time', { exact: true }).inputValue(), '00:05');
  assert.equal(await page.getByLabel('End time', { exact: true }).inputValue(), '23:59');
  await page.setViewportSize(phoneViewport);
  await verifyMobile();
  await click('Choose end time');
  await verifyMobile();
  await capture('edit-group-time-picker-mobile.png');
  await page.keyboard.press('Escape');
  assert.ok(await endPicker.evaluate(control => document.activeElement === control));
  const endInput = page.getByLabel('End time', { exact: true });
  await endInput.press('Control+a');
  await endInput.pressSequentially('2400');
  assert.equal(await endInput.inputValue(), '24:00');
  assert.equal(await endInput.evaluate(input => input.validity.patternMismatch), true);
  await endInput.press('Control+a');
  await endInput.pressSequentially('0005');
  assert.equal(await endInput.inputValue(), '00:05');
  await click('Update preview');
  await page.getByText('End time must be later than start time.', { exact: true }).waitFor();
  await endInput.press('Control+a');
  await endInput.pressSequentially('2215');
  assert.equal(await endInput.inputValue(), '22:15');
  await click('Update preview');
  await page.getByText('Monday · 00:05–22:15', { exact: true }).waitFor();
  await page.setViewportSize(viewport);

  await click('Open module library');
  await activate(page.getByRole('tab', { name: /^KSB Library/ }));
  await click('Add KSB');
  await fill('KSB code', 'K1');
  await fill('Description', 'Planning criteria');
  await click('Add to preview');
  await click('Edit K1');
  await fill('Description', 'Planning criteria reviewed');
  await click('Update preview');
  await page.getByText('Planning criteria reviewed', { exact: true }).waitFor();
  const uploadKsbCsv = async (name, csv) => {
    const picker = page.waitForEvent('filechooser');
    await click('Upload CSV');
    await (await picker).setFiles({ name, mimeType: 'text/csv', buffer: Buffer.from(csv) });
  };
  await uploadKsbCsv('duplicates.csv', 'code,description\nB1,Would be added\nk1,Already exists');
  await page.getByRole('alert').filter({ hasText: 'Duplicate KSB code K1' }).waitFor();
  assert.equal(await page.getByText('Would be added', { exact: true }).count(), 0);
  await uploadKsbCsv('ksbs.csv', 'code,description\nS1,"Plan, deliver and review"\nB1,Reflect on progress');
  await page.getByText(/ksbs.csv contains 2 new KSBs/).waitFor();
  if (mobile) await verifyMobile();
  await capture(`ksb-import-${layout}.png`);
  await click('Import KSBs');
  await page.getByText('2 KSBs imported into preview.', { exact: true }).waitFor();
  await page.getByText('Plan, deliver and review', { exact: true }).waitFor();
  const download = page.waitForEvent('download');
  await click('Download draft CSV');
  const draftCsv = await download;
  assert.equal(draftCsv.suggestedFilename(), 'REV-ksb-draft.csv');
  assert.equal(await readFile(await draftCsv.path(), 'utf8'),
    'code,description\r\n"K1","Planning criteria reviewed"\r\n"S1","Plan, deliver and review"\r\n"B1","Reflect on progress"\r\n');
  await activate(page.getByRole('tab', { name: 'Module Templates (0)', exact: true }));
  await click('Add module template');
  await fill('Module name', 'Planning template');
  await fill('Number of sessions', '2');
  await fill('Progress reviews', '2');
  assert.equal(await page.getByLabel('Number of weeks', { exact: true }).count(), 0);
  assert.equal(await page.getByLabel('Number of lectures', { exact: true }).count(), 0);
  await fill('Number of books', '1');
  await page.getByRole('checkbox', { name: /K1/ }).check();
  await fill('Search KSBs', 'does not exist');
  await page.getByText('1 selected', { exact: true }).waitFor();
  assert.equal(await page.getByRole('checkbox', { name: /K1/ }).count(), 0);
  await fill('Search KSBs', '');
  await page.getByRole('checkbox', { name: /S1/ }).check();
  await click('Add weekly topic');
  await fill('Topic content', 'Review week one');
  await click('Add to preview');
  await click('Planning review');
  await click('Cohorts');
  await click('January intake');
  await click('Monday delivery');
  await click('Add module');
  assert.match(await page.getByRole('combobox', { name: 'Programme', exact: true }).textContent(), /Planning review/);
  assert.equal(await page.getByLabel('Group', { exact: true }).locator('option:checked').textContent(), 'Monday delivery');
  assert.equal(await page.locator('#catalog-editor footer').evaluate(footer => getComputedStyle(footer).position), mobile ? 'static' : 'sticky');
  if (mobile) await verifyMobile();
  await capture(`scheduled-module-parents-${layout}.png`);
  assert.ok(await page.getByRole('radio', { name: 'Select existing module', exact: true }).isChecked());
  await select('Module and revision', 'Planning template · v1 (latest)');
  const selectedCurriculum = page.getByRole('region', { name: 'Selected module curriculum', exact: true });
  await selectedCurriculum.getByText('v1', { exact: true }).waitFor();
  await activate(selectedCurriculum.getByText('View KSBs and weekly topics', { exact: true }));
  await activate(selectedCurriculum.getByText('Week 1', { exact: true }));
  await selectedCurriculum.getByText('Review week one', { exact: true }).waitFor();
  await selectedCurriculum.getByText('Plan, deliver and review', { exact: false }).waitFor();
  if (mobile) await verifyMobile();
  await capture(`existing-module-curriculum-${layout}.png`);
  await fill('Start date', '2027-01-04');
  assert.equal(await page.getByLabel('End date', { exact: true }).inputValue(), '2027-01-17');
  await fill('End date', '2027-01-20');
  await page.getByText('No preview tutors added.', { exact: true }).waitFor();
  await selectPreviewStaff('Tutor', 'Temporary Tutor', 'tutor@preview.test');
  await click('Add to preview');
  await page.getByText('Temporary Tutor', { exact: true }).waitFor();
  await page.getByText(/Pinned revision v1/).waitFor();
  const pinnedLink = page.getByRole('link', { name: 'View pinned curriculum' });
  await pinnedLink.press('Enter');
  assert.ok(await page.locator('#pinned-curriculum').evaluate(region => document.activeElement === region));
  await page.getByText('Week 1', { exact: true }).click();
  await page.getByText('Review week one', { exact: true }).waitFor();
  await page.getByText('Plan, deliver and review', { exact: false }).waitFor();
  await page.setViewportSize(phoneViewport);
  await verifyMobile();
  await capture('scheduled-module-mobile.png');

  await page.setViewportSize(viewport);
  const deliveryUrl = page.url();
  assert.ok(new URL(deliveryUrl).searchParams.has('delivery'));
  const crumb = page.getByRole('navigation', { name: 'Breadcrumb' });
  assert.match(await crumb.getByRole('link', { name: 'Monday delivery', exact: true }).getAttribute('href'), /group=/);
  await page.goBack();
  await page.getByRole('heading', { name: 'Monday delivery', exact: true }).waitFor();
  await page.goForward();
  await page.waitForURL(deliveryUrl);
  await page.getByRole('heading', { name: 'Planning template', exact: true }).waitFor();
  await click('Edit Planning template');
  assert.equal(await page.getByLabel('End date', { exact: true }).inputValue(), '2027-01-20');
  await click('Reset end date to automatic');
  assert.equal(await page.getByLabel('End date', { exact: true }).inputValue(), '2027-01-17');
  await fill('Start date', '2027-01-05');
  assert.equal(await page.getByLabel('End date', { exact: true }).inputValue(), '2027-01-18');
  page.once('dialog', async dialog => {
    assert.equal(dialog.message(), 'Discard your unsaved changes?');
    await dialog.dismiss();
  });
  await page.goBack();
  await page.getByRole('form', { name: 'Edit scheduled module' }).waitFor();
  assert.equal(await page.getByLabel('Start date', { exact: true }).inputValue(), '2027-01-05');
  await click('Update preview');
  await page.getByText('Scheduled module updated in preview.', { exact: true }).waitFor();
  await page.getByRole('heading', { name: 'Pinned curriculum v1', exact: true }).waitFor();
  await page.getByText('18 Jan 2027', { exact: true }).waitFor();
  await click('Edit Planning template');
  await page.getByText('Auto-calculated', { exact: true }).waitFor();
  assert.equal(await page.getByLabel('End date', { exact: true }).inputValue(), '2027-01-18');
  await click('Cancel');
  await click('Back to Monday delivery');
  await page.getByRole('heading', { name: 'Monday delivery', exact: true }).waitFor();
  await click('Back to January intake');
  await page.getByRole('heading', { name: 'January intake', exact: true }).waitFor();
  await click('Planning review');
  assert.equal(await page.getByRole('navigation', { name: 'Programme workspace' }).getByRole('button', { name: 'Cohorts' }).getAttribute('aria-current'), 'page');
  await click('Overview');
  await page.getByRole('heading', { name: 'Delivery at a glance' }).waitFor();
  assert.equal(await page.getByText('Planned duration', { exact: true }).count(), 0);
  await capture(`programme-overview-${layout}.png`);
  await click('Edit Planning review');
  assert.equal(await page.getByLabel('Planned duration (months)', { exact: true }).count(), 0);
  assert.equal(await page.getByLabel('EPA organisation', { exact: true }).inputValue(), 'Preview assessment organisation');
  await fill('Description', 'Reviewed programme description');
  await click('Update preview');
  await click('Cohorts');
  await click('Add cohort');
  await fill('Cohort name', 'Six-month intake');
  assert.equal(await cohortDuration.inputValue(), '');
  await cohortDuration.fill('6');
  await fill('Start date', '2027-01-01');
  await fill('EPA period (months)', '0');
  assert.equal(await page.getByLabel('Training end date', { exact: true }).inputValue(), '2027-06-30');
  await click('Add to preview');
  await page.getByRole('heading', { name: 'Six-month intake', exact: true }).waitFor();
  await click('Planning review');
  await page.getByRole('heading', { name: 'Cohorts', exact: true }).waitFor();
  await page.getByText('14 months', { exact: true }).waitFor();
  await page.getByText('6 months', { exact: true }).waitFor();
  await click('January intake');
  await click('Edit January intake');
  assert.equal(await cohortDuration.inputValue(), '14');
  assert.equal(await page.getByLabel('Training end date', { exact: true }).inputValue(), '2028-02-29');
  assert.equal(await page.getByLabel('Apprenticeship end date', { exact: true }).inputValue(), '2028-03-31');
  await page.getByText('15 months', { exact: true }).waitFor();
  await click('Cancel');
  await click('Planning review');
  await click('Curriculum');
  await click('Planning template');
  await click('Edit Planning template');
  await fill('Number of sessions', '4');
  await fill('Topic content', 'Revised week one');
  await click('Update preview');
  await page.getByText(/Latest revision v2/).waitFor();
  await page.getByText('Week 1', { exact: true }).click();
  await page.getByText('Revised week one', { exact: true }).waitFor();
  await click('Planning review');
  await click('Cohorts');
  await click('January intake');
  await click('Monday delivery');
  await click('Planning template');
  await page.getByText('Week 1', { exact: true }).click();
  await page.getByText('Review week one', { exact: true }).waitFor();
  await click('Planning review');
  await click('Pricing breakdown');
  await checkLabels('section[aria-labelledby="pricing-calculator"]');
  await select('Cohort', 'January intake');
  await select('Group', 'Monday delivery');
  await page.getByText('£1,600', { exact: true }).waitFor();
  await page.getByText('£750', { exact: true }).waitFor();
  await capture(`pricing-${layout}.png`);
  await page.setViewportSize({ width: 320, height: 800 });
  await verifyMobile();
  const table = page.getByRole('table', { name: 'Cost allocation and reconciliation' });
  assert.ok(await table.evaluate(element => element.scrollWidth <= element.clientWidth), 'Allocation table clips at 320 px');
  await checkContrast(table.getByRole('columnheader', { name: 'Category' }));
  await checkContrast(page.getByRole('status').filter({ hasText: 'Funding band comparison' }));

  await click('Programme catalogue');
  await click('Add module');
  const parentProgramme = page.getByRole('combobox', { name: 'Programme', exact: true });
  assert.match(await parentProgramme.textContent(), /Select a programme/);
  assert.ok(await page.getByLabel('Cohort', { exact: true }).isDisabled());
  await chooseFromSearch('Programme', 'Search programmes by name or code', 'REV', /Planning review/);
  await select('Cohort', 'January intake');
  await select('Group', 'Monday delivery');
  await select('Module and revision', 'Planning template · v2 (latest)');
  await activate(parentProgramme);
  await activate(page.getByRole('option', { name: 'Select a programme', exact: true }));
  for (const label of ['Cohort', 'Group', 'Module and revision']) {
    assert.equal(await page.getByLabel(label, { exact: true }).inputValue(), '');
  }
  assert.ok(await page.getByRole('button', { name: 'Add to preview' }).isDisabled());
  await verifyMobile();
  assert.equal(await page.locator('#catalog-editor footer').evaluate(footer => getComputedStyle(footer).position), 'static');
  await capture('scheduled-module-parents-narrow.png');
  await page.setViewportSize(viewport);
  await chooseFromSearch('Programme', 'Search programmes by name or code', 'REV', /Planning review/);
  await select('Cohort', 'January intake');
  await select('Group', 'Monday delivery');
  await page.getByRole('radio', { name: 'Create new module', exact: true }).check();
  await fill('Module name', 'Inline template');
  await fill('Number of sessions', '2');
  await fill('Progress reviews', '1');
  await fill('Number of books', '1');
  await page.getByRole('checkbox', { name: /S1/ }).check();
  await page.getByRole('button', { name: 'Remove S1 from selection', exact: true }).waitFor();
  await click('Add weekly topic');
  await fill('Topic content', 'Topic added during scheduling');
  await click('Next to schedule and tutor');
  assert.ok(await page.getByRole('heading', { name: 'Schedule and tutor' }).evaluate(heading => heading === document.activeElement));
  await fill('Start date', '2027-01-31');
  assert.equal(await page.getByLabel('End date', { exact: true }).inputValue(), '2027-02-13');
  await chooseFromSearch('Tutor', 'Search tutor by name or email', 'tutor@preview.test', /Temporary Tutor/);
  await fill('End date', '2028-03-01');
  await click('Add to preview');
  await page.getByRole('alert').filter({ hasText: 'must fit within the cohort training dates' }).waitFor();
  await click('Reset end date to automatic');
  assert.equal(await page.getByLabel('End date', { exact: true }).inputValue(), '2027-02-13');
  await fill('End date', '2027-02-09');
  if (mobile) await verifyMobile();
  await capture(`inline-module-form-${layout}.png`);
  await click('Add to preview');
  await page.getByRole('heading', { name: 'Inline template', exact: true }).waitFor();
  await page.getByText('9 Feb 2027', { exact: true }).waitFor();
  await page.getByText('Week 1', { exact: true }).click();
  await page.getByText('Topic added during scheduling', { exact: true }).waitFor();
  await click('Back to Monday delivery');
  await click('Edit Inline template');
  assert.equal(await page.getByLabel('End date', { exact: true }).inputValue(), '2027-02-09');
  await click('Cancel');
  await click('Planning review');
  await click('Curriculum');
  const templateTab = page.getByRole('tab', { name: 'Module Templates (2)', exact: true });
  const ksbTab = page.getByRole('tab', { name: 'KSB Library (3)', exact: true });
  await templateTab.focus();
  await page.keyboard.press('ArrowRight');
  await page.getByRole('tab', { name: 'KSB Library (3)', selected: true }).waitFor();
  await ksbTab.focus();
  await page.keyboard.press('ArrowRight');
  await page.getByRole('tab', { name: 'KSB Mapping', selected: true }).waitFor();
  const matrix = page.getByRole('table', { name: 'KSB mapping matrix' });
  const rows = code => matrix.getByRole('row').filter({ has: page.getByRole('rowheader', { name: code, exact: true }) });
  assert.equal(await rows('K1').getByText('Assigned', { exact: true }).count(), 1);
  assert.equal(await rows('S1').getByText('Assigned', { exact: true }).count(), 2);
  assert.equal(await rows('B1').getByText('Unassigned', { exact: true }).count(), 2);
  if (mobile) {
    await verifyMobile();
    const scroller = page.getByRole('region', { name: 'Scrollable KSB mapping matrix' });
    assert.ok(await scroller.evaluate(region => region.scrollWidth > region.clientWidth));
    await scroller.focus();
    await page.keyboard.press('ArrowRight');
    await page.waitForFunction(() => document.activeElement?.scrollLeft > 0);
    await verifyMobile();
    await scroller.evaluate(region => { region.scrollLeft = 0; });
  }
  await capture(`ksb-mapping-${layout}.png`, !mobile);
  await click('Unassigned KSBs');
  assert.equal(await rows('S1').count(), 0);
  assert.equal(await rows('B1').count(), 1);
  await click('Assigned KSBs');
  await fill('Search KSB code or description', 'plan, deliver');
  assert.equal(await rows('S1').count(), 1);
  assert.equal(await rows('K1').count(), 0);
  const mappingDownload = page.waitForEvent('download');
  await click('Export CSV');
  const mappingCsv = await mappingDownload;
  assert.equal(mappingCsv.suggestedFilename(), 'REV-ksb-mapping.csv');
  assert.equal(await readFile(await mappingCsv.path(), 'utf8'),
    'code,description,"Planning template","Inline template"\r\n"S1","Plan, deliver and review","Yes","Yes"\r\n');
  await fill('Search KSB code or description', '');
  await click('All KSBs');
  await click('Edit assignments');
  const assignmentForm = page.getByRole('form', { name: 'Edit assignments' });
  await select('Module', 'Inline template · v1');
  assert.ok(await assignmentForm.getByRole('checkbox', { name: /S1/ }).isChecked());
  assert.equal(await assignmentForm.getByRole('checkbox', { name: /K1/ }).isChecked(), false);
  await select('Module', 'Planning template · v2');
  await assignmentForm.getByRole('checkbox', { name: /B1/ }).check();
  await click('Remove K1 from selection');
  page.once('dialog', async dialog => {
    assert.equal(dialog.message(), 'Discard your unsaved changes?');
    await dialog.dismiss();
  });
  await click('Cancel');
  assert.ok(await assignmentForm.getByRole('checkbox', { name: /B1/ }).isChecked());
  assert.equal(await assignmentForm.getByRole('checkbox', { name: /K1/ }).isChecked(), false);
  await assignmentForm.getByRole('checkbox', { name: /K1/ }).check();
  await capture(`ksb-assignment-edit-${layout}.png`);
  await click('Save assignments');
  await page.getByText('KSB assignments updated in preview.', { exact: true }).waitFor();
  await matrix.getByRole('columnheader', { name: /Planning template\s*v3/ }).waitFor();
  assert.equal(await rows('B1').getByText('Assigned', { exact: true }).count(), 1);
  const assignedTotal = page.getByRole('term').filter({ hasText: /^Assigned KSBs$/ });
  assert.equal(await assignedTotal.locator('xpath=following-sibling::dd').textContent(), '3');
  await click('Cohorts');
  await click('January intake');
  await click('Monday delivery');
  await click('Planning template');
  await page.getByRole('heading', { name: 'Pinned curriculum v1' }).waitFor();
  assert.equal(await page.getByText('Reflect on progress', { exact: false }).count(), 0);
  await page.waitForURL(url => url.searchParams.has('programme'));
  await page.reload();
  await page.getByText(/No programmes to show/).waitFor();
  await page.goto(routeRoot + '/legacy');
  await page.getByText('Save your changes here. Review and publish separately when ready.', { exact: true }).waitFor();
  assert.deepEqual(catalogueRequests, []);
  console.log(`[${layout}] Full interactive preview workflow passed. Setup and API 404 displayed separately. Preview link, data reset on reload and internal legacy route passed. Zero preview catalogue requests.`);

  const base = '/enrolment_api/programme-structure/catalog/';
  const metadata = { source_system: 'manual', archived_at: null, external_id: '', revision: 1 };
  const programme = {
    ...metadata, id: 'fixture-programme', name: 'Schedule fixture', code: 'FIXTURE', status: 'draft', level: 4,
    total_cost: '100.00', epa_cost: '20.00', description: '', epao: '',
    lecture_hours: '2.50', funding_band_maximum: null,
  };
  const cohort = {
    ...metadata, id: 'fixture-cohort', name: 'Schedule cohort', start_date: '2027-01-01', training_end_date: '2027-12-31',
    epa_period: 0, epa_unit: 'months', apprenticeship_end_date: '2027-12-31', duration_days: 365,
    training_duration_months: 12, apprenticeship_duration_months: 12,
  };
  const group = {
    ...metadata, id: 'fixture-group', name: 'Schedule group', cohort_id: cohort.id, coach_id: null,
    weekday: 1, start_time: '17:30:15', end_time: '18:00:38',
  };
  const scope = base + `programmes/${programme.id}/`;
  const groups = [group];
  const creates = [];
  const updates = [];
  const cohortUpdates = [];
  const fixtureTutor = { id: 'fixture-tutor', name: 'Fixture Tutor', email: 'fixture-tutor@example.test' };
  const templates = [];
  const deliveries = [];
  const scheduled = [];
  const deliveryUpdates = [];
  liveCatalogue = (route, path) => {
    if (path === base + 'staff/') return route.fulfill({ json: { coaches: [], tutors: [fixtureTutor] } });
    if (path === base + 'programmes/') return route.fulfill({ json: { programmes: [
      { ...programme, counts: { cohorts: 1, groups: groups.length, scheduled_modules: deliveries.length } },
    ] } });
    if (path === scope) return route.fulfill({ json: {
      programme, cohorts: [cohort], groups, modules: templates, deliveries, ksbs: [],
    } });
    if (path === scope + 'deliveries/' && route.request().method() === 'POST') {
      const values = route.request().postDataJSON();
      scheduled.push(values);
      const pinned = { id: 'fixture-pin', number: 1 };
      const draft = values.new_template;
      const template = {
        ...draft, weeks: Number(draft.weeks), lectures: Number(draft.lectures),
        reviews: draft.reviews == null ? null : Number(draft.reviews), books: draft.books == null ? null : Number(draft.books),
      };
      templates.push({ ...metadata, ...template, id: 'inline-fixture-template', current_revision: pinned });
      const delivery = {
        ...metadata, id: 'inline-fixture-delivery', group_id: values.group_id, module_id: templates[0].id,
        start_date: values.start_date, end_date: values.end_date, end_date_override: values.end_date, tutor_id: values.tutor_id,
        curriculum: { ...template, ...pinned, ksbs: [] },
      };
      deliveries.push(delivery);
      return route.fulfill({ json: delivery, status: 201 });
    }
    if (path === scope + 'deliveries/inline-fixture-delivery/' && route.request().method() === 'PATCH') {
      const values = route.request().postDataJSON();
      deliveryUpdates.push(values);
      Object.assign(deliveries[0], values, {
        end_date: values.end_date ?? '2027-01-16', end_date_override: values.end_date,
        revision: deliveries[0].revision + 1,
      });
      return route.fulfill({ json: deliveries[0] });
    }
    if (path === scope + `cohorts/${cohort.id}/` && route.request().method() === 'PATCH') {
      cohortUpdates.push(route.request().postDataJSON());
      Object.assign(cohort, {
        training_duration_months: 2, apprenticeship_duration_months: 2, duration_days: 59,
        training_end_date: '2027-02-28', apprenticeship_end_date: '2027-02-28', revision: cohort.revision + 1,
      });
      return route.fulfill({ json: cohort });
    }
    if (path === scope + 'groups/' && route.request().method() === 'POST') {
      const values = route.request().postDataJSON();
      creates.push(values);
      const created = { ...metadata, ...values, id: 'new-group', weekday: Number(values.weekday) };
      groups.push(created);
      return route.fulfill({ json: created, status: 201 });
    }
    if (path === scope + `groups/${group.id}/` && route.request().method() === 'PATCH') {
      const values = route.request().postDataJSON();
      updates.push(values);
      Object.assign(group, values, { revision: group.revision + 1 });
      return route.fulfill({ json: group });
    }
    return route.abort();
  };
  await page.setViewportSize(viewport);
  await openProgrammeStructure();
  const search = page.getByRole('searchbox', { name: 'Search programmes' });
  await search.fill('absent programme');
  await page.getByText('No programmes match your search.', { exact: true }).waitFor();
  await search.fill('FIXTURE');
  await page.getByRole('button', { name: programme.name, exact: true }).waitFor();
  await search.clear();
  assert.ok(await page.getByRole('button', { name: 'Add programme' }).isEnabled());
  assert.equal(await page.getByRole('heading', { level: 1 }).count(), 1);
  const filters = page.getByRole('group', { name: 'Programme status' });
  await filters.getByRole('button', { name: /^active$/i }).click();
  await page.getByText('No programmes match this status.', { exact: true }).waitFor();
  await filters.getByRole('button', { name: /^draft$/i }).click();
  await page.getByText('0 scheduled modules', { exact: true }).waitFor();
  if (mobile) await verifyMobile();
  await capture(`programme-list-${layout}.png`);
  assert.equal(await page.getByRole('link', { name: /legacy programme structure|preview catalogue|interactive preview/i }).count(), 0);
  await click(programme.name);
  await click('Cohorts');
  await click(cohort.name);
  await click(`Edit ${cohort.name}`);
  assert.equal(await cohortDuration.inputValue(), '12');
  await cohortDuration.fill('2');
  assert.equal(await page.getByLabel('Training end date', { exact: true }).inputValue(), '2027-02-28');
  await click('Update cohort');
  await page.getByRole('heading', { name: cohort.name, exact: true }).waitFor();
  assert.deepEqual(cohortUpdates, [{ name: cohort.name, start_date: '2027-01-01', training_duration_months: '2', epa_period: '0' }]);
  await click(group.name);
  await page.getByText('Monday · 17:30–18:00', { exact: true }).waitFor();
  await click(`Edit ${group.name}`);
  assert.equal(await page.getByLabel('Start time', { exact: true }).inputValue(), '17:30');
  assert.equal(await endInput.inputValue(), '18:00');
  await fill('Group name', 'Renamed group');
  await click('Update group');
  await page.getByRole('heading', { name: 'Renamed group', exact: true }).waitFor();
  assert.deepEqual(updates[0], { name: 'Renamed group', weekday: '1' });
  assert.equal(group.start_time, '17:30:15');
  assert.equal(group.end_time, '18:00:38');
  await click('Edit Renamed group');
  await click('Choose end time');
  await select('Choose end time hour', '21');
  await fill('End time minute', '42');
  await click('Set end time');
  await click('Update group');
  await page.getByText('Monday · 17:30–21:42', { exact: true }).waitFor();
  assert.deepEqual(updates[1], { name: 'Renamed group', weekday: '1', end_time: '21:42' });
  await click(cohort.name);
  await click('Add group');
  await fill('Group name', 'Saturday group');
  await select('Day of week', 'Saturday');
  await click('Choose start time');
  await select('Choose start time hour', '13');
  await select('Choose start time minute', '47');
  await click('Set start time');
  await fill('End time', '14:09');
  await click('Create group');
  await page.getByText('Saturday · 13:47–14:09', { exact: true }).waitFor();
  assert.deepEqual(creates[0], {
    name: 'Saturday group', cohort_id: cohort.id, coach_id: null, weekday: '6', start_time: '13:47', end_time: '14:09',
  });
  const directLink = page.url();
  await page.goto(directLink);
  await page.getByRole('heading', { name: 'Saturday group', exact: true }).waitFor();
  await click('Add module');
  assert.ok(await page.getByRole('radio', { name: 'Create new module', exact: true }).isChecked());
  await fill('Module name', 'Inline live fixture');
  await fill('Number of sessions', '1');
  await fill('Start date', '2027-01-10');
  await fill('End date', '2027-01-12');
  await chooseFromSearch('Tutor', 'Search tutor by name or email', fixtureTutor.email, /Fixture Tutor/);
  await click('Add module');
  await page.getByRole('heading', { name: 'Inline live fixture', exact: true }).waitFor();
  assert.deepEqual(scheduled, [{
    group_id: 'new-group', cohort_id: cohort.id, tutor_id: fixtureTutor.id,
    start_date: '2027-01-10', end_date: '2027-01-12',
    new_template: { name: 'Inline live fixture', weeks: '1', lectures: '1', reviews: null, books: null, weekly_topics: [], ksb_ids: [] },
  }]);
  await click('Edit Inline live fixture');
  assert.equal(await page.getByLabel('Start date', { exact: true }).inputValue(), '2027-01-10');
  await fill('End date', '2027-01-14');
  await click('Update scheduled module');
  await page.getByText('14 Jan 2027', { exact: true }).waitFor();
  assert.deepEqual(deliveryUpdates, [{
    start_date: '2027-01-10', end_date: '2027-01-14', module_id: templates[0].id, module_revision_id: 'fixture-pin',
  }]);
  await click('Edit Inline live fixture');
  await click('Reset end date to automatic');
  assert.equal(await page.getByLabel('End date', { exact: true }).inputValue(), '2027-01-16');
  await click('Update scheduled module');
  await page.getByText('16 Jan 2027', { exact: true }).waitFor();
  assert.equal(deliveryUpdates[1].end_date, null);
  await click('Back to Saturday group');
  await click('Back to Schedule cohort');
  await page.getByRole('heading', { name: 'Schedule cohort', exact: true }).waitFor();
  assert.deepEqual(pageErrors, [], 'The local workflow should run without uncaught browser errors');
  console.log(`[${layout}] Catalogue flow passed with intercepted API fixtures, including inline template creation, delivery date edits, and direct record links.`);
  console.log(screenshots);
} finally {
  await browser.close();
}
