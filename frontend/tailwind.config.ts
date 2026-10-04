import type { Config } from "tailwindcss";

/**
 * Palette and type scale.
 *
 * Every colour is a CSS variable holding an RGB triple, resolved in
 * `globals.css` for each theme and consumed here through Tailwind's
 * `<alpha-value>` placeholder. That indirection is what makes `bg-buy/12` work
 * identically on paper and on ink: the opacity modifier still composes, and no
 * component has to know which theme it is rendering into.
 *
 * Two principles hold across both themes, borrowed from how trading and
 * research desks actually present numbers:
 *
 * 1. Colour carries meaning, never decoration. A recommendation, a risk level
 *    and a confidence each own one colour, so a warm cell always signals the
 *    same thing wherever it appears. The neutrals are deliberately desaturated
 *    so the few meaningful colours carry.
 * 2. Neither surface is pure. The dark theme is near-black with a warm cast
 *    rather than blue-grey; the light theme is warm paper rather than white,
 *    with ink rather than black text. Maximum contrast is fatiguing to read for
 *    the length of time an operator spends on a table.
 */
const token = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: token("canvas"),
        surface: token("surface"),
        raised: token("raised"),
        overlay: token("overlay"),
        border: token("border"),
        hairline: token("hairline"),
        primary: token("primary"),
        secondary: token("secondary"),
        muted: token("muted"),
        faint: token("faint"),
        // Restrained gold. The one accent, used for emphasis and never for a
        // semantic state, so it cannot be confused with a result.
        accent: token("accent"),
        "accent-dim": token("accent-dim"),
        buy: token("buy"),
        review: token("review"),
        pass: token("pass"),
        risk: {
          low: token("risk-low"),
          medium: token("risk-medium"),
          high: token("risk-high"),
          critical: token("risk-critical"),
        },
      },
      fontFamily: {
        // Playfair Display: page titles, section headings and display figures.
        display: ["var(--font-display)", "Georgia", "serif"],
        // The reading and interface face.
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        // Dense numeric columns.
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      fontSize: {
        "3xs": ["0.625rem", { lineHeight: "0.875rem", letterSpacing: "0.08em" }],
        "2xs": ["0.6875rem", { lineHeight: "1rem", letterSpacing: "0.04em" }],
      },
      letterSpacing: {
        label: "0.1em",
      },
      boxShadow: {
        card: "var(--shadow-card)",
        lift: "var(--shadow-lift)",
      },
      backgroundImage: {
        "hairline-top": "var(--sheen)",
      },
    },
  },
  plugins: [],
};

export default config;
