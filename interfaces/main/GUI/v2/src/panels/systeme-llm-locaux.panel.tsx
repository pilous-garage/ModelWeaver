// systeme/llm-locaux — moteurs LLM locaux (Ollama, LM Studio…). Migré de V1.
// Routes : llm/local/list (poll 5s), llm/local/start, llm/local/stop.

import React from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll } from './panel-utils.ts';

const LANG_FR = `
panels:
  systeme-llm-locaux:
    titre: "LLM locaux"
    nom: "Moteur"
    statut: "Statut"
    port: "Port"
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  systeme-llm-locaux:
    titre: "Local LLMs"
    nom: "Engine"
    statut: "Status"
    port: "Port"
    erreur: "Error"
`;


function LlmLocauxPanel({ ctx }: { ctx: any; params: Record<string, any> }) {
  const { data, error, reload } = usePoll<any>(
    ctx.api.post, 'llm/local/list', {}, 5000,
    (res) => res?.result ?? res ?? [],
    true,
  );
  const engines = Array.isArray(data) ? data : data?.engines ?? [];

  const act = async (action: string, name: string) => {
    try { await ctx.api.post(`llm/local/${action}`, { name }); reload(); } catch { /* best-effort */ }
  };

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      {error && <div style={{ color: '#f87171', marginBottom: 6 }}>{ctx.t?.('panels.systeme-llm-locaux.erreur') ?? 'Erreur'} : {error}</div>}
      {engines.length === 0 && !error && <div style={{ color: '#64748b' }}>Aucun moteur détecté</div>}
      {engines.map((e: any, i: number) => (
        <div key={e.name ?? i} style={{ display: 'flex', gap: 8, padding: '3px 0', borderBottom: '1px solid var(--mw-border, #1e293b)', alignItems: 'center' }}>
          <span style={{ flex: 1, fontWeight: 600 }}>{e.name ?? e.engine ?? e.id}</span>
          <span style={{ color: e.running ? '#4ade80' : '#94a3b8' }}>{e.running ? '●' : '○'}</span>
          <span style={{ color: '#64748b' }}>{e.port ?? ''}</span>
          {e.running ? (
            <button className="mw-btn" onClick={() => act('stop', e.name)} style={{ fontSize: 11 }}>■</button>
          ) : (
            <button className="mw-btn" onClick={() => act('start', e.name)} style={{ fontSize: 11 }}>▶</button>
          )}
        </div>
      ))}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'systeme-llm-locaux',
  labelKey: 'panels.systeme-llm-locaux.titre',
  iconKey: 'panels.systeme-llm-locaux.titre',
  version: '1.0.0',
  essential: false,
  bundles: ['systeme'],
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[systeme-llm-locaux] LLM locaux v1.0.0\n  routes: llm/local/list, llm/local/start, llm/local/stop',
  component: LlmLocauxPanel,
};

export const langFr = LANG_FR;