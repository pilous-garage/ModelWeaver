/** PanelDef — Contrat d'un panneau */
import React from 'react';

export interface DaemonRouteDeclaration {
  route: string;
  methods: ("GET" | "POST" | "DELETE" | "PUT")[];
  desc: string;
  params?: Record<string, string>;
}

export interface PanelMenuItemDef {
  menuPath: string[];
  id: string;
  label: string;
  shortcut?: string;
  type?: "normal" | "toggle-visibility" | "separator";
  action: string;
  disabled?: boolean;
}

export interface PanelContext {
  api: any;
  layout: any;
  theme: any;
  onMenuAction: (action: string) => void;
}

export interface PanelDef {
  id: string;
  label: string;
  icon?: string;
  version: string;
  description: string;
  descriptionLong?: string;
  idWarning?: boolean | string;
  daemonRoutes: DaemonRouteDeclaration[];
  menu?: PanelMenuItemDef[];
  defaultSize?: { width?: number; height?: number };
  /** true = panel compilé dans le monolithe (bundle GUI) ; false/absent =
   * panel EXTERNE compilé par panel-creator et chargé à runtime. */
  essential?: boolean;

  declaration(): string;
  onActivate?(ctx: PanelContext): void;
  onDeactivate?(ctx: PanelContext): void;
  onRefresh?(ctx: PanelContext): Promise<void>;

  component: React.FC<{ ctx: PanelContext }>;
}
