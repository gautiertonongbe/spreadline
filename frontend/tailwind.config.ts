import type { Config } from "tailwindcss";

/**
 * The palette is deliberately restrained. This is analytical software: colour
 * carries meaning (a recommendation, a risk level, a direction) and is not used
 * for decoration, so that a red cell always means something.
 */
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#0b0e14",
        surface: "#11151d",
        raised: "#161b25",
        border: "#232a37",
        muted: "#8792a8",
        primary: "#e8ecf4",
        accent: "#4c8dff",
        buy: "#2fbf71",
        review: "#e0a33e",
        pass: "#e05c5c",
        risk: {
          low: "#2fbf71",
          medium: "#e0a33e",
          high: "#e8763c",
          critical: "#e05c5c",
        },
      },
      fontFamily: {
        sans: ["ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Inter", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
      },
    },
  },
  plugins: [],
};

export default config;
