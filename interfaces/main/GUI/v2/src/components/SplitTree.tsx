// SplitTree — rendu récursif d'un arbre résolu : splits, groupes, MiniLayouts.
// Les séparateurs de splits sont draggables (resize en pixels).

import React, { useRef } from 'react';
import type { ResolvedNode } from '../layout/resolve.ts';
import { TabGroup } from './TabGroup.tsx';
import type { PanelOcc } from '../layout/types.ts';

export interface RenderCtx {
  windowId: string;
  t: (k: string) => string;
  onActivate(groupId: string, occId: string): void;
  onClose(groupId: string, occId: string): void;
  onMove(fromGroup: string, toGroup: string, occId: string, index?: number): void;
  onSplit(groupId: string, dir: 'horizontal' | 'vertical', occId: string, fromGroup?: string): void;
  onExtract(groupId: string, occId: string): void;
  onCloseMiniLayout?(miniLayoutId: string): void;
  onResize?(splitId: string, index: number, deltaPx: number, totalPx: number): void;
  renderPanel(occ: PanelOcc): React.ReactNode;
  renderMiniLayout?(slip: any, close?: () => void): React.ReactNode;
}

// ── Separator (drag souris custom — pas de DragEvent natif) ───────────

function Separator({ splitId, index, direction, totalRef, onResize }: {
  splitId: string;
  index: number;
  direction: 'horizontal' | 'vertical';
  totalRef: React.RefObject<HTMLDivElement>;
  onResize?: RenderCtx['onResize'];
}) {
  const dragging = useRef(false);

  const startDrag = (e: React.MouseEvent) => {
    if (!onResize) return;
    e.preventDefault();
    e.stopPropagation();
    dragging.current = true;
    // Dernière position du pointeur : le delta est INCÉRÉMENTAL (depuis le
    // dernier mousemove), pas total depuis le mousedown. Sinon, chaque mousemove
    // ré-applique le déplacement complet au layout déjà redimensionné → le
    // séparateur "dérape" (double-compte).
    let lastX = e.clientX;
    let lastY = e.clientY;

    const onMove = (ev: MouseEvent) => {
      if (!dragging.current) return;
      const dx = ev.clientX - lastX;
      const dy = ev.clientY - lastY;
      lastX = ev.clientX;
      lastY = ev.clientY;
      const delta = direction === 'horizontal' ? dx : dy;
      const total = direction === 'horizontal'
        ? (totalRef.current?.clientWidth ?? 600)
        : (totalRef.current?.clientHeight ?? 400);
      if (total > 0) onResize(splitId, index, delta, total);
    };
    const onUp = () => {
      dragging.current = false;
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    document.body.style.cursor = direction === 'horizontal' ? 'col-resize' : 'row-resize';
    document.body.style.userSelect = 'none';
  };

  return (
    <div
      className="mw-split-separator"
      data-testid={`split-sep-${splitId}-${index}`}
      onMouseDown={startDrag}
      style={{
        background: 'var(--mw-border, #334155)',
        flexShrink: 0,
        [direction === 'horizontal' ? 'width' : 'height']: 4,
        cursor: direction === 'horizontal' ? 'col-resize' : 'row-resize',
        zIndex: 5,
      }}
    />
  );
}

// ── SplitTree ─────────────────────────────────────────────────────────

export function SplitTree({ node, ctx }: { node: ResolvedNode; ctx: RenderCtx }) {
  const containerRef = useRef<HTMLDivElement>(null);

  if (node.kind === 'group') {
    return (
      <TabGroup
        group={node}
        windowId={ctx.windowId}
        t={ctx.t}
        onActivate={(o) => ctx.onActivate(node.id, o)}
        onClose={(o) => ctx.onClose(node.id, o)}
        onMove={ctx.onMove}
        onSplit={ctx.onSplit}
        onExtract={ctx.onExtract}
        renderPanel={ctx.renderPanel}
      />
    );
  }
  if (node.kind === "miniLayout") {
    if (!ctx.renderMiniLayout) return null;
    const close = ctx.onCloseMiniLayout ? () => ctx.onCloseMiniLayout!(node.id) : undefined;
    return ctx.renderMiniLayout(node, close);
  }
  // split
  const hasResize = typeof ctx.onResize === 'function';
  return (
    <div
      ref={containerRef}
      className="mw-split-root"
      data-direction={node.direction}
      data-testid={`split-${node.id}`}
      style={{
        display: 'flex',
        flexDirection: node.direction === 'horizontal' ? 'row' : 'column',
        height: '100%', width: '100%', minHeight: 0, minWidth: 0,
      }}
    >
      {node.children.map((child, i) => (
        <React.Fragment key={i}>
          {i > 0 && (
            <Separator
              splitId={node.id}
              index={i - 1}
              direction={node.direction}
              totalRef={containerRef}
              onResize={hasResize ? ctx.onResize : undefined}
            />
          )}
          <div style={{ flex: node.sizes[i] ?? 1, minHeight: 0, minWidth: 0, overflow: 'hidden' }}>
            <SplitTree node={child} ctx={ctx} />
          </div>
        </React.Fragment>
      ))}
    </div>
  );
}
