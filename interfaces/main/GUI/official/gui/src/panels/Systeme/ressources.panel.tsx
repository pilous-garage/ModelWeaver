import React from 'react';
import { ResourcesPanel } from '../../panels/ResourcesPanel.tsx';
import type { PanelDef } from '../../types.ts';
export const Panel: PanelDef = {
  id: "systeme-ressources", label: "Ressources", icon: "pie_chart", version: "1.0.0",
  description: "CPU, RAM, disque en temps réel",
  daemonRoutes: [{ route: "system/state/get", methods: ["GET"], desc: "Ressources" }],
  menu: [],
  declaration: () => "[systeme-ressources] Ressources v1.0.0\n  Routes: system/state/get",
  component: ({ ctx }) => React.createElement(ResourcesPanel, { app: ctx.api }),
};
