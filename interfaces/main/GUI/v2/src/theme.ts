// theme.ts — gestion des thèmes (variables CSS injectées).
//
// Un thème = un jeu de variables CSS (--mw-*) injecté dans un <style> global
// (#mw-theme). Le daemon expose theme/list|get|save (~/.modelweaver/themes/).
// Si aucun thème sauvegardé, fallback dark (défaut GUI).

import { parse } from 'yaml';
import { daemonPost } from './bridge.ts';

export interface ThemeDef {
  name: string;
  label?: string;
  vars: Record<string, string>;
}

// Variables CSS par défaut (dark) — référence du design system.
const DARK_VARS: Record<string, string> = {
  '--mw-bg': '#0f172a',
  '--mw-bg-panel': '#1e293b',
  '--mw-bg-hover': '#243349',
  '--mw-fg': '#e2e8f0',
  '--mw-fg-dim': '#94a3b8',
  '--mw-fg-faint': '#64748b',
  '--mw-border': '#334155',
  '--mw-accent': '#3b82f6',
  '--mw-accent-bg': '#1d4ed8',
  '--mw-error': '#f87171',
  '--mw-ok': '#4ade80',
  '--mw-font-ui': 'sans-serif',
  '--mw-font-mono': 'ui-monospace, monospace',
};

const LIGHT_VARS: Record<string, string> = {
  '--mw-bg': '#f1f5f9',
  '--mw-bg-panel': '#ffffff',
  '--mw-bg-hover': '#e2e8f0',
  '--mw-fg': '#0f172a',
  '--mw-fg-dim': '#475569',
  '--mw-fg-faint': '#64748b',
  '--mw-border': '#cbd5e1',
  '--mw-accent': '#2563eb',
  '--mw-accent-bg': '#3b82f6',
  '--mw-error': '#dc2626',
  '--mw-ok': '#16a34a',
  '--mw-font-ui': 'sans-serif',
  '--mw-font-mono': 'ui-monospace, monospace',
};

const BUILTIN: Record<string, { label: string; vars: Record<string, string> }> = {
  dark: { label: 'Sombre', vars: DARK_VARS },
  light: { label: 'Clair', vars: LIGHT_VARS },
};

let _current = 'dark';
let _styleEl: HTMLStyleElement | null = null;

export function getCurrentTheme(): string {
  return _current;
}

/** Liste des thèmes connus (builtin + daemon). */
export async function listThemes(): Promise<ThemeDef[]> {
  const out: ThemeDef[] = Object.entries(BUILTIN).map(([name, t]) => ({ name, label: t.label, vars: t.vars }));
  try {
    const res = await daemonPost('theme/list', {});
    const themes = res?.result?.themes ?? res?.themes ?? [];
    for (const t of themes) {
      if (!BUILTIN[t.name]) out.push({ name: t.name, label: t.label ?? t.name, vars: {} });
    }
  } catch { /* best-effort */ }
  return out;
}

/** Charge un thème daemon (YAML → vars CSS). */
export async function loadThemeVars(name: string): Promise<Record<string, string> | null> {
  try {
    const res = await daemonPost('theme/get', { name });
    const yaml = res?.result?.yaml ?? res?.yaml;
    if (!yaml) return null;
    const data = parse(yaml);
    if (!data) return null;
    // Formats acceptés : {vars: {...}} | {dark: {...}, light: {...}} | vars plats
    if (data.vars && typeof data.vars === 'object') return data.vars;
    if (data[_current] && typeof data[_current] === 'object') return data[_current];
    const flat: Record<string, string> = {};
    for (const [k, v] of Object.entries(data)) {
      if (typeof v === 'string' && (k.startsWith('--') || k.startsWith('mw-'))) {
        flat[k.startsWith('mw-') ? `--${k}` : k] = v;
      }
    }
    return Object.keys(flat).length ? flat : null;
  } catch {
    return null;
  }
}

/** Applique un thème (injecte les variables CSS). Persiste via layout.theme. */
export async function applyTheme(name: string): Promise<void> {
  _current = BUILTIN[name] ? name : name;
  const vars = BUILTIN[name]?.vars ?? (await loadThemeVars(name)) ?? DARK_VARS;
  injectVars(vars);
}

function injectVars(vars: Record<string, string>) {
  if (!_styleEl) {
    _styleEl = document.createElement('style');
    _styleEl.id = 'mw-theme';
    document.head.appendChild(_styleEl);
  }
  const css = `:root {\n${Object.entries(vars).map(([k, v]) => `  ${k}: ${v};`).join('\n')}\n}`;
  _styleEl.textContent = css;
  document.documentElement.setAttribute('data-theme', _current);
}

/** Applique le thème depuis un layout au boot. */
export async function applyThemeFromLayout(theme?: { global?: string; panel?: string }): Promise<string> {
  const name = theme?.global || 'dark';
  await applyTheme(name);
  return name;
}
