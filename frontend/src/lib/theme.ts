/**
 * The storage key for the colour theme.
 *
 * It lives in its own module because both sides need it: the inline script in
 * the server-rendered layout that resolves the theme before first paint, and
 * the client toggle that writes it. Exporting it from the `"use client"` toggle
 * instead gave the server a client reference rather than the string, and the
 * script silently read `localStorage.getItem(undefined)`, which always misses:
 * the stored choice was written correctly and never read back.
 */
export const THEME_KEY = "spreadline-theme";

export type Theme = "light" | "dark";
