"use client";

import { useEffect, useState, type ReactNode } from "react";
import { clsx } from "clsx";

import { THEME_KEY, type Theme } from "@/lib/theme";

/**
 * Light and dark, chosen explicitly, remembered locally.
 *
 * The initial value is not decided here. It is decided before first paint by
 * the inline script in the layout, which reads the stored choice and falls back
 * to the operating system. This component only reports what that script
 * resolved and lets it be changed, which is why it renders neutral until it has
 * mounted: guessing on the server would produce a wrong highlight for one frame
 * and a hydration mismatch for the console.
 */
export function ThemeToggle({ compact = false }: { compact?: boolean }) {
  const [theme, setTheme] = useState<Theme | null>(null);

  useEffect(() => {
    setTheme(document.documentElement.dataset.theme === "dark" ? "dark" : "light");
  }, []);

  const apply = (next: Theme) => {
    document.documentElement.dataset.theme = next;
    try {
      window.localStorage.setItem(THEME_KEY, next);
    } catch {
      // A browser with storage blocked still switches; it just forgets.
    }
    setTheme(next);
  };

  const options: { value: Theme; label: string; icon: ReactNode }[] = [
    {
      value: "light",
      label: "Light",
      icon: (
        <svg viewBox="0 0 16 16" width="13" height="13" fill="none" aria-hidden="true">
          <circle cx="8" cy="8" r="3.1" stroke="currentColor" strokeWidth="1.3" />
          <path
            d="M8 1.2v1.6M8 13.2v1.6M1.2 8h1.6M13.2 8h1.6M3.2 3.2l1.1 1.1M11.7 11.7l1.1 1.1M12.8 3.2l-1.1 1.1M4.3 11.7l-1.1 1.1"
            stroke="currentColor"
            strokeWidth="1.3"
            strokeLinecap="round"
          />
        </svg>
      ),
    },
    {
      value: "dark",
      label: "Dark",
      icon: (
        <svg viewBox="0 0 16 16" width="13" height="13" fill="none" aria-hidden="true">
          <path
            d="M13.4 9.6A5.8 5.8 0 0 1 6.4 2.6a5.8 5.8 0 1 0 7 7Z"
            stroke="currentColor"
            strokeWidth="1.3"
            strokeLinejoin="round"
          />
        </svg>
      ),
    },
  ];

  return (
    <div
      className="inline-flex items-center gap-0.5 rounded-full border border-border bg-raised p-0.5"
      role="group"
      aria-label="Colour theme"
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => apply(option.value)}
          aria-pressed={theme === option.value}
          // Named explicitly rather than relying on the title: once a button has
          // text content the title is ignored for the accessible name, so the
          // full control announced as "Dark" and the compact one as "Dark
          // theme". Same control, two names, depending on the breakpoint.
          aria-label={`${option.label} theme`}
          title={`${option.label} theme`}
          className={clsx(
            "inline-flex items-center gap-1.5 rounded-full px-2 py-1 text-2xs transition",
            theme === option.value
              ? "bg-surface text-primary shadow-card"
              : "text-faint hover:text-secondary",
          )}
        >
          {option.icon}
          {!compact && <span>{option.label}</span>}
        </button>
      ))}
    </div>
  );
}
