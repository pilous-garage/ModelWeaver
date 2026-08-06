// monitoring/llm-distant — moniteur d'usage LLM (poll 15s, fenêtres 1h/24h/7j). Migré de V1.

import React from 'react';
import type { PanelDef } from './contract.ts';
import { useEffect, useRef, useState } from 'react';

const LANG_FR = `
panels:
  monitoring-llm-distant:
    titre: "Moniteur LLM"
    fenetre: "Fenêtre"
    requetes: "Requêtes"
    tokensIn: "Tokens in"
    tokensOut: "Tokens out"
    cout: "Coût"
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  monitoring-llm-distant:
    titre: "LLM monitor"
    fenetre: "Window"
    requetes: "Requests"
    tokensIn: "Tokens in"
    tokensOut: "Tokens out"
    cout: "Cost"
    erreur: "Error"
`;


const WINDOWS = ['1h', '24h', '7j'];

function LlmMonitorPanel({ ctx, params }: { ctx: any; params: Record<string, any> }) {
  const [data, setData] = useState<any>(null);
  const [window, setWindow] = useState(params.window ?? '24h');
  const [err, setErr] = useState<string | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    const tick = async () => {
      try {
        const res = await ctx.api.post('usage/monitor', {});
        if (alive.current) { setData(res?.result ?? {}); setErr(null); }
      } catch (e: any) {
        if (alive.current) setErr(String(e?.message ?? e));
      }
    };
    tick();
    const iv = setInterval(tick, 15000);
    return () => { alive.current = false; clearInterval(iv); };
  }, [ctx.api.post]);

  const w = data?.windows?.[window];
  const summary = w?.summary ?? {};
  const models = w?.models ?? [];

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
        {WINDOWS.map((wd) => (
          <button
            key={wd}
            onClick={() => setWindow(wd)}
            className="mw-btn"
            style={{ padding: '2px 8px', fontSize: 11, opacity: wd === window ? 1 : 0.6 }}
          >{wd}</button>
        ))}
      </div>
      {err && <div style={{ color: '#f87171', marginBottom: 6 }}>{ctx.t?.('panels.monitoring-llm-distant.erreur') ?? 'Erreur'} : {err}</div>}
      {summary && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4, marginBottom: 8 }}>
          <div>{ctx.t?.('panels.monitoring-llm-distant.requetes') ?? 'Requêtes'} : {summary.requests ?? 0}</div>
          <div>{ctx.t?.('panels.monitoring-llm-distant.tokensIn') ?? 'Tokens in'} : {summary.tokens_in ?? 0}</div>
          <div>{ctx.t?.('panels.monitoring-llm-distant.tokensOut') ?? 'Tokens out'} : {summary.tokens_out ?? 0}</div>
          <div>{ctx.t?.('panels.monitoring-llm-distant.cout') ?? 'Coût'} : {summary.cost ?? 0}</div>
        </div>
      )}
      {models.map((m: any, i: number) => (
        <div key={i} style={{ display: 'flex', gap: 8, padding: '2px 0', borderBottom: '1px solid var(--mw-border, #1e293b)' }}>
          <span style={{ flex: 1 }}>{m.name ?? m.id}</span>
          <span style={{ color: '#94a3b8' }}>{m.requests ?? 0}</span>
          <span style={{ color: '#64748b' }}>{m.tokens_out ?? 0}</span>
        </div>
      ))}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'monitoring-llm-distant',
  labelKey: 'panels.monitoring-llm-distant.titre',
  iconKey: 'panels.monitoring-llm-distant.titre',
  version: '1.0.0',
  essential: false,
  bundles: ['monitoring'],
  paramsSchema: {
    window: { type: 'enum', enum: ['1h', '24h', '7j'], default: '24h' },
  },
  defaultParams: { window: '24h' },
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[monitoring-llm-distant] Moniteur LLM v1.0.0\n  routes: usage/monitor',
  component: LlmMonitorPanel,
};

export const langFr = LANG_FR;