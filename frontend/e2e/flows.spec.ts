import { expect, test } from "@playwright/test";

/**
 * The flows, driven the way a person drives them.
 *
 * Every bug this file exists to catch was invisible to a rendering test: a
 * missing CORS verb that made a save silently do nothing, a threshold formatted
 * by the wrong unit, a message that said the opposite of what happened. A form
 * that renders is not a form that works.
 */

test.describe("theme", () => {
  test("the choice survives a reload", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Dark theme" }).first().click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

    await page.reload();
    // The pre-paint script has to restore it, or every load flashes the wrong theme.
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

    await page.getByRole("button", { name: "Light theme" }).first().click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  });
});

test.describe("opportunities", () => {
  test("filters narrow the list and survive in the URL", async ({ page }) => {
    await page.goto("/opportunities");
    await page.getByRole("button", { name: "Check", exact: true }).click();
    await expect(page).toHaveURL(/recommendation=review/);
    await expect(page.locator("main")).toContainText(/\d+ matches/i);
  });

  test("the full table and the decision view are both reachable", async ({ page }) => {
    await page.goto("/opportunities");
    await page.getByRole("link", { name: "Full table" }).click();
    await expect(page).toHaveURL(/view=full/);
    await expect(page.locator("table")).toBeVisible();
  });

  test("a record shows the ladder, the evidence and the working", async ({ page }) => {
    await page.goto("/opportunities");
    await page.locator("a[href^='/opportunities/']").first().click();
    await expect(page.locator("main")).toContainText("The call");
    await expect(page.locator("main")).toContainText("The numbers, in order");
    await expect(page.locator("main")).toContainText("Gross spread");
    await expect(page.locator("main")).toContainText("How solid is this gap");

    // The ladder has to reconcile on screen, not only in the API.
    const text = await page.locator("main").innerText();
    const money = (label: string) => {
      const match = text.match(new RegExp(`${label}[^$]*\\$([\\d,]+\\.\\d\\d)`));
      const captured = match?.[1];
      return captured ? Number(captured.replace(/,/g, "")) : NaN;
    };
    const total = money("Total cost");
    const fees = money("Marketplace fees");
    expect(Number.isFinite(total) && Number.isFinite(fees)).toBe(true);
    expect(total).toBeGreaterThan(fees);
  });

  test("the score adds up in front of the reader", async ({ page }) => {
    await page.goto("/opportunities");
    await page.locator("a[href^='/opportunities/']").first().click();
    await page.getByText("Score breakdown").click();
    await expect(page.locator("main")).toContainText("How it was scored");
    // Each component states the arithmetic it performed, with real numbers.
    await expect(page.locator("main")).toContainText(/x 100|curve|scores/);
  });
});

test.describe("autonomy", () => {
  // Autonomy state is global to the organisation, so anything this file changes
  // is put back. Otherwise a failure mid-suite leaves the next run starting from
  // a state no test established.
  test.afterAll(async ({ request, baseURL }) => {
    void baseURL;
    await request
      .post("http://localhost:8000/api/v1/autonomy/emergency-stop", {
        data: { active: false, reason: "End to end suite finished" },
      })
      .catch(() => undefined);
  });

  test("the dashboard states the authority before anything else", async ({ page }) => {
    await page.goto("/autonomy");
    await expect(page.locator("main")).toContainText(/current authority/i);
    await expect(page.locator("main")).toContainText(/Level \d/);
    await expect(page.locator("main")).toContainText(/authorised/i);
  });

  test("breaker thresholds are formatted by their own unit", async ({ page }) => {
    await page.goto("/autonomy");
    const text = await page.locator("main").innerText();
    // "0.0500 limit" is what the wrong unit looks like.
    expect(text).toMatch(/\$[\d,]+\.\d\d limit/);
    expect(text).toMatch(/[\d.]+% limit/);
    expect(text).not.toMatch(/0\.\d{4} limit/);
  });

  test("a policy change writes a new version and keeps the old one", async ({ page }) => {
    await page.goto("/autonomy/policy");
    const before = (await page.locator("main").innerText()).match(
      /Active policy (v[\d.]+)/,
    )?.[1];
    expect(before).toBeTruthy();

    const field = page
      .locator("div")
      .filter({ hasText: /^Maximum inventory age/ })
      .locator("input")
      .first();
    const current = await field.inputValue();
    await field.fill(String(Number(current) + 1));

    await page.getByRole("button", { name: /Save \d+ change/ }).click();
    // This is the assertion the missing CORS verb would have failed.
    await expect(page.locator("main")).toContainText(/Saved as v[\d.]+/);

    await page.reload();
    const after = (await page.locator("main").innerText()).match(
      /Active policy (v[\d.]+)/,
    )?.[1];
    expect(after).not.toEqual(before);
    await expect(page.locator("main")).toContainText(before!);
  });

  test("the emergency stop takes effect and is reversible", async ({ page }) => {
    page.on("dialog", (dialog) => dialog.accept("End to end check"));
    await page.goto("/autonomy");

    // The stop is global state, so the test establishes its own starting point
    // rather than assuming one. A suite that depends on the order it happens to
    // run in is a suite that fails for reasons nobody can reproduce.
    const resume = page.getByRole("button", { name: "Resume autonomy" });
    if (await resume.isVisible().catch(() => false)) {
      await resume.click();
      await expect(page.locator("main")).not.toContainText(/autonomous activity stopped/i);
    }

    await page.getByRole("button", { name: "Stop autonomous activity" }).click();
    await expect(page.locator("main")).toContainText(/autonomous activity stopped/i);
    // It stops spending. It does not stop analysis or unwind anything.
    await expect(page.locator("main")).toContainText(/current authority/i);

    await page.getByRole("button", { name: "Resume autonomy" }).click();
    await expect(page.locator("main")).not.toContainText(/autonomous activity stopped/i);
  });

  test("a decision records both the outcome and the stage that objected", async ({
    page,
  }) => {
    await page.goto("/autonomy/decisions");
    const rows = page.locator("tbody tr");
    if ((await rows.count()) === 0) test.skip();

    await expect(page.locator("main")).toContainText(/scout: proceed/);
    await page.locator("a[href^='/autonomy/decisions/']").first().click();
    await expect(page.locator("main")).toContainText("What each stage concluded");
    await expect(page.locator("main")).toContainText("The policy it was decided under");
    await expect(page.locator("main")).toContainText(
      "Changing the policy today cannot rewrite",
    );
  });

  test("the gate reports without ever promoting", async ({ page }) => {
    await page.goto("/autonomy");
    const before = (await page.locator("main").innerText()).match(/Level (\d)/)?.[1];
    await page.reload();
    const after = (await page.locator("main").innerText()).match(/Level (\d)/)?.[1];
    expect(after).toEqual(before);
    await expect(page.locator("main")).toContainText("nothing here promotes itself");
  });
});

test.describe("history", () => {
  test("real and simulated are counted separately", async ({ page }) => {
    await page.goto("/history");
    await expect(page.locator("main")).toContainText(/real market records/i);
    await expect(page.locator("main")).toContainText(/simulated/i);
  });

  test("a listing can be observed, polled and stopped", async ({ page }) => {
    page.on("dialog", (dialog) => dialog.accept());
    await page.goto("/history");

    const countOf = async () =>
      Number(
        (await page.locator("main").innerText()).match(/Under observation\s*(\d+)/i)?.[1] ??
          "0",
      );
    const before = await countOf();

    await page.getByRole("button", { name: "Observe a listing" }).click();
    await page.getByPlaceholder("B09XS7JWHH").fill("B0863TXGM3");
    await page.getByRole("button", { name: "Start observing" }).click();
    await expect.poll(countOf).toBe(before + 1);

    await page.getByRole("button", { name: "Run one pass now" }).click();
    await expect(page.locator("main")).toContainText(/Polled \d+, wrote \d+ observation/);

    // Read the total only once the refresh has settled: the message appears
    // before the server component repaints, so reading immediately catches the
    // pre-refresh number and makes the next assertion a coin toss.
    const observationsOf = async () =>
      Number(
        (
          (await page.locator("main").innerText()).match(
            /Price observations\s*([\d,]+)/i,
          )?.[1] ?? "0"
        ).replace(/,/g, ""),
      );
    await expect.poll(observationsOf).toBeGreaterThan(0);
    const settled = await observationsOf();

    // Stopping keeps everything already observed: a gap in a history should be
    // explained by a row saying when observation stopped, not by a deletion.
    await page.locator("tbody tr").last().getByRole("button", { name: "Stop" }).click();
    await expect.poll(countOf).toBe(before);
    expect(await observationsOf()).toBeGreaterThanOrEqual(settled);
  });
});

test.describe("backtest", () => {
  test("a replay reports its caveats above the return figure", async ({ page }) => {
    await page.goto("/autonomy/backtest");
    await page.getByRole("button", { name: "Run the replay" }).click();

    const main = page.locator("main");
    await expect(main).toContainText("What the replay found", { timeout: 30_000 });
    await expect(main).toContainText("What this does not prove");
    await expect(main).toContainText("what the market would have allowed");

    // The ordering is the point. A return figure read without its selling
    // assumption is what this page exists to prevent, so the caveats have to
    // come first in the document, not folded away underneath.
    const body = await main.innerText();
    const caveats = body.indexOf("What this does not prove");
    const figure = body.search(/REALISED PROFIT|Nothing was bought/i);
    expect(caveats).toBeGreaterThan(-1);
    expect(figure).toBeGreaterThan(caveats);
  });

  test("refusals are one row per cause, not one per day", async ({ page }) => {
    await page.goto("/autonomy/backtest");
    await page.getByRole("button", { name: "Run the replay" }).click();
    await expect(page.locator("main")).toContainText("What the replay found", {
      timeout: 30_000,
    });

    const refusals = page.locator("section", { hasText: "What it refused, and why" });
    if ((await refusals.count()) === 0) return;

    // A day count above one is the proof that grouping happened: the same
    // candidate refused for the same reason on consecutive days is one row.
    await expect(refusals.first().locator("tbody tr").first()).toBeVisible();
    const rows = await refusals.first().locator("tbody tr").allInnerTexts();
    const days = rows.map((row) => Number(row.split("\t")[2] ?? "0"));
    expect(Math.max(...days)).toBeGreaterThan(1);
  });
});

test.describe("allocation", () => {
  test("the plan shows what it left out as prominently as what it funded", async ({
    page,
  }) => {
    await page.goto("/autonomy/allocation");
    const main = page.locator("main");

    await expect(main).toContainText("The plan");
    await expect(main).toContainText("Deliberately uncommitted");

    // Capital left idle is a decision, not a shortfall, and the page has to say
    // so: a plan that only showed what it bought would be a sales pitch.
    await expect(main).toContainText(/Ranked and not funded|Refused before ranking/);
    await expect(main).toContainText("profit per dollar");
  });

  test("the budget requested can never raise the policy ceiling", async ({ page }) => {
    await page.goto("/autonomy/allocation?budget=999999");
    const body = await page.locator("main").innerText();
    const budget = body.match(/BUDGET\s*\$([\d,]+)/i)?.[1]?.replace(/,/g, "");
    expect(Number(budget ?? "0")).toBeLessThan(999999);
  });

  test("reading a plan opens nothing", async ({ page }) => {
    await page.goto("/autonomy/positions");
    const before = (await page.locator("main").innerText()).match(
      /Capital deployed\s*\$([\d,.]+)/i,
    )?.[1];

    await page.goto("/autonomy/allocation");
    await expect(page.locator("main")).toContainText("The plan");

    await page.goto("/autonomy/positions");
    const after = (await page.locator("main").innerText()).match(
      /Capital deployed\s*\$([\d,.]+)/i,
    )?.[1];
    expect(after).toBe(before);
  });
});

test.describe("execution", () => {
  test("the page states the boundary rather than implying automation", async ({ page }) => {
    await page.goto("/execution");
    const main = page.locator("main");

    await expect(main).toContainText("Placing the order is yours");
    await expect(main).toContainText("does not place retail orders");
    await expect(main).toContainText("no purchasing bot");

    // Nothing that would let somebody believe the product buys for them.
    const body = await main.innerText();
    expect(body.toLowerCase()).not.toContain("buy it for me");
    expect(body.toLowerCase()).not.toContain("auto-purchase");
  });

  test("an authorised instruction can be closed out as not bought", async ({ page }) => {
    await page.goto("/execution");
    const main = page.locator("main");
    const pending = await main.innerText();
    if (pending.includes("Nothing to buy right now")) {
      // No instruction outstanding in this environment. The form's behaviour is
      // covered by the backend suite; skipping beats fabricating a purchase in
      // somebody's real queue.
      test.skip();
      return;
    }

    await page.getByRole("button", { name: "Record what I bought" }).first().click();
    await page.getByLabel("Units bought").fill("0");
    await expect(main).toContainText("releases the");
    await page.getByRole("button", { name: "Record: nothing bought" }).click();
    await expect(main).toContainText(/Closed out\s*1|not executed/i);
  });
});
