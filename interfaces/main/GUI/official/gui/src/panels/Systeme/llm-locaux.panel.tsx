import React from 'react';
import { LocalModelsPanel } from '../../panels/LocalModelsPanel.tsx';
import type { PanelDef } from '../../types.ts';
export const Panel: PanelDef = {
  id: "systeme-llm-locaux", label: "LLM locaux", icon: "computer", version: "1.0.0",
  description: "Moteurs LLM locaux (Ollama, LM Studio…)",
  daemonRoutes: [
    { route: "llm/local/list", methods: ["GET"], desc: "Moteurs détectés" },
    { route: "llm/local/start", methods: ["POST"], desc: "Démarrer" },
    { route: "llm/local/stop", methods: ["POST"], desc: "Arrêter" },
  ],
  menu: [{ menuPath: ["Affichage"], id: "local:refresh", label: "Détecter les moteurs", action: "panel:local:refresh" }],
  declaration: () => "[systeme-llm-locaux] LLM locaux v1.0.0\n  Routes: llm/local/list, llm/local/start, llm/local/stop",
  component: ({ ctx }) => React.createElement(LocalModelsPanel, { app: ctx.api }),
};
