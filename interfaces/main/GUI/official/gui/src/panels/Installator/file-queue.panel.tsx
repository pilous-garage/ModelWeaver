import React from 'react';
import { InstallQueuePanel } from '../../panels/InstallQueuePanel.tsx';
import type { PanelDef } from '../../types.ts';
export const Panel: PanelDef = {
  id: "installator-file-queue", label: "File d'installation", icon: "queue", version: "1.0.0",
  description: "File d'attente des installations",
  daemonRoutes: [
    { route: "jobs/list", methods: ["GET"], desc: "Liste des jobs" },
    { route: "jobs/add", methods: ["POST"], desc: "Ajouter un job" },
  ],
  menu: [{ menuPath: ["Fichier"], id: "queue:clear", label: "Vider la file", action: "panel:queue:clear" }],
  declaration: () => "[installator-file-queue] File d'installation v1.0.0\n  Routes: jobs/list, jobs/add",
  component: ({ ctx }) => React.createElement(InstallQueuePanel, { app: ctx.api }),
};
