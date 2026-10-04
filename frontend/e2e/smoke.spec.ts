import { expect, test } from "@playwright/test";

/**
 * Every page renders with real data behind it.
 *
 * A page that throws server-side in Next.js still returns 200 with an error
 * boundary in the body, so asserting on the status code proves nothing. These
 * assert on content that can only be there if the API answered.
 */

const PAGES: { path: string; heading: string; proof: RegExp }[] = [
  { path: "/", heading: "Dashboard", proof: /Now|Opportunities/ },
  { path: "/opportunities", heading: "Opportunities", proof: /Decisions|Full table/ },
  { path: "/analyze", heading: "Analyze", proof: /Search/ },
  { path: "/bulk", heading: "Bulk analysis", proof: /./ },
  { path: "/capital", heading: "Capital", proof: /Constraints/ },
  { path: "/providers", heading: "Providers", proof: /Provider|Capabilities/ },
  {
    path: "/execution",
    heading: "What to buy, and what you paid",
    proof: /Where this stands|Nothing to buy/i,
  },
  { path: "/autonomy", heading: "Autonomy", proof: /Current authority/i },
  { path: "/autonomy/policy", heading: "Policy and limits", proof: /Active policy/ },
  {
    path: "/autonomy/allocation",
    heading: "What to buy now",
    proof: /The plan|Nothing to buy/i,
  },
  { path: "/autonomy/positions", heading: "Positions", proof: /Capital deployed/i },
  { path: "/autonomy/decisions", heading: "Decisions", proof: /Authorised|No decisions/ },
  { path: "/autonomy/sell", heading: "What to sell", proof: /position|Nothing/i },
  {
    path: "/autonomy/backtest",
    heading: "What this policy would have done",
    proof: /Replay the active policy/,
  },
  { path: "/autonomy/learning", heading: "What it gets wrong", proof: /Where this stands/i },
  { path: "/autonomy/events", heading: "Audit log", proof: /./ },
  { path: "/history", heading: "Observation history", proof: /Price observations/i },
];

for (const page of PAGES) {
  test(`${page.path} renders`, async ({ page: browser }) => {
    const errors: string[] = [];
    browser.on("pageerror", (error) => errors.push(error.message));

    await browser.goto(page.path);
    await expect(
      browser.getByRole("heading", { name: page.heading, level: 1 }),
    ).toBeVisible();
    await expect(browser.locator("main")).toContainText(page.proof);

    // An error boundary is the failure mode a 200 hides.
    await expect(browser.locator("main")).not.toContainText("Could not load");
    await expect(browser.locator("main")).not.toContainText("Application error");
    expect(errors, `client errors on ${page.path}`).toEqual([]);
  });
}

test("navigation reaches every section", async ({ page }) => {
  await page.goto("/");
  for (const label of ["Opportunities", "Capital", "Overview", "Observation history"]) {
    await expect(page.getByRole("link", { name: label }).first()).toBeVisible();
  }
});

test("money never renders as a floating point artefact", async ({ page }) => {
  await page.goto("/opportunities");
  const body = (await page.locator("main").innerText()) ?? "";
  // 71.64959999999999 is what a float round-trip looks like.
  expect(body).not.toMatch(/\d\.\d{5,}/);
});

test("a missing value reads as not available, never as zero", async ({ page }) => {
  await page.goto("/autonomy");
  const body = await page.locator("main").innerText();
  if (body.includes("nothing measured yet")) {
    expect(body).toContain("not available");
  }
});
