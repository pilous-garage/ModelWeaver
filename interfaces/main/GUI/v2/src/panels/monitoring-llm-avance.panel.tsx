// monitoring/llm-avance — graphes de consommation LLM (token/min, req/min).
// Sources : usage/series (série temporelle par global/agent/provider/
// provider_model), usage/monitor (fenêtres).
// Pas de lib de chart : graphe SVG de barres léger (inline).

import React, { useEffect, useRef, useState } from 'react';
import type { PanelDef } from './contract.ts';

const LANG_FR = `
panels:
  monitoring-llm-avance:
    titre: "Monitoring LLM avancé"
    fenetre: "Fenêtre"
    dim: "Dimension"
    global: "Global"
    agent: "Par agent"
    provider: "Par provider"
    model: "Par provider/modèle"
    reqMin: "Requêtes / min"
    tokMin: "Tokens / min"
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  monitoring-llm-avance:
    titre: "Advanced LLM monitoring"
    fenetre: "Window"
    dim: "Dimension"
    global: "Global"
    agent: "By agent"
    provider: "By provider"
    model: "By provider/model"
    reqMin: "Requests / min"
    tokMin: "Tokens / min"
    erreur: "Error"
`;

const WINDOWS = ['5m', '15m', '1h', '4h', '24h', '7j'];
const DIMS = [
  { id: 'global', labelKey: 'global' },
  { id: 'agent', labelKey: 'agent' },
  { id: 'provider', labelKey: 'provider' },
  { id: 'provider_model', labelKey: 'model' },
];

const PALETTE = ['#60a5fa', '#4ade80', '#fbbf24', '#f87171', '#a78bfa', '#34d399', '#f472b6', '#22d3ee'];


function BarChart({ points, height, colorKey }: { points: any[]; height: number; colorKey: (i: number) => string }) {
  if (!points || points.length === 0) return <div style={{ fontSize: 11, color: '#475569' }}>—</div>;
  const W = 640, H = height;
  const max = Math.max(...points.map((p) => Math.max(p.req ?? 0, p.tokens_in ?? 0, 1)), 1);
  const barW = W / points.length;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: H, display: 'block' }} preserveAspectRatio="none">
      {points.map((p, i) => {
        const h = ((p.tokens_in ?? 0) / max) * (H - 12);
        return (
          <g key={i}>
            <rect x={i * barW + 1} y={H - 4 - h} width={Math.max(barW - 2, 1)} height={Math.max(h, 1)} fill={colorKey(i)} opacity={0.85} rx={1} />
          </g>
        );
      })}
      <line x1="0" y1={H - 4} x2={W} y2={H - 4} stroke="#334155" strokeWidth="1" />
    </svg>
  );
}


function LlmAvancePanel({ ctx, params }: { ctx: any; params: Record<string, any> }) {
  const [window, setWindow] = useState(params.window ?? '1h');
  const [dim, setDim] = useState(params.dim ?? 'global');
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    const tick = async () => {
      try {
        const res = await ctx.api.post('usage/series', { window, dim });
        if (alive.current) { setData(res?.result ?? {}); setErr(null); }
      } catch (e: any) {
        if (alive.current) setErr(String(e?.message ?? e));
      }
    };
    tick();
    const iv = setInterval(tick, 15000);
    return () => { alive.current = false; clearInterval(iv); };
  }, [ctx.api.post, window, dim]);

  const series = data?.series ?? {};
  const global = data?.global ?? [];
  const keys = Object.keys(series);

  const fmt = (n: number | null | undefined) => n == null ? '0' : Number(n).toLocaleString('fr-FR');
  const dimLabel = (k: string) => {
    if (dim === 'provider_model') return k;
    if (dim === 'agent') return `agent ${k}`;
    return k;
  };

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      {err && <div style={{ color: '#f87171', marginBottom: 6 }}>{ctx.t?.('panels.monitoring-llm-avance.erreur') ?? 'Erreur'} : {err}</div>}

      {/* Sélecteurs */}
      <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 8, flexWrap: 'wrap' }}>
        {WINDOWS.map((wd) => (
          <button key={wd} onClick={() => setWindow(wd)} className="mw-btn"
            style={{ padding: '2px 8px', fontSize: 11, opacity: wd === window ? 1 : 0.6 }}>
            {wd}
          </button>
        ))}
        <span style={{ width: 8 }} />
        {DIMS.map((d) => (
          <button key={d.id} onClick={() => setDim(d.id)} className="mw-btn"
            style={{ padding: '2px 8px', fontSize: 11, opacity: d.id === dim ? 1 : 0.6 }}>
            {ctx.t?.(`panels.monitoring-llm-avance.${d.labelKey}`) ?? d.labelKey}
          </button>
        ))}
      </div>

      {/* Graphe global */}
      <div style={{ fontWeight: 700, margin: '4px 0', color: '#67e8f9' }}>{ctx.t?.('panels.monitoring-llm-avance.global') ?? 'Global'}</div>
      <div style={{ fontSize: 11, color: '#94a3b8', marginBottom: 2 }}>{ctx.t?.('panels.monitoring-llm-avance.tokMin') ?? 'Tokens / min'}</div>
      <BarChart points={global} height={80} colorKey={() => '#60a5fa'} />
      <div style={{ fontSize: 11, color: '#94a3b8', margin: '6px 0 2px' }}>{ctx.t?.('panels.monitoring-llm-avance.reqMin') ?? 'Requêtes / min'}</div>
      <BarChart points={global} height={50} colorKey={() => '#4ade80'} />

      {/* Par voie */}
      {dim !== 'global' && (
        <>
          <div style={{ fontWeight: 700, margin: '10px 0 4px', color: '#a5b4fc' }}>
            {dim === 'agent' ? (ctx.t?.('panels.monitoring-llm-avance.agent') ?? 'Par agent')
              : dim === 'provider' ? (ctx.t?.('panels.monitoring-llm-avance.provider') ?? 'Par provider')
              : (ctx.t?.('panels.monitoring-llm-avance.model') ?? 'Par provider/modèle')}
          </div>
          {keys.map((k, ki) => (
            <div key={k} style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: '#e2e8f0' }}>{dimLabel(k)}</div>
              <BarChart points={series[k] ?? []} height={40} colorKey={() => PALETTE[ki % PALETTE.length]} />
            </div>
          ))}
        </>
      )}

      {/* Résumé global */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4, marginTop: 8, fontSize: 11 }}>
        <div>{ctx.t?.('panels.monitoring-llm-avance.reqMin') ?? 'Requêtes'} : {fmt(global.reduce((a: number, p: any) => a + (p.requests ?? 0), 0))}</div>
        <div>{ctx.t?.('panels.monitoring-llm-avance.tokMin') ?? 'Tokens'} : {fmt(global.reduce((a: number, p: any) => a + (p.tokens_in ?? 0) + (p.tokens_out ?? 0), 0))}</div>
      </div>
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'monitoring-llm-avance',
  labelKey: 'panels.monitoring-llm-avance.titre',
  iconKey: 'panels.monitoring-llm-avance.titre',
  version: '1.0.0',
  essential: false,
  bundles: ['monitoring'],
  paramsSchema: {
    window: { type: 'enum', enum: WINDOWS, default: '1h' },
    dim: { type: 'enum', enum: ['global', 'agent', 'provider', 'provider_model'], default: 'global' },
  },
  defaultParams: { window: '1h', dim: 'global' },
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[monitoring-llm-avance] Monitoring LLM avancé v1.0.0\n  graphes token/min + req/min (global/agent/provider/provider_model)\n  routes: usage/series, usage/monitor',
  component: LlmAvancePanel,
};

export const langFr = LANG_FR;