import js from "@eslint/js";
import globals from "globals";

// Recommended rules for browser modules.
// https://eslint.org/docs/latest/use/configure/configuration-files
// https://eslint.org/docs/latest/use/configure/language-options#specify-globals
export default [
  {
    files: ["custom_components/marstek/frontend/**/*.js"],
    ...js.configs.recommended,
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: globals.browser,
    },
    rules: {
      ...js.configs.recommended.rules,
      // The card is already split across modules. This stops the entry file
      // growing without a bound; 1400 is above the current 1305 lines.
      "max-lines": ["error", { max: 1400, skipBlankLines: false, skipComments: false }],
    },
  },
];
