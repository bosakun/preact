import { defineConfig } from '@playwright/test';
const port = process.env.PREACT_E2E_PORT || '8000';
const baseURL = `http://127.0.0.1:${port}`;
export default defineConfig({
  testDir: './e2e', timeout: 30000, use: { baseURL, viewport: { width: 1440, height: 1100 } },
  webServer: { command: `../.venv/bin/python -m uvicorn preact.service.app:app --host 127.0.0.1 --port ${port}`, url: `${baseURL}/api/health`, reuseExistingServer: !process.env.CI },
});
