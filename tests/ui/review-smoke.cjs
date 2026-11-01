const { chromium } = require(process.env.MV_PLAYWRIGHT_PATH);

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.MV_BROWSER_EXECUTABLE });
  try {
    const page = await browser.newPage();
    await page.goto(process.env.MV_REVIEW_URL);
    await page.locator('#review').waitFor({ state: 'visible' });
    if (await page.locator('#projectName').innerText() !== 'Test') throw new Error('Project name missing');
    await page.locator('#items button').first().click();
    const width = page.getByLabel('Width', { exact: true });
    await width.fill('550');
    await width.press('Tab');
    const [saved] = await Promise.all([
      page.waitForResponse(response => response.url().endsWith('/api/model') && response.request().method() === 'POST'),
      page.locator('.edit-save').click(),
    ]);
    if (!saved.ok()) throw new Error('Save request failed');
    await page.reload();
    await page.locator('#review').waitFor({ state: 'visible' });
    await page.locator('#items button').first().click();
    if (await page.getByLabel('Width', { exact: true }).inputValue() !== '550') throw new Error('Saved width did not persist');
    await page.locator('#topView').click();
    if (await page.locator('#topView').getAttribute('aria-pressed') !== 'true') throw new Error('Top view did not activate');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
