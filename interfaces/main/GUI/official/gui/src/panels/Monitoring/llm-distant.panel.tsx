import React from 'react';
import { LlmMonitorPanel } from '../../panels/LlmMonitorPanel.tsx';
import type { PanelDef } from '../../types.ts';
export const Panel: PanelDef = {
  id: "monitoring/llm-distant", label: "Moniteur LLM distant", icon: "monitoring", version: "1.0.0",
  description: "Consommation LLM distants : requêtes, tokens, coût sur 1h/24h/7j",
  daemonRoutes: [{ route: "usage/monitor", methods: ["GET"], desc: "Consommation LLM" }],
  menu: [],
  declaration: () => "[monitoring/llm-distant] Moniteur LLM distant v1.0.0\n  Routes: usage/monitor",
  component: ({ ctx }) => React.createElement(LlmMonitorPanel),
};
