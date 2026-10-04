import { expect, test } from "@playwright/test";

/**
 * The door, driven by a browser with no session.
 *
 * The rest of the suite starts signed in, which is exactly why these tests
 * throw the session away first: the assertion that matters most is what a
 * stranger sees, and a signed-in fixture hides it.
 */
test.describe("signed out", () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test("a protected page sends you to sign in and remembers where you were", async ({
    page,
  }) => {
    await page.goto("/autonomy/positions");
    await expect(page).toHaveURL(/\/login\?next=%2Fautonomy%2Fpositions/);
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  });

  test("the sign-in page shows nothing about the product's data", async ({ page }) => {
    await page.goto("/login");
    const body = await page.locator("body").innerText();
    // No navigation, no figures, no product names: a sign-in page that lists
    // the sections tells an anonymous visitor what the product does with money.
    expect(body).not.toContain("Opportunities");
    expect(body).not.toContain("Positions");
    expect(body).not.toMatch(/\$\d/);
  });

  test("a wrong password says nothing about whether the account exists", async ({
    page,
  }) => {
    await page.goto("/login");
    await page.getByLabel("Email").fill("nobody@spreadline.invalid");
    await page.getByLabel("Password").fill("not-the-password");
    await page.getByRole("button", { name: "Sign in" }).click();

    const error = page.locator("main, body").getByText(/not correct/i).first();
    await expect(error).toBeVisible();
    const text = await error.innerText();
    expect(text.toLowerCase()).not.toContain("no such");
    expect(text.toLowerCase()).not.toContain("unknown");
  });
});

test.describe("signing out", () => {
  // Its own session, signed in from scratch. Signing out of the shared one
  // would revoke it on the server and every test that ran afterwards would
  // fail somewhere with no obvious connection to this file.
  test.use({ storageState: { cookies: [], origins: [] } });

  test("ends the session rather than only clearing the tab", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel("Email").fill(process.env.E2E_EMAIL!);
    await page
      .getByLabel("Password")
      .fill(process.env.E2E_PASSWORD!);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("heading", { name: "Dashboard", level: 1 })).toBeVisible();

    // The dashboard heading appears in the server-rendered HTML, so it is on
    // screen before React has attached anything to it. Clicking then does
    // nothing at all, which is a flake in a test and a shrug in real use.
    await page.waitForLoadState("networkidle");

    const signedOut = page.waitForResponse(
      (response) =>
        response.url().includes("/auth/logout") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Sign out" }).first().click();
    await signedOut;
    await expect(page).toHaveURL(/\/login/);

    // Gone on the server, not just in this tab: a protected page has to bounce
    // rather than render from a cookie the browser still happens to hold.
    await page.goto("/autonomy");
    await expect(page).toHaveURL(/\/login/);
  });
});
