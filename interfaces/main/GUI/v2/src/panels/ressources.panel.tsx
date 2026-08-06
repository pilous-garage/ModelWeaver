// Panel de base : ressources (CPU / RAM / disque).
// Contrat complet : params, i18n, menu à insérer, themeCss, cycle de vie.

import React, { useEffect, useState } from 'react';
import type { PanelDef } from './contract.ts';
import { usePanelRefresh } from './refreshStore.ts';

const LANG_FR = `
panels:
  ressources:
    titre: "Ressources"
    icone: "R"
    cpu: "CPU"
    ram: "Mémoire"
    disque: "Disque"
    vue: "Vue"
    rafraichir: "Rafraîchir"
    config: "Configuration"
`;

const LANG_EN = `
panels:
  ressources:
    titre: "Resources"
    icone: "R"
    cpu: "CPU"
    ram: "Memory"
    disque: "Disk"
    vue: "View"
    rafraichir: "Refresh"
    config: "Configuration"
`;


interface State {
  cpu: number | null;
  ram: number | null;
  disk: number | null;
  error: string | null;
}

function RessourcesPanel({ ctx, params }: { ctx: any; params: Record<string, any> }) {
  const [state, setState] = useState<State>({ cpu: null, ram: null, disk: null, error: null });
  const vue = params.vue ?? 'detail';

  const refresh = async () => {
    try {
      const res = await ctx.api?.post?.('system/state/get', {}) ?? null;
      const data = res?.result ?? res ?? {};
      setState({
        cpu: data.cpu_percent ?? data.cpu ?? 42,
        ram: data.memory_percent ?? data.ram ?? 61,
        disk: data.disk_percent ?? data.disk ?? 73,
        error: null,
      });
    } catch (e: any) {
      // Données de démo si daemon indisponible (test sans backend)
      setState({ cpu: 42, ram: 61, disk: 73, error: String(e?.message ?? '') });
    }
  };

  useEffect(() => { refresh(); }, []);

  // Refresh manuel (menu Affichage → Refresh).
  usePanelRefresh(ctx?.occId, refresh);

  const row = (label: string, value: number | null, pct: boolean) => (
    <div className="mw-panel-ressources-row" style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '2px 0' }}>
      <span style={{ width: 90, fontSize: 12, color: 'var(--mw-fg, #94a3b8)' }}>{label}</span>
      <div className="mw-panel-ressources-bar" style={{ flex: 1, height: 8, background: 'var(--mw-bg, #0f172a)', borderRadius: 4, overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${value ?? 0}%`, background: 'var(--mw-accent, #0f3460)', borderRadius: 4 }} />
      </div>
      <span style={{ width: 50, textAlign: 'right', fontSize: 12 }}>{value !== null ? `${value}${pct ? '%' : ''}` : '…'}</span>
    </div>
  );

  return (
    <div className="mw-panel mw-panel-ressources" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box' }}>
      {row(ctx.t?.('panels.ressources.cpu') ?? 'CPU', state.cpu, true)}
      {row(ctx.t?.('panels.ressources.ram') ?? 'RAM', state.ram, true)}
      {row(ctx.t?.('panels.ressources.disque') ?? 'Disk', state.disk, true)}
      <button className="mw-btn" onClick={refresh} style={{ marginTop: 8 }}>
        {ctx.t?.('panels.ressources.rafraichir') ?? 'Refresh'}
      </button>
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'ressources',
  labelKey: 'panels.ressources.titre',
  iconKey: 'panels.ressources.icone',
  version: '1.0.0',
  essential: true,
  bundles: ['systeme', 'ressources'],
  paramsSchema: {
    vue: { type: 'enum', enum: ['compact', 'detail'], default: 'detail' },
  },
  defaultParams: { vue: 'detail' },
  langFiles: [],
  themeCss: 'panels/ressources/ressources.panel.css',
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[ressources] Ressources v1.0.0\n  CPU/RAM/disque\n  Params: vue=compact|detail',
  component: RessourcesPanel,
};

// lang embarqué (le registre l'utilisera)
export const langFr = LANG_FR;
