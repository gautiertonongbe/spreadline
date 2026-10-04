"use client";

/**
 * The sign-in form.
 *
 * It repeats whatever the server said and adds nothing. In particular it never
 * distinguishes "no such account" from "wrong password", because the server
 * deliberately does not: an error message is a place where an authentication
 * layer leaks what it knows.
 */

import { useState } from "react";

import { Button, ErrorState } from "@/components/ui";
import { API_URL, authApi } from "@/lib/api";

export function LoginForm({ next }: { next: string }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await authApi.login({ email: email.trim(), password });

      // A 200 from the login route is not proof of a session. If the API is on
      // a different site from this page, the browser accepts the response and
      // discards the cookie, and the only symptom is bouncing back to this form
      // with nothing said. Ask once, so the cause is named rather than guessed.
      try {
        await authApi.me();
      } catch {
        setError(
          `Signed in, but the browser did not keep the session. The API at ${API_URL} ` +
            `is on a different site from this page at ${window.location.origin}, so the ` +
            "session cookie is discarded. Serve both from the same site, or put the API " +
            "behind the same origin.",
        );
        setBusy(false);
        return;
      }

      // A full navigation rather than a client push: every page renders on the
      // server, and the server needs the cookie the browser has just been given.
      window.location.assign(next);
    } catch (caught) {
      setError((caught as Error).message);
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-3">
      {error && <ErrorState message={error} />}
      <label className="block">
        <span className="label">Email</span>
        <input
          type="email"
          autoComplete="username"
          autoFocus
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          className="mt-1.5 w-full rounded border border-border bg-canvas px-3 py-2 text-sm text-primary"
        />
      </label>
      <label className="block">
        <span className="label">Password</span>
        <input
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          className="mt-1.5 w-full rounded border border-border bg-canvas px-3 py-2 text-sm text-primary"
        />
      </label>
      <Button
        type="submit"
        tone="accent"
        disabled={busy || !email.trim() || !password}
        className="w-full"
      >
        {busy ? "Signing in" : "Sign in"}
      </Button>
    </form>
  );
}
