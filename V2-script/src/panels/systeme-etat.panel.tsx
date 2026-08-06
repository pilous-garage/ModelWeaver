// systeme/etat — état du matériel (one-shot). Migré de V1.

import React from 'react';
import type { PanelDef } from './contract.ts';
import { useEffect, useState } from 'react';

const LANG_FR = `
panels:
  systeme-etat:
    titre: "État système"
    cpu: "CPU"
    memoire: "Mémoire"
    carte: "Carte mère"
    gpus: "GPU"
    disques: "Disques"
    reseau: "Réseau"
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  systeme-etat:
    titre: "System state"
    cpu: "CPU"
    memoire: "Memory"
    carte: "Motherboard"
    gpus: "GPU"
    disques: "Disks"
    reseau: "Network"
    erreur: "Error"
`;


function SystemeEtatPanel({ ctx }: { ctx: any; params: Record<string, any> }) {
  const [hw, setHw] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    ctx.api.post('system/hardware', {}).then((res: any) => {
      if (alive) setHw(res?.result ?? {});
    }).catch((e: any) => {
      if (alive) setErr(String(e?.message ?? e));
    });
    return () => { alive = false; };
  }, [ctx.api.post]);

  const row = (label: string, value: any) => (
    <div style={{ display: 'flex', gap: 8, padding: '2px 0', borderBottom: '1px solid var(--mw-border, #1e293b)', fontSize: 12 }}>
      <span style={{ width: 100, color: '#64748b' }}>{label}</span>
      <span style={{ flex: 1 }}>{value ?? '—'}</span>
    </div>
  );

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box' }}>
      {err && <div style={{ color: '#f87171', marginBottom: 6, fontSize: 12 }}>{ctx.t?.('panels.systeme-etat.erreur') ?? 'Erreur'} : {err}</div>}
      {row(ctx.t?.('panels.systeme-etat.cpu') ?? 'CPU', hw?.cpu?.name ?? hw?.cpu?.model)}
      {row(ctx.t?.('panels.systeme-etat.memoire') ?? 'Mémoire', hw?.memory?.total ? `${(hw.memory.total / (1024 ** 3)).toFixed(1)} Go` : null)}
      {row(ctx.t?.('panels.systeme-etat.carte') ?? 'Carte mère', hw?.motherboard?.model ?? hw?.motherboard?.name)}
      {row(ctx.t?.('panels.systeme-etat.gpus') ?? 'GPU', Array.isArray(hw?.gpus) ? hw.gpus.map((g: any) => g.name ?? g.model).join(', ') : null)}
      {row(ctx.t?.('panels.systeme-etat.disques') ?? 'Disques', Array.isArray(hw?.disks) ? hw.disks.length : null)}
      {row(ctx.t?.('panels.systeme-etat.reseau') ?? 'Réseau', Array.isArray(hw?.network?.interfaces) ? hw.network.interfaces.map((n: any) => n.name).join(', ') : null)}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'systeme-etat',
  labelKey: 'panels.systeme-etat.titre',
  iconKey: 'panels.systeme-etat.titre',
  version: '1.0.0',
  essential: true,
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[systeme-etat] État système v1.0.0\n  routes: system/hardware',
  component: SystemeEtatPanel,
};

export const langFr = LANG_FR;