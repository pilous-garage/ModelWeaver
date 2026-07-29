import React from 'react';
import { SystemDashboardPanel } from '../../panels/SystemDashboardPanel.tsx';
import type { PanelDef } from '../../types.ts';
export const Panel: PanelDef = {
  id: "systeme-dashboard", label: "Dashboard", icon: "dashboard", version: "1.0.0",
  description: "Vue d'ensemble du système",
  daemonRoutes: [
    { route: "system/info", methods: ["GET"], desc: "Info système" },
    { route: "system/state/get", methods: ["GET"], desc: "État" },
    { route: "service/list", methods: ["GET"], desc: "Services" },
  ],
  menu: [{ menuPath: ["Affichage"], id: "sysdash:refresh", label: "Rafraîchir", action: "panel:sysdash:refresh" }],
  declaration: () => "[systeme-dashboard] Dashboard v1.0.0\n  Routes: system/info, system/state/get, service/list",
  component: ({ ctx }) => React.createElement(SystemDashboardPanel, { app: ctx.api }),
};
