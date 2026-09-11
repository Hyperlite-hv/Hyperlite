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
        anthracite: {
          950: "#0d0f14",
          900: "#14161c",
          800: "#1c1f26",
          700: "#23262f",
          600: "#2c303a",
          500: "#3a3f4b",
          400: "#565c6b",
          300: "#7a8194",
          200: "#a4aabb",
          100: "#d4d7e0",
        },
        status: {
          running: "#3fb950",
          stopped: "#7a8194",
          warning: "#e3a008",
          error: "#e5484d",
        },
        accent: {
          blue: "#4f8cff",
          orange: "#ff9f43",
          green: "#3fb950",
        },
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      boxShadow: {
        panel: "0 1px 2px rgba(0,0,0,0.3), 0 0 0 1px rgba(255,255,255,0.03)",
      },
    },
  },
  plugins: [],
};
