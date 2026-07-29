import React from 'react';
import { ServicesMonitorPanel } from '../../panels/ServicesMonitorPanel.tsx';
import type { PanelDef } from '../../types.ts';
export const Panel: PanelDef = {
  id: "debug-services", label: "Services", icon: "monitor_heart", version: "1.0.0",
  description: "Supervision des services du daemon",
  daemonRoutes: [
    { route: "service/list", methods: ["GET"], desc: "Liste services" },
    { route: "service/resources", methods: ["GET"], desc: "Ressources" },
    { route: "service/restart", methods: ["POST"], desc: "Redémarrer" },
    { route: "service/stop", methods: ["POST"], desc: "Arrêter" },
  ],
  menu: [{ menuPath: ["Debug"], id: "services:restart-all", label: "Redémarrer tout", action: "panel:services:restart-all" }],
  declaration: () => "[debug-services] Services v1.0.0\n  Routes: service/list, service/resources, service/restart, service/stop",
  component: ({ ctx }) => React.createElement(ServicesMonitorPanel, { app: ctx.api }),
};
