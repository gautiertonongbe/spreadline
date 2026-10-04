import { expect, test as setup } from "@playwright/test";

/**
 * Sign in once, and hand the session to every other test.
 *
 * Through the real form rather than by injecting a cookie: the sign-in page is
 * the one screen every user meets first, and a suite that skipped past it would
 * never notice the day it stopped working.
 *
 * Credentials come from the environment, with no default: a password literal in
 * a repository is a password in the repository, whatever the account is for.
 * Create the account with `make account` and put E2E_EMAIL and E2E_PASSWORD in
 * `.env`, which is gitignored.
 */
export const STORAGE_STATE = "e2e/.auth/session.json";

const EMAIL = process.env.E2E_EMAIL;
const PASSWORD = process.env.E2E_PASSWORD;

setup("sign in", async ({ page }) => {
  // No default. A password literal in a repository is a password in the repository,
  // whatever the account is for.
  if (!EMAIL || !PASSWORD) {
    throw new Error(
      "Set E2E_EMAIL and E2E_PASSWORD before running this suite. Create the account " +
        "with `make account`, then put the credentials in .env, which is gitignored.",
    );
  }

  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();

  // The dashboard, not the form. A failed sign-in leaves the form on screen
  // with an error, and every later test would then fail somewhere less obvious.
  await expect(
    page.getByRole("heading", { name: "Dashboard", level: 1 }),
  ).toBeVisible({ timeout: 20_000 });

  await page.context().storageState({ path: STORAGE_STATE });
});
