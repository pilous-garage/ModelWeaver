import React from 'react';
import { SystemStatePanel } from '../../panels/SystemStatePanel.tsx';
import type { PanelDef } from '../../types.ts';
export const Panel: PanelDef = {
  id: "systeme-etat", label: "État système", icon: "info", version: "1.0.0",
  description: "Informations sur le système hôte",
  daemonRoutes: [{ route: "system/state/get", methods: ["GET"], desc: "État du système" }],
  menu: [],
  declaration: () => "[systeme-etat] État système v1.0.0\n  Routes: system/state/get",
  component: ({ ctx }) => React.createElement(SystemStatePanel, { app: ctx.api }),
};
