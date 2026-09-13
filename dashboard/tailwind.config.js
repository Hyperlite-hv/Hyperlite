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
        // Palette issue de la refonte design 2026-09-13 (maquette Claude
        // Design fournie par Antho, ecrans 5a/5b/6a/6b) : violet/marine
        // fonce, meme famille que le logo Hyperlite (HyperliteLogo.jsx).
        status: {
          running: "#2BC4B6",
          stopped: "#9A94C4",
          warning: "#F5A04B",
          error: "#FF8FA3",
        },
        accent: {
          // Violet de marque -- texte/bordures/anneaux de jauge/etats actifs.
          // Le remplissage plein des boutons primaires (fond plus sature,
          // #6C5CE7) vit dans .btn-primary (index.css), pas ici : la
          // maquette utilise deux tons de violet distincts selon le role.
          blue: "#8B7CF6",
          orange: "#F5A04B",
          green: "#2BC4B6", // nom historique ("vert") -- devenu le teal de la maquette
          pink: "#FF8FA3",
        },
        // Habillage permanent (header + sidebar) : reste dans cette teinte
        // quel que soit le theme -- c'est l'ancre visuelle de l'identite
        // Hyperlite, independante du mode clair/sombre. Valeurs exactes de
        // la maquette (ecran 5a) : header et sidebar partagent le meme fond.
        chrome: {
          950: "#2B2657",
          900: "#13112C",
          800: "#13112C",
          700: "#241F52",
          400: "#9A94C4",
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
