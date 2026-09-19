/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        panel: {
          DEFAULT: "#1c1f26",
          light: "#f3f4f6",
        },
        surface: {
          DEFAULT: "#23262f",
          light: "#ffffff",
        },
        // Content ramp (page, cards, text): the real values live in CSS variables
        // (see index.css), a light default set and a dark set under .dark, so no
        // component needs to know the active theme; only index.css defines both
        // sets of values. The name "anthracite" is kept as is to avoid renaming
        // ~40 files; it is only a historical alias now.
        anthracite: {
          950: "rgb(var(--a-950) / <alpha-value>)",
          900: "rgb(var(--a-900) / <alpha-value>)",
          800: "rgb(var(--a-800) / <alpha-value>)",
          700: "rgb(var(--a-700) / <alpha-value>)",
          600: "rgb(var(--a-600) / <alpha-value>)",
          500: "rgb(var(--a-500) / <alpha-value>)",
          400: "rgb(var(--a-400) / <alpha-value>)",
          300: "rgb(var(--a-300) / <alpha-value>)",
          200: "rgb(var(--a-200) / <alpha-value>)",
          100: "rgb(var(--a-100) / <alpha-value>)",
        },
        // "Indigo console" palette: indigo/white, from the same family as the
        // Hyperlite logo (HyperliteLogo.jsx).
        status: {
          running: "#16A34A",
          stopped: "#6B7280",
          warning: "#D97706",
          error: "#DC2626",
        },
        accent: {
          // Brand indigo: text/borders/gauge rings/active states AND the solid
          // fill of primary buttons (see .btn-primary, index.css): a single tone
          // serves both roles.
          blue: "#4F46E5",
          orange: "#D97706",
          green: "#16A34A",
          pink: "#DC2626",
        },
        // Permanent chrome (sidebar): stays in this hue whatever the light/dark
        // theme of the content, a visual anchor of the Hyperlite identity that is
        // independent of the mode. Only the side column (logo + nav + Datacenter
        // tree) keeps this dark indigo background; the header is light.
        chrome: {
          950: "#171340",
          900: "#211C49",
          800: "#211C49",
          700: "#332C6E",
          400: "#8C88BE",
          100: "#EDEBFA",
        },
      },
      fontFamily: {
        sans: ["Archivo", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ['"IBM Plex Mono"', "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      boxShadow: {
        panel: "0 1px 2px rgba(0,0,0,0.3), 0 0 0 1px rgba(255,255,255,0.03)",
      },
    },
  },
  plugins: [],
};
