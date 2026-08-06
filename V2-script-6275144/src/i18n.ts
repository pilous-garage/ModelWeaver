// i18n — résolution de clés multilingues (fr/en).
//
// Chaque chaîne visible est une clé (jamais de texte en dur). Les traductions
// vivent dans _FR et _EN (dictionnaires embarqués). La locale active est un
// STORE RÉACTIF (useSyncExternalStore) : changer de langue re-rend l'UI.
// La préférence est persistée via le daemon (session/save 'prefs-langue').

import { useSyncExternalStore } from 'react';
import { parse } from 'yaml';

export type Locale = 'fr' | 'en';

// ── Dictionnaires embarqués ───────────────────────────────────────────

const _FR: Record<string, string> = {
  // menu
  'menu.fichier': 'Fichier',
  'menu.nouvelleFenetre': 'Nouvelle fenêtre',
  'menu.quitter': 'Quitter',
  'menu.fenetre': 'Fenêtre',
  'menu.fenetresOuvertes': 'Fenêtres ouvertes',
  'menu.fenetreVide': '(fenêtre vide)',
  'menu.ouvertsDansFenetre': 'Ouverts dans une fenêtre',
  'menu.ouvrir': 'Ouvrir une fenêtre',
  'menu.enregistrer': 'Enregistrer',
  'menu.pleinEcran': 'Plein écran',
  'menu.fermerFenetre': 'Fermer la fenêtre',
  'menu.affichage': 'Affichage',
  'menu.themes': 'Thèmes',
  'menu.panneaux': 'Panneaux',
  'menu.onglet': 'Onglet',
  'menu.miniLayout': 'Mini-layout',
  'menu.langue': 'Langue',
  'menu.langueFr': 'Français',
  'menu.langueEn': 'English',
  'menu.fermerOnglet': 'Fermer',
};

const _EN: Record<string, string> = {
  'menu.fichier': 'File',
  'menu.nouvelleFenetre': 'New window',
  'menu.quitter': 'Quit',
  'menu.fenetre': 'Window',
  'menu.fenetresOuvertes': 'Open windows',
  'menu.fenetreVide': '(empty window)',
  'menu.ouvertsDansFenetre': 'Open in a window',
  'menu.ouvrir': 'Open a window',
  'menu.enregistrer': 'Save',
  'menu.pleinEcran': 'Fullscreen',
  'menu.fermerFenetre': 'Close window',
  'menu.affichage': 'View',
  'menu.themes': 'Themes',
  'menu.panneaux': 'Panels',
  'menu.onglet': 'Tab',
  'menu.miniLayout': 'Mini-layout',
  'menu.langue': 'Language',
  'menu.langueFr': 'French',
  'menu.langueEn': 'English',
  'menu.fermerOnglet': 'Close',
};

// ── Store réactif ─────────────────────────────────────────────────────

let _locale: Locale = 'fr';
let _dict: Record<string, string> = { ..._FR };
const _extras: Record<Locale, Record<string, string>> = { fr: {}, en: {} };
const _listeners = new Set<() => void>();

function notify() {
  for (const l of _listeners) l();
}

function rebuildDict() {
  _dict = { ...(_locale === 'en' ? _EN : _FR), ..._extras[_locale] };
  notify();
}

/** Charge un dictionnaire YAML pour une locale donnée (fusion dans les extras). */
export function loadLangYaml(yaml: string, locale?: Locale): void {
  try {
    const data = parse(yaml);
    flatten('', data, _extras[locale ?? _locale]);
    rebuildDict();
  } catch {
    // ignore malformed
  }
}

/** Charge plusieurs dictionnaires YAML. */
export function loadLangYamls(yamls: string[], locale?: Locale): void {
  for (const y of yamls) loadLangYaml(y, locale);
}

/** Définit la locale active (fr/en) et notifie les abonnés. */
export function setLocale(locale: Locale): void {
  if (locale === _locale) return;
  _locale = locale;
  rebuildDict();
}

export function getLocale(): Locale {
  return _locale;
}

function subscribe(cb: () => void) {
  _listeners.add(cb);
  return () => { _listeners.delete(cb); };
}
function getSnapshot(): Locale {
  return _locale;
}

/** Hook React : re-rend au changement de langue. */
export function useLocale(): Locale {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

/** Résout une clé (ex. "menu.fichier"). Retourne la clé si introuvable. */
export function t(key: string): string {
  if (key === undefined || key === null) return '';
  const val = _dict[key];
  if (val !== undefined) return val;
  return key;
}

function flatten(prefix: string, obj: any, out: Record<string, string>) {
  for (const [k, v] of Object.entries(obj || {})) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (typeof v === 'string') out[key] = v;
    else if (v && typeof v === 'object') flatten(key, v, out);
  }
}

/** Dictionnaire minimal par défaut (le FR est déjà chargé). */
export function loadDefaultLang(): void {
  // le FR est le dict de base ; rien d'autre à faire
}

// ── Persistance (best-effort via daemon) ──────────────────────────────

const PREFS_NAME = 'prefs-langue';

/** Charge la langue persistée (daemon session/get) — best-effort. */
export async function loadSavedLocale(post?: (route: string, body: any) => Promise<any>): Promise<Locale> {
  if (!post) return _locale;
  try {
    const res = await post('session/get', { name: PREFS_NAME });
    const yaml = res?.result?.yaml ?? res?.yaml;
    if (yaml) {
      const data = parse(yaml);
      const loc = data?.locale;
      if (loc === 'en' || loc === 'fr') setLocale(loc);
    }
  } catch {
    // best-effort
  }
  return _locale;
}

/** Persiste la langue (daemon session/save) — best-effort. */
export async function persistLocale(locale: Locale, post?: (route: string, body: any) => Promise<any>): Promise<void> {
  if (!post) return;
  try {
    await post('session/save', { name: PREFS_NAME, yaml: `locale: ${locale}\n` });
  } catch {
    // best-effort
  }
}
