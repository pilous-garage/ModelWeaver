import React from 'react';
import { DockerResourcesPanel } from '../DockerResourcesPanel.tsx';
import type { PanelDef } from '../../types.ts';

export const Panel: PanelDef = {
  id: "docker-ressources",
  label: "Ressources Docker",
  icon: "docker",
  version: "1.0.0",
  description: "Gestion des ressources Docker du swarm (caches, fork, batteries de tests persistantes)",
  daemonRoutes: [
    { route: "docker/caches/list", methods: ["POST"], desc: "Liste des caches" },
    { route: "docker/cache/create", methods: ["POST"], desc: "Créer un cache (fork+install+commit)" },
    { route: "docker/fork", methods: ["POST"], desc: "Fork un cache en conteneur testeur" },
    { route: "docker/run-tests", methods: ["POST"], desc: "Batterie de tests (conteneur persistant)" },
    { route: "docker/snapshot", methods: ["POST"], desc: "Snapshot état en image" },
    { route: "docker/release", methods: ["POST"], desc: "Libérer un conteneur" },
    { route: "docker/status", methods: ["POST"], desc: "État des conteneurs gérés" },
  ],
  declaration: () => "[docker-ressources] Ressources Docker v1.0.0\n  Caches, fork, batteries de tests persistantes pour le swarm",
  component: ({ ctx }) => React.createElement(DockerResourcesPanel, { app: ctx.api }),
};
