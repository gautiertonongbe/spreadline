import { defineConfig } from "@playwright/test";

/**
 * End-to-end tests against a running stack.
 *
 * Deliberately not mocked. Every bug these tests exist to catch was a wiring
 * bug: a missing CORS verb, a form that rendered and did nothing, a number
 * formatted by the wrong unit. None of those are visible to a component test
 * with a stubbed fetch, which is exactly why this suite drives the real API.
 *
 *   make dev   (API on :8000, web on :3200)
 *   npx playwright test
 */
const STORAGE_STATE = "e2e/.auth/session.json";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3200",
    trace: "retain-on-failure",
  },
  projects: [
    // Signing in is its own project so it runs once and every other test starts
    // already signed in. A suite that logged in per test would spend most of its
    // time on a form it is not testing.
    { name: "setup", testMatch: /auth\.setup\.ts/ },
    {
      name: "signed in",
      testIgnore: /auth\.setup\.ts/,
      dependencies: ["setup"],
      use: { storageState: STORAGE_STATE },
    },
  ],
});
