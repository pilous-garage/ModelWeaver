// Duplicats de TEST des panels de base (pour valider plusieurs onglets,
// split, drag/drop, sans dépendre des panels officiels).
//
// - ressources-variant : copie du panel ressources avec params différents
// - etat-simple : variante minimaliste d'état

import React, { useEffect, useState } from 'react';
import type { PanelDef } from './contract.ts';

// ── ressources-variant ───────────────────────────────────────────────

const RESSOURCES_VARIANT_LANG_FR = `
panels:
  ressources-variant:
    titre: "Ressources (variant)"
    icone: "Rv"
`;

function RessourcesVariant({ ctx, params }: { ctx: any; params: Record<string, any> }) {
  const [cpu, setCpu] = useState<number | null>(null);
  useEffect(() => {
    // données de démo (pas de backend requis pour les tests)
    setCpu(50 + Math.round(Math.random() * 40));
  }, []);
  return (
    <div className="mw-panel mw-panel-ressources-variant" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box' }}>
      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>Ressources (variant)</div>
      <div style={{ fontSize: 12 }}>CPU : {cpu ?? '…'}%</div>
      <div style={{ fontSize: 12 }}>params : {JSON.stringify(params)}</div>
    </div>
  );
}

export const RessourcesVariantPanel: PanelDef = {
  id: 'ressources-variant',
  labelKey: 'panels.ressources-variant.titre',
  iconKey: 'panels.ressources-variant.icone',
  version: '1.0.0',
  essential: true,
  paramsSchema: { mode: { type: 'enum', enum: ['a', 'b'], default: 'a' } },
  defaultParams: { mode: 'a' },
  declaration: () => '[ressources-variant] Ressources (variant de test) v1.0.0',
  langEmbedded: RESSOURCES_VARIANT_LANG_FR,
  component: RessourcesVariant,
};

// ── etat-simple ──────────────────────────────────────────────────────

const ETAT_SIMPLE_LANG_FR = `
panels:
  etat-simple:
    titre: "État (simple)"
    icone: "Es"
`;

function EtatSimple({ ctx, params }: { ctx: any; params: Record<string, any> }) {
  return (
    <div className="mw-panel mw-panel-etat-simple" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box' }}>
      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>État (simple)</div>
      <div style={{ fontSize: 12 }}>Statut : OK</div>
      <div style={{ fontSize: 12 }}>params : {JSON.stringify(params)}</div>
    </div>
  );
}

export const EtatSimplePanel: PanelDef = {
  id: 'etat-simple',
  labelKey: 'panels.etat-simple.titre',
  iconKey: 'panels.etat-simple.icone',
  version: '1.0.0',
  essential: true,
  paramsSchema: {},
  defaultParams: {},
  declaration: () => '[etat-simple] État simple (variant de test) v1.0.0',
  langEmbedded: ETAT_SIMPLE_LANG_FR,
  component: EtatSimple,
};
