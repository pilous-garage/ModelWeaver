// installator/outils-installes — outils installés (via tools/installed/list HTTP). Migré de V1.

import React from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll, unwrapResult } from './panel-utils.ts';

const LANG_FR = `
panels:
  installator-outils-installes:
    titre: "Outils installés"
    version: "Version"
    statut: "Statut"
    desinstaller: "Désinstaller"
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  installator-outils-installes:
    titre: "Installed tools"
    version: "Version"
    statut: "Status"
    desinstaller: "Uninstall"
    erreur: "Error"
`;


function InstalledToolsPanel({ ctx }: { ctx: any; params: Record<string, any> }) {
  const { data, error, reload } = usePoll<any>(
    ctx.api.post, 'tools/installed/list', {},
    5000,
    (res) => unwrapResult(res).tools ?? [],
    true,
  );
  const tools = data ?? [];

  const uninstall = async (ref: string) => {
    try { await ctx.api.post('jobs/add', { ref, job_type: 'uninstall' }); reload(); } catch { /* best-effort */ }
  };

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      {error && <div style={{ color: '#f87171', marginBottom: 6 }}>{ctx.t?.('panels.installator-outils-installes.erreur') ?? 'Erreur'} : {error}</div>}
      {tools.length === 0 && !error && <div style={{ color: '#64748b' }}>…</div>}
      {tools.map((t: any) => (
        <div key={t.ref} style={{ display: 'flex', gap: 8, padding: '2px 0', borderBottom: '1px solid var(--mw-border, #1e293b)', alignItems: 'center' }}>
          <span style={{ flex: 1 }}>{t.name ?? t.ref}</span>
          <span style={{ width: 80, color: '#94a3b8' }}>{t.version ?? ''}</span>
          <span style={{ width: 70, color: t.status === 'installed' ? '#4ade80' : '#94a3b8' }}>{t.status}</span>
          <button className="mw-btn" onClick={() => uninstall(t.ref)} style={{ fontSize: 11 }}>
            {ctx.t?.('panels.installator-outils-installes.desinstaller') ?? 'Désinstaller'}
          </button>
        </div>
      ))}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'installator-outils-installes',
  labelKey: 'panels.installator-outils-installes.titre',
  iconKey: 'panels.installator-outils-installes.titre',
  version: '1.0.0',
  essential: true,
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[installator-outils-installes] Outils installés v1.0.0\n  routes: tools/installed/list, jobs/add',
  component: InstalledToolsPanel,
};

export const langFr = LANG_FR;