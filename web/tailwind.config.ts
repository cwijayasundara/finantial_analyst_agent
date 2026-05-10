import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        positive: "#36b37e",
        negative: "#ff5630",
        warn:     "#ffab00",
        info:     "#4c9aff",
        accent:   "#6554c0",
        ink:      "#0d1117",
        paper:    "#f5f6f8",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
