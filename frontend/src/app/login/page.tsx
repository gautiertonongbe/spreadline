import { LoginForm } from "@/components/login-form";
import { Logo } from "@/components/logo";

export const dynamic = "force-dynamic";

/**
 * The only page that renders without a session.
 *
 * Deliberately bare: no navigation, no data, nothing that hints at what is
 * behind it. A sign-in page that lists the sections of the product is a sign-in
 * page that tells an anonymous visitor what the product does with money.
 */
export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; reason?: string }>;
}) {
  const params = await searchParams;
  const next = params.next && params.next.startsWith("/") ? params.next : "/";

  return (
    <div className="flex min-h-screen items-center justify-center px-6 py-12">
      <div className="w-full max-w-[380px]">
        <div className="mb-8">
          <Logo />
        </div>
        <h1 className="display text-[1.5rem] font-medium leading-tight tracking-tight text-primary">
          Sign in
        </h1>
        <p className="mt-1.5 text-[0.8125rem] leading-relaxed text-muted">
          Spreadline decides what to do with capital. It does not have a public sign-up:
          accounts are created from the command line or by an owner.
        </p>

        {params.reason === "expired" && (
          <div className="mt-5 rounded border border-review/25 bg-review/10 px-3 py-2 text-xs leading-relaxed text-review">
            That session ended. Sign in again to continue.
          </div>
        )}

        <div className="mt-6">
          <LoginForm next={next} />
        </div>

        <p className="mt-8 text-2xs leading-relaxed text-faint">
          No account yet? Create the first one with{" "}
          <code className="rounded bg-raised px-1 py-0.5">
            python -m scripts.create_user --email you@example.com
          </code>{" "}
          from the backend directory. The password is typed at a prompt rather than passed
          as an argument, so it stays out of the shell history.
        </p>
      </div>
    </div>
  );
}
