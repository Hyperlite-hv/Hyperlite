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
        // Palette de la refonte 2026-09-17 (direction "indigo console",
        // validee par Antho sur maquette avant integration -- cf. lien
        // artefact dans la conversation) : indigo/blanc plutot que le
        // violet-marine precedent, jugee trop generique/IA. Meme famille
        // que le logo Hyperlite (HyperliteLogo.jsx).
        status: {
          running: "#16A34A",
          stopped: "#6B7280",
          warning: "#D97706",
          error: "#DC2626",
        },
        accent: {
          // Indigo de marque -- texte/bordures/anneaux de jauge/etats actifs
          // ET remplissage plein des boutons primaires (voir .btn-primary,
          // index.css) : contrairement a l'ancienne palette, un seul ton
          // sert les deux roles ici (coherent avec la reference validee).
          blue: "#4F46E5",
          orange: "#D97706",
          green: "#16A34A",
          pink: "#DC2626",
        },
        // Habillage permanent (sidebar) : reste dans cette teinte quel que
        // soit le theme clair/sombre du contenu -- ancre visuelle de
        // l'identite Hyperlite, independante du mode. Le header/topbar ne
        // fait plus partie du "chrome" depuis la refonte 2026-09-17 (passe
        // clair, voir Header.jsx) ; seule la colonne laterale (logo + nav +
        // arbre Datacenter) garde ce fond indigo fonce.
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
