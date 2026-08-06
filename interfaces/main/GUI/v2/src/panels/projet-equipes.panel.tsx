// projet/equipes — alias du panel équipes (V1 : projet-equipes).
// Réutilise le même composant que agents-equipe.

import type { PanelDef } from './contract.ts';
import { Panel as Base } from './agents-equipe.panel.tsx';

export const Panel: PanelDef = {
  id: 'projet-equipes',
  labelKey: 'panels.agents-equipe.titre',
  iconKey: 'panels.agents-equipe.titre',
  version: '1.0.0',
  essential: false,
  langEmbedded: (Base as any).langEmbedded,
  langEmbeddedEn: (Base as any).langEmbeddedEn,
  declaration: () => '[projet-equipes] Équipes v1.0.0\n  routes: team/list, team/add-member, team/set-leader, capabilities',
  component: Base.component,
};

export const langFr = (Base as any).langEmbedded;