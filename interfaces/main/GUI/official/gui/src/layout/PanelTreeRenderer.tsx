/** PanelTreeRenderer — Rendu récursif d'un arbre de panneaux */

import React, { useEffect, useState } from 'react';
import { Group, Panel, Separator } from 'react-resizable-panels';
import { PANEL_REGISTRY } from '../panels/index.ts';
import type { PanelTreeNode } from './useLayout.ts';
import { logGui } from '../gui_log.ts';

interface Props {
  tree: PanelTreeNode;
  ctx: any; // PanelContext
}

/** Charge PA RESSEUSEMENT un panel externe référencé mais non chargé. */
function LazyPanel({ id, ctx }: { id: string; ctx: any }) {
  const [def, setDef] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    logGui('panel:lazy-request', { id });
    import('../panels/loader.ts').then((m) =>
      m.ensurePanelLoaded(id).then((d) => {
        if (cancelled) return;
        if (d) setDef(d); else setError('chargement échoué');
      }),
    ).catch((e) => { if (!cancelled) setError(String(e?.message || e)); });
    return () => { cancelled = true; };
  }, [id]);

  if (error) {
    return (
      <div style={{ padding: '1rem', color: 'var(--error, #e84545)' }}>
        Panneau "{id}" : {error}
      </div>
    );
  }
  if (!def) {
    return (
      <div style={{ padding: '1rem', color: '#94a3b8' }}>
        Chargement du panneau "{id}"…
      </div>
    );
  }
  const C = def.component;
  return <C ctx={ctx} />;
}

export function PanelTreeRenderer({ tree, ctx }: Props) {
  if (tree.type === 'panel') {
    if (tree.visible === false) return null;
    const panelDef = PANEL_REGISTRY[tree.id || ''];
    if (!panelDef) {
      // Panel référencé mais absent du registry → essayer de le charger à la
      // demande (panel externe non encore chargé).
      return <LazyPanel id={tree.id || ''} ctx={ctx} />;
    }
    const PanelComponent = panelDef.component;
    return <PanelComponent ctx={ctx} />;
  }

  if (tree.type === 'ext') {
    if (tree.visible === false) return null;
    return (
      <iframe
        src={tree.url || `/extensions/${tree.id}/index.html`}
        style={{ width: '100%', height: '100%', border: 'none', background: 'var(--bg, #1a1a2e)' }}
        title={tree.id}
      />
    );
  }

  // Nœud splitter
  const direction = tree.direction || 'horizontal';
  const children = (tree.children || []).filter((c) => c.visible !== false);

  if (children.length === 0) return null;
  if (children.length === 1) return <PanelTreeRenderer tree={children[0]} ctx={ctx} />;

  return (
    <Group direction={direction} style={{ height: '100%', width: '100%' }}>
      {children.map((child, idx) => (
        <React.Fragment key={child.id || `node-${idx}`}>
          {idx > 0 && (
            <Separator
              style={{
                width: direction === 'horizontal' ? '4px' : '100%',
                height: direction === 'vertical' ? '4px' : undefined,
                background: 'var(--border, #2a2a4a)',
                cursor: direction === 'horizontal' ? 'col-resize' : 'row-resize',
              }}
            />
          )}
          <Panel
            defaultSize={child.sizes?.[0] !== undefined ? child.sizes[idx] : undefined}
            minSize={10}
          >
            <PanelTreeRenderer tree={child} ctx={ctx} />
          </Panel>
        </React.Fragment>
      ))}
    </Group>
  );
}
