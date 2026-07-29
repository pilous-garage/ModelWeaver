/** PanelTreeRenderer — Rendu récursif d'un arbre de panneaux */

import React from 'react';
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels';
import { PANEL_REGISTRY } from '../panels/index.ts';
import type { PanelTreeNode } from './useLayout.ts';

interface Props {
  tree: PanelTreeNode;
  ctx: any; // PanelContext
}

export function PanelTreeRenderer({ tree, ctx }: Props) {
  if (tree.type === 'panel') {
    if (tree.visible === false) return null;
    const panelDef = PANEL_REGISTRY[tree.id || ''];
    if (!panelDef) {
      return (
        <div style={{ padding: '1rem', color: 'var(--error, #e84545)' }}>
          Panneau inconnu : "{tree.id}"
        </div>
      );
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
    <PanelGroup direction={direction} style={{ height: '100%', width: '100%' }}>
      {children.map((child, idx) => (
        <React.Fragment key={child.id || `node-${idx}`}>
          {idx > 0 && (
            <PanelResizeHandle
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
    </PanelGroup>
  );
}
