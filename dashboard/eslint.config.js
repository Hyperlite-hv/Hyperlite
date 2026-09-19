import js from "@eslint/js";
import globals from "globals";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";

export default [
  // Vendored third-party code (noVNC, xterm) and build output are not linted.
  { ignores: ["dist/**", "public/**", "node_modules/**"] },
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
      // New, opinionated React Compiler rule: reported as a warning (visible backlog) until
      // the affected effects are reviewed one by one.
      "react-hooks/set-state-in-effect": "warn",
    },
  },
  {
    files: ["*.config.js"],
    languageOptions: { globals: { ...globals.node } },
  },
];
