import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import de from "./locales/de.json";
import en from "./locales/en.json";

export const LANGUAGES = ["de", "en"] as const;
export type Language = (typeof LANGUAGES)[number];
export const DEFAULT_LANGUAGE: Language = "de";
const STORAGE_KEY = "rushbase.lang";

function storedLanguage(): Language {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === "de" || stored === "en") return stored;
  } catch {
    // Storage may be unavailable; the default language applies.
  }
  return DEFAULT_LANGUAGE;
}

/** Switch the UI language and remember the choice. */
export async function setLanguage(language: Language): Promise<void> {
  await i18n.changeLanguage(language);
  document.documentElement.lang = language;
  try {
    window.localStorage.setItem(STORAGE_KEY, language);
  } catch {
    // Persisting the choice is a convenience only.
  }
}

void i18n.use(initReactI18next).init({
  resources: { de: { translation: de }, en: { translation: en } },
  lng: storedLanguage(),
  fallbackLng: DEFAULT_LANGUAGE,
  interpolation: { escapeValue: false },
});
document.documentElement.lang = i18n.language;

export default i18n;
