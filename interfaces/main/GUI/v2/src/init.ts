import type { PanelDef } from './types.ts';
import { Panel as DebugServicesPanel } from './panels/debug-services.panel.tsx';

const registry: Record<string, PanelDef> = {
  [DebugServicesPanel.id]: DebugServicesPanel,
};

export { registry as PANEL_REGISTRY };
export { DebugServicesPanel };

export function getPanelDeclaration(id: string): string | null {
  return registry[id]?.declaration?.() ?? null;
}

export function listAllPanelDeclarations(): string[] {
  return Object.entries(registry).map(([id, p]) => `--- ${id} ---\n${p.declaration?.() ?? '(aucune)'}`);
}

export function initPanelsV2(): Record<string, PanelDef> {
  return registry;
}
