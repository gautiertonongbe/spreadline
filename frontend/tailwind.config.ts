import type { Config } from "tailwindcss";

/**
 * Palette and type scale.
 *
 * Two principles, both borrowed from how trading and research desks actually
 * present numbers:
 *
 * 1. Colour carries meaning, never decoration. A recommendation, a risk level
 *    and a confidence each own one colour, so a warm cell always signals the
 *    same thing wherever it appears. The neutrals are deliberately desaturated
 *    so the few meaningful colours carry.
 * 2. The surface is near-black with a warm cast rather than blue-grey, and text
 *    is warm off-white rather than pure white. Pure white on pure black is
 *    fatiguing to read for the length of time an operator spends on a table.
 */
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#0a0a0c",
        surface: "#121216",
        raised: "#1a1a20",
        overlay: "#212129",
        border: "#26262f",
        hairline: "#1e1e25",
        primary: "#f2efe9",
        secondary: "#c9c5bd",
        muted: "#85838f",
        faint: "#5c5a65",
        // Restrained gold. The one accent, used for emphasis and never for a
        // semantic state, so it cannot be confused with a result.
        accent: "#c9a961",
        "accent-dim": "#8a7440",
        buy: "#5bbf83",
        review: "#d4a548",
        pass: "#d86a6a",
        risk: {
          low: "#5bbf83",
          medium: "#d4a548",
          high: "#dc8a56",
          critical: "#d86a6a",
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
        card: "0 1px 2px rgba(0,0,0,0.4), 0 8px 24px -12px rgba(0,0,0,0.6)",
        lift: "0 2px 4px rgba(0,0,0,0.5), 0 16px 40px -16px rgba(0,0,0,0.7)",
      },
      backgroundImage: {
        "hairline-top":
          "linear-gradient(to bottom, rgba(255,255,255,0.045), rgba(255,255,255,0))",
      },
    },
  },
  plugins: [],
};

export default config;
