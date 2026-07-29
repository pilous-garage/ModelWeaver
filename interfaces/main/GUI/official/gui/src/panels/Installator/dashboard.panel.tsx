import React from 'react';
import { DashboardPanel } from '../../panels/DashboardPanel.tsx';
import type { PanelDef } from '../../types.ts';

export const Panel: PanelDef = {
  id: "installator-dashboard",
  label: "Dashboard",
  icon: "dashboard",
  version: "1.0.0",
  description: "Panneau principal de l'installateur",
  daemonRoutes: [
    { route: "system/info", methods: ["GET"], desc: "Version du système" },
    { route: "system/state/get", methods: ["GET"], desc: "État courant" },
  ],
  menu: [
    { menuPath: ["Affichage"], id: "dashboard:refresh", label: "Rafraîchir", action: "panel:dashboard:refresh" },
  ],
  declaration: () => [
    "[installator-dashboard] Dashboard v1.0.0",
    "  Panneau principal de l'installateur ModelWeaver",
    "  Routes: system/info, system/state/get",
    "  Menu: Affichage > Rafraîchir",
  ].join("\n"),
  component: ({ ctx }) => React.createElement(DashboardPanel, { app: ctx.api }),
};
