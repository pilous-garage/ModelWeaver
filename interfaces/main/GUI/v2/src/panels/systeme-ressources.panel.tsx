// systeme/ressources — ressources système temps réel (poll 2s). Migré de V1.

import React from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll, unwrapResult } from './panel-utils.ts';

const LANG_FR = `
panels:
  systeme-ressources:
    titre: "Ressources"
    gpus: "GPU"
    reseau: "Réseau"
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  systeme-ressources:
    titre: "Resources"
    gpus: "GPU"
    reseau: "Network"
    erreur: "Error"
`;


function RessourcesSystemePanel({ ctx }: { ctx: any; params: Record<string, any> }) {
  const { data, error } = usePoll<any>(
    ctx.api.post, 'system/resources', {},
    2000,
    unwrapResult,
  );
  const gpus = data?.gpus ?? [];
  const net = data?.network ?? {};

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      {error && <div style={{ color: '#f87171', marginBottom: 6 }}>{ctx.t?.('panels.systeme-ressources.erreur') ?? 'Erreur'} : {error}</div>}
      {gpus.length > 0 && (
        <>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>{ctx.t?.('panels.systeme-ressources.gpus') ?? 'GPU'}</div>
          {gpus.map((g: any, i: number) => (
            <div key={i} style={{ display: 'flex', gap: 8, padding: '2px 0', borderBottom: '1px solid var(--mw-border, #1e293b)' }}>
              <span style={{ flex: 1 }}>{g.name ?? g.model ?? g.id}</span>
              <span style={{ color: '#94a3b8' }}>{g.utilization != null ? `${g.utilization}%` : ''}</span>
              <span style={{ color: '#64748b' }}>{g.memory_used != null ? `${g.memory_used}/${g.memory_total ?? '?'} MB` : ''}</span>
            </div>
          ))}
        </>
      )}
      {net?.interfaces && (
        <>
          <div style={{ fontWeight: 600, margin: '8px 0 4px' }}>{ctx.t?.('panels.systeme-ressources.reseau') ?? 'Réseau'}</div>
          {Object.entries(net.interfaces).map(([name, v]: [string, any]) => (
            <div key={name} style={{ display: 'flex', gap: 8, padding: '2px 0', borderBottom: '1px solid var(--mw-border, #1e293b)' }}>
              <span style={{ flex: 1 }}>{name}</span>
              <span style={{ color: '#94a3b8' }}>{v?.rx_bps != null ? `${(v.rx_bps / 1024).toFixed(0)} Ko/s ↓` : ''}</span>
              <span style={{ color: '#64748b' }}>{v?.tx_bps != null ? `${(v.tx_bps / 1024).toFixed(0)} Ko/s ↑` : ''}</span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'systeme-ressources',
  labelKey: 'panels.systeme-ressources.titre',
  iconKey: 'panels.systeme-ressources.titre',
  version: '1.0.0',
  essential: true,
  bundles: ['systeme'],
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[systeme-ressources] Ressources v1.0.0\n  routes: system/resources',
  component: RessourcesSystemePanel,
};

export const langFr = LANG_FR;