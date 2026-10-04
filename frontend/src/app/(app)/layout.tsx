import { redirect } from "next/navigation";

import { AccountMenu } from "@/components/account-menu";
import { DataSourceBanner } from "@/components/data-source-banner";
import { Logo } from "@/components/logo";
import { Nav } from "@/components/nav";
import { ThemeToggle } from "@/components/theme-toggle";
import { authApi, isNotAuthenticated, type Account } from "@/lib/api";

export const dynamic = "force-dynamic";

/**
 * Everything behind the sign-in.
 *
 * The session is checked here rather than in each page, so a page cannot be
 * added without the check. The middleware turns away a request with no cookie
 * before it reaches this point; this is what catches the cookie that exists and
 * is no longer good, which the middleware cannot tell apart from one that is.
 */
export default async function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  let account: Account;
  try {
    account = (await authApi.me()).user;
  } catch (error) {
    if (isNotAuthenticated(error)) redirect("/login?reason=expired");
    throw error;
  }

  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-60 shrink-0 border-r border-border bg-surface lg:flex lg:flex-col">
        <div className="px-6 pb-5 pt-6">
          <a href="/" className="block">
            <Logo />
          </a>
          <div className="rule-fade mt-4" />
        </div>
        <Nav />
        <div className="mt-auto space-y-4 border-t border-hairline px-6 py-5">
          <ThemeToggle />
          <DataSourceBanner variant="sidebar" />
          <AccountMenu account={account} />
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between gap-4 border-b border-border bg-surface px-6 py-3 lg:hidden">
          <a href="/" className="block">
            <Logo />
          </a>
          <div className="flex items-center gap-3">
            <ThemeToggle compact />
            <DataSourceBanner variant="compact" />
            <AccountMenu account={account} compact />
          </div>
        </header>
        <div className="border-b border-hairline lg:hidden">
          <Nav orientation="horizontal" />
        </div>
        <main className="min-w-0 flex-1 px-6 py-7 lg:px-9 lg:py-9">
          {children}
        </main>
      </div>
    </div>
  );
}
