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
        // Rampe de contenu (page, cartes, textes) : les valeurs reelles vivent
        // dans des variables CSS (voir index.css), ivoire/lavande/charbon par
        // defaut, violet fonce sous .dark -- donc aucun composant n'a besoin
        // de connaitre le theme actif, seul index.css definit les deux jeux
        // de valeurs. Le nom "anthracite" est garde tel quel pour ne pas
        // devoir renommer ~40 fichiers ; ce n'est plus qu'un alias historique.
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
        status: {
          running: "#3fb950",
          stopped: "#7a8194",
          warning: "#e3a008",
          error: "#e5484d",
        },
        accent: {
          // Indigo/violet -- accent de marque, constant en clair comme en
          // sombre (boutons primaires, onglet actif, jauges CPU/RAM).
          blue: "#5A4FCF",
          orange: "#ff9f43",
          green: "#3fb950",
        },
        // Habillage permanent (header + sidebar) : reste dans cette teinte
        // indigo/violet quel que soit le theme -- c'est l'ancre visuelle de
        // l'identite Hyperlite, independante du mode clair/sombre.
        chrome: {
          950: "#2E2560",
          900: "#3B2F72",
          800: "#4B3F8C",
          700: "#5D4FA6",
          400: "#B7AEDD",
          100: "#F5F3FA",
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
