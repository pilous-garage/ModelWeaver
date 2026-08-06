// Contrat d'un panel (PanelDef) — référence complète.
// Voir SPEC.md §13.2.

import type { MenuItem } from '../layout/types.ts';

export interface PanelContext {
  api: any;                          // accès au daemon (HTTP)
  layout: any;                       // layout courant
  params: Record<string, any>;       // params de l'occurrence
  onMenuAction(action: string): void;
  addTab(groupId: string, panelId: string, params?: Record<string, any>): void;
  closeTab(groupId: string, occId: string): void;
  activateTab(groupId: string, occId: string): void;
  activateMiniLayout(occId: string): void;
  closeMiniLayout(occId: string): void;
  extractTabToWindow(groupId: string, occId: string): void;
  setPanelTheme(occId: string, theme: string): void;
  t(key: string): string;
}

export interface ParamsSchemaField {
  type: 'string' | 'number' | 'boolean' | 'enum';
  enum?: (string | number)[];
  default?: any;
  description?: string;
}

export interface PanelDef {
  id: string;
  labelKey: string;                  // clé i18n (jamais de texte en dur)
  iconKey?: string;                  // clé i18n de l'icône (onglet)
  version: string;
  essential?: boolean;               // true = bundle GUI
  paramsSchema?: Record<string, ParamsSchemaField>;
  defaultParams?: Record<string, any>;
  langFiles?: string[];              // fichiers .lang.<locale>.yaml
  /** YAML lang embarqué directement dans le module (fallback simple). */
  langEmbedded?: string;
  /** YAML lang anglais embarqué (multilingue : langEmbedded = FR). */
  langEmbeddedEn?: string;
  menu?: MenuItem[];                 // items à insérer dans le menu global
  themeCss?: string;                 // CSS du contenu (classes mw-panel-<id>-*)
  onActivate?: (ctx: PanelContext) => void;
  onDeactivate?: (ctx: PanelContext) => void;
  onParamsChange?: (ctx: PanelContext, oldParams: any, newParams: any) => void;
  declaration: () => string;         // description textuelle (inspecteur)
  component: React.FC<{ ctx: PanelContext; params: Record<string, any> }>;
}
