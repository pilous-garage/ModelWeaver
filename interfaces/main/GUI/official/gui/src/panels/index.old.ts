// ⚡ Généré par scripts/discover-panels.ts

import React from 'react';

// ── Installator ───────────────────────────────────────────
import { DashboardPanel as _i_Dashboard } from '../DashboardPanel.tsx';
import { DependenciesPanel as _i_Deps } from '../DependenciesPanel.tsx';
import { InstallQueuePanel as _i_Queue } from '../InstallQueuePanel.tsx';
import { InstalledToolsPanel as _i_Tools } from '../InstalledToolsPanel.tsx';

// ── Agents ───────────────────────────────────────────────
import { AgentsPanel as _a_Liste } from '../AgentsPanel.tsx';
import { AgentMonitoringPanel as _a_Monitoring } from '../AgentMonitoringPanel.tsx';
import { TeamCompositionPanel as _a_Team } from '../TeamCompositionPanel.tsx';
import { AgentLauncherPanel as _a_Launcher } from '../AgentLauncherPanel.tsx';

// ── Communication ────────────────────────────────────────
import { ChatPanel as _c_Chat } from '../ChatPanel.tsx';

// ── Gestion ──────────────────────────────────────────────
import { CataloguePanel as _g_Catalogue } from '../CataloguePanel.tsx';
import { KeysPanel as _g_Keys } from '../KeysPanel.tsx';

// ── Systeme ──────────────────────────────────────────────
import { LocalModelsPanel as _s_Local } from '../LocalModelsPanel.tsx';
import { SystemStatePanel as _s_State } from '../SystemStatePanel.tsx';
import { SystemDashboardPanel as _s_Dashboard } from '../SystemDashboardPanel.tsx';
import { ResourcesPanel as _s_Resources } from '../ResourcesPanel.tsx';

// ── Debug ────────────────────────────────────────────────
import { DebugPanel as _d_Debug } from '../DebugPanel.tsx';

// ── Service ──────────────────────────────────────────────
import { ServicesMonitorPanel as _sv_Services } from '../ServicesMonitorPanel.tsx';

// ── Projet ───────────────────────────────────────────────
import { TeamCompositionPanel as _p_Team } from '../TeamCompositionPanel.tsx';

import type { PanelDef } from '../types.ts';

const _make = (id: string, label: string, icon: string,
               Component: React.FC<any>, routes: string[] = []): PanelDef => ({
  id, label, icon, version: "1.0.0",
  description: label,
  daemonRoutes: routes.map(r => ({ route: r, methods: ["GET"] as const, desc: r })),
  menu: [],
  declaration() { return `[${id}] ${label} v1.0.0`; },
  component: Component as React.FC<any>,
});

export const PANEL_REGISTRY: Record<string, PanelDef> = {
  "installator-dashboard": _make("installator-dashboard", "Dashboard", "dashboard",
    _i_Dashboard, ["system/info", "system/state/get"]),
  "installator-deps": _make("installator-deps", "Dépendances", "checklist",
    _i_Deps, ["deps/check", "deps/install"]),
  "installator-file-queue": _make("installator-file-queue", "File installation", "queue",
    _i_Queue, ["jobs/list", "jobs/add"]),
  "installator-outils-installes": _make("installator-outils-installes", "Outils installés", "build",
    _i_Tools, ["tools/installed/list", "tools/install"]),

  "agents-liste": _make("agents-liste", "Agents", "smart_toy",
    _a_Liste, ["agent/list", "agent/get", "agent/signal", "agent/metrics"]),
  "agents-monitoring": _make("agents-monitoring", "Monitoring agents", "monitoring",
    _a_Monitoring, ["agent/metrics", "service/resources"]),
  "agents-composition-equipe": _make("agents-composition-equipe", "Équipe", "groups",
    _a_Team, ["team/list", "team/get", "team/add-member"]),
  "agents-lanceur": _make("agents-lanceur", "Lanceur", "play_arrow",
    _a_Launcher, ["agent/launch"]),

  "communication-chat": _make("communication-chat", "Chat", "chat",
    _c_Chat, ["chat/session/list", "chat/session/get", "chat/session/send"]),

  "gestion-catalogue-modeles": _make("gestion-catalogue-modeles", "Catalogue modèles", "database",
    _g_Catalogue, ["llm/models/list", "providers/list"]),
  "gestion-cles-api": _make("gestion-cles-api", "Clés API", "key",
    _g_Keys, ["keys/list", "keys/set", "keys/delete", "keys/set_lock"]),

  "systeme-etat": _make("systeme-etat", "État système", "info",
    _s_State, ["system/state/get"]),
  "systeme-dashboard": _make("systeme-dashboard", "Dashboard système", "dashboard",
    _s_Dashboard, ["system/info", "system/state/get", "service/list"]),
  "systeme-ressources": _make("systeme-ressources", "Ressources", "pie_chart",
    _s_Resources, ["system/state/get"]),
  "systeme-llm-locaux": _make("systeme-llm-locaux", "LLM locaux", "computer",
    _s_Local, ["llm/local/list", "llm/local/start", "llm/local/stop"]),

  "debug-logs": _make("debug-logs", "Debug", "bug_report",
    _d_Debug, ["logs/read", "service/list", "system/state/get"]),

  "service-monitor": _make("service-monitor", "Services", "monitor_heart",
    _sv_Services, ["service/list", "service/resources"]),
};

export function getPanelDeclaration(id: string): string | null {
  const p = PANEL_REGISTRY[id];
  return p ? p.declaration() : null;
}

export function listAllPanelDeclarations(): string[] {
  return Object.entries(PANEL_REGISTRY).map(([id, p]) => `--- ${id} ---\n${p.declaration()}`);
}
