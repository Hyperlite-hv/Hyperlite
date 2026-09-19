import js from "@eslint/js";
import globals from "globals";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";

export default [
  // Vendored third-party code (noVNC, xterm) and build output are not linted.
  { ignores: ["dist/**", "public/**", "node_modules/**", "e2e-report/**", "e2e-results/**"] },
  js.configs.recommended,
  {
    files: ["src/**/*.{js,jsx}"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: { ...globals.browser },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: { react, "react-hooks": reactHooks },
    settings: { react: { version: "18.3" } },
    rules: {
      ...react.configs.recommended.rules,
      ...react.configs["jsx-runtime"].rules,
      ...reactHooks.configs.recommended.rules,
      // Props are documented by usage; PropTypes are not used in this code base.
      "react/prop-types": "off",
      // Apostrophes and quotes are valid in JSX text; keep guarding the characters that
      // usually indicate a typo (a stray > or }).
      "react/no-unescaped-entities": ["error", { forbid: [">", "}"] }],
      // Opinionated React Compiler rule. The flagged code is the usual "fetch on mount / reset when
      // a dependency changes" effect, which is legitimate; the proper fix is a data-fetching layer
      // (tracked in the UI-stack migration issue), not rewriting each effect by hand.
      "react-hooks/set-state-in-effect": "off",
    },
  },
  {
    files: ["*.config.js"],
    languageOptions: { globals: { ...globals.node } },
  },
];
