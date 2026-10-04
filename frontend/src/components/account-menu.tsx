"use client";

/**
 * Who is signed in, and how to stop being.
 *
 * Sign-out is a real request, not a cleared cookie: the session has to end on
 * the server, or "signed out" means the token in somebody's hands still works.
 */

import { useState } from "react";

import { Button } from "@/components/ui";
import { authApi, type Account } from "@/lib/api";

export function AccountMenu({
  account,
  compact = false,
}: {
  account: Account;
  compact?: boolean;
}) {
  const [busy, setBusy] = useState(false);

  const signOut = async () => {
    setBusy(true);
    try {
      await authApi.logout();
    } catch {
      // Even if the call fails the right thing is to leave: the next request
      // will be refused, and staying on a page that cannot load is worse.
    }
    window.location.assign("/login");
  };

  if (compact) {
    return (
      <Button size="small" disabled={busy} onClick={() => void signOut()}>
        {busy ? "Signing out" : "Sign out"}
      </Button>
    );
  }

  return (
    <div className="space-y-2">
      <div className="min-w-0">
        <div className="label">Signed in</div>
        <div className="truncate text-2xs text-secondary" title={account.email}>
          {account.full_name || account.email}
        </div>
        {account.full_name && (
          <div className="truncate text-2xs text-faint">{account.email}</div>
        )}
      </div>
      <Button size="small" disabled={busy} onClick={() => void signOut()}>
        {busy ? "Signing out" : "Sign out"}
      </Button>
    </div>
  );
}
