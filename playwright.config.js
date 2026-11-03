const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "tests/ui",
  timeout: 30000,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: "http://127.0.0.1:4173",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "python -m http.server 4173 --bind 127.0.0.1 --directory storchcam/web",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: !process.env.CI,
  },
});
