// ⚡ Généré par scripts/discover-panels.ts
// Panneaux découverts automatiquement depuis src/panels/

import type { PanelDef } from '../types.ts';

// Découverte automatique des .panel.tsx
const registry: Record<string, PanelDef> = {};

// Import dynamique des modules découverts
const panelContext = import.meta.glob('./**/*.panel.tsx', { eager: true });

for (const [path, mod] of Object.entries(panelContext)) {
  const panel = (mod as any).Panel;
  if (!panel || !panel.id) {
    console.warn(`[panels] ${path}: pas d'export Panel valide`);
    continue;
  }
  if (registry[panel.id]) {
    console.error(`[panels] ERREUR: id="${panel.id}" en conflit (${path})`);
    continue;
  }
  registry[panel.id] = panel;
}

export const PANEL_REGISTRY: Record<string, PanelDef> = registry;

export function getPanelDeclaration(id: string): string | null {
  const p = PANEL_REGISTRY[id];
  return p?.declaration?.() ?? null;
}

export function listAllPanelDeclarations(): string[] {
  return Object.entries(PANEL_REGISTRY).map(([id, p]) => `--- ${id} ---\n${p.declaration?.() ?? '(aucune)'}`);
}
