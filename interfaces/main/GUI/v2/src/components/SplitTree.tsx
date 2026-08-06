// SplitTree — rendu récursif d'un arbre résolu : splits, groupes, MiniLayouts.
// Les séparateurs de splits sont draggables (resize en pixels).

import React, { useRef } from 'react';
import type { ResolvedNode } from '../layout/resolve.ts';
import { TabGroup } from './TabGroup.tsx';
import type { PanelOcc } from '../layout/types.ts';

export interface RenderCtx {
  windowId: string;
  t: (k: string) => string;
  /** occId de l'onglet à SURENCOUCHER (déclenché depuis le menu, jusqu'au prochain clic). */
  highlight?: string | null;
  onActivate(groupId: string, occId: string): void;
  onClose(groupId: string, occId: string): void;
  onMove(fromGroup: string, toGroup: string, occId: string, index?: number): void;
  onSplit(groupId: string, dir: 'horizontal' | 'vertical', occId: string, fromGroup?: string): void;
  onExtract(groupId: string, occId: string): void;
  /** renomme un onglet (titre personnalisé) ; label null = retirer le titre. */
  onRename?(groupId: string, occId: string, label: string | null): void;
  /** zoom d'un onglet : fixe sa valeur LOCALE (l'effectif est calculé au rendu). */
  onZoom?(groupId: string, occId: string, localValue: number): void;
  /** verrouille/déverrouille le zoom d'un onglet (ancestorFactor = produit des ancêtres). */
  onZoomLock?(groupId: string, occId: string, ancestorFactor: number): void;
  /** resize : sepPosPx = position du séparateur RELATIVE au container (px). */
  onResize?(splitId: string, index: number, sepPosPx: number, totalPx: number): void;
  /** rend l'onglet ; ancestorFactor = produit des zooms des ancêtres (pour mini-layouts). */
  renderPanel(occ: PanelOcc, ancestorFactor?: number): React.ReactNode;
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
    // Point d'ORIGINE : la position du séparateur (relative au container) au
    // moment du clic = position souris relative (on clique dessus).
    // Puis à chaque mousemove on TRANSLATE depuis l'origine par le déplacement
    // total de la souris : sepPos = origin + (sourisActuelle - sourisDépart).
    // → le séparateur est TOUJOURS sous la souris, sans dérive par accumulation.
    const container = totalRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    const origin = direction === 'horizontal' ? e.clientX - rect.left : e.clientY - rect.top;
    const startPointer = direction === 'horizontal' ? e.clientX : e.clientY;
    const total = direction === 'horizontal' ? rect.width : rect.height;
    if (total <= 0) return;

    const onMove = (ev: MouseEvent) => {
      if (!dragging.current) return;
      const pointer = direction === 'horizontal' ? ev.clientX : ev.clientY;
      const sepPos = origin + (pointer - startPointer); // relative au container
      onResize(splitId, index, sepPos, total);
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

export function SplitTree({ node, ctx, zoomFactor = 1 }: { node: ResolvedNode; ctx: RenderCtx; zoomFactor?: number }) {
  const containerRef = useRef<HTMLDivElement>(null);

  if (node.kind === 'group') {
    return (
      <TabGroup
        group={node}
        windowId={ctx.windowId}
        zoomFactor={zoomFactor}
        t={ctx.t}
        onActivate={(o) => ctx.onActivate(node.id, o)}
        onClose={(o) => ctx.onClose(node.id, o)}
        onMove={ctx.onMove}
        onSplit={ctx.onSplit}
        onExtract={ctx.onExtract}
        onRename={ctx.onRename}
        onZoom={ctx.onZoom}
        onZoomLock={ctx.onZoomLock}
        highlight={ctx.highlight}
        renderPanel={ctx.renderPanel}
      />
    );
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
            <SplitTree node={child} ctx={ctx} zoomFactor={zoomFactor} />
          </div>
        </React.Fragment>
      ))}
    </div>
  );
}
