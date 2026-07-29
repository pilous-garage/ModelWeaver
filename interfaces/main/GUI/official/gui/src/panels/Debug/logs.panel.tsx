import React from 'react';
import { DebugPanel } from '../../panels/DebugPanel.tsx';
import type { PanelDef } from '../../types.ts';
export const Panel: PanelDef = {
  id: "debug-logs", label: "Debug", icon: "bug_report", version: "1.0.0",
  description: "Logs, processus et ressources système",
  daemonRoutes: [
    { route: "logs/read", methods: ["GET"], desc: "Logs applicatifs" },
    { route: "service/list", methods: ["GET"], desc: "Services actifs" },
    { route: "system/state/get", methods: ["GET"], desc: "État système" },
  ],
  menu: [{ menuPath: ["Debug"], id: "debug:clear-logs", label: "Vider les logs", action: "panel:debug:clear-logs" }],
  declaration: () => "[debug-logs] Debug v1.0.0\n  Routes: logs/read, service/list, system/state/get",
  component: ({ ctx }) => React.createElement(DebugPanel, { app: ctx.api }),
};
