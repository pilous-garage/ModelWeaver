// TabGroup — groupe d'onglets (barre + contenu du panel actif).
// Drag/drop custom coordonné PARTOUT (dragStore.ts) :
//   - CLIC simple (pas de déplacement / timer expiré) → onActivate (jamais split)
//   - DRAG (mouvement > seuil) :
//       * ghost (clone flottant) suit le curseur partout
//       * le groupe SURVOLÉ devient la cible (cross-group !) et affiche les
//         marqueurs :
//           - barre d'onglets → marqueur d'insertion (replacement à l'index)
//           - centre du corps → ajout en fin de file du groupe cible
//           - bord (10%)      → aperçu du split (overlay sur le groupe cible)
//       * au relâcher : moveTab / splitGroup sur la cible (mutation du layout)
// L'annulation du drag se fait par clic droit ou Echap (voir dnd.ts).

import React, { useCallback, useEffect, useRef, useState } from 'react';
import type { PanelOcc } from '../layout/types.ts';
import { getPanel } from '../panels/registry.ts';
import type { ResolvedGroup } from '../layout/resolve.ts';
import { startDrag } from '../dnd.ts';
import { registerGroup, computeDrop, setDrop, clearDrop, useDropState } from '../dragStore.ts';

interface Props {
  group: ResolvedGroup;
  windowId: string;
  t: (k: string) => string;
  onActivate(occId: string): void;
  onClose(occId: string): void;
  onMove(fromGroup: string, toGroup: string, occId: string, index?: number): void;
  onSplit(groupId: string, dir: 'horizontal' | 'vertical', occId: string, fromGroup?: string): void;
  onExtract(groupId: string, occId: string): void;
  renderPanel(occ: PanelOcc): React.ReactNode;
}

// Seuil de mouvement (px) : au-delà, c'est un drag ; en-deçà, un clic.
const DRAG_THRESHOLD = 5;
// Délai (ms) : si le pointeur ne bouge pas dans ce délai, ce n'est pas un drag.
const CLICK_TIMER_MS = 180;

export function TabGroup({ group, windowId, t, onActivate, onClose, onMove, onSplit, onExtract, renderPanel }: Props) {
  const barRef = useRef<HTMLDivElement>(null);
  const groupRef = useRef<HTMLDivElement>(null);
  const ghostRef = useRef<HTMLDivElement>(null);
  const draggingOcc = useRef<string>('');
  const [dragging, setDragging] = useState(false);
  const [ghostPos, setGhostPos] = useState<{ x: number; y: number } | null>(null);
  const drop = useDropState();
  // est-ce que CE groupe est la cible du drag en cours ?
  const isTarget = drop.targetGroupId === group.id;

  const labelOf = (occ: PanelOcc) => {
    const def = getPanel(occ.panel);
    return def ? (t(def.labelKey) !== def.labelKey ? t(def.labelKey) : def.id) : occ.panel;
  };

  /** Index d'insertion dans la barre selon le curseur (parmi les onglets). */
  const computeIndex = useCallback((x: number, excludeOccId: string): number => {
    const bar = barRef.current;
    if (!bar) return 0;
    const tabs = Array.from(bar.querySelectorAll<HTMLElement>('[data-occ]'))
      .filter((el) => el.dataset.occ !== excludeOccId);
    for (let i = 0; i < tabs.length; i++) {
      const r = tabs[i].getBoundingClientRect();
      if (x < r.x + r.width / 2) return i;
    }
    return tabs.length;
  }, []);

  // Enregistre ce groupe dans le registre global (pour le ciblage cross-group).
  useEffect(() => {
    const detach = registerGroup({
      id: group.id,
      rect: () => groupRef.current?.getBoundingClientRect() as DOMRect,
      barRect: () => barRef.current?.getBoundingClientRect() as DOMRect,
      computeIndex,
    });
    return detach;
  }, [group.id, computeIndex]);

  const restoreBody = () => {
    document.body.style.userSelect = '';
    document.body.style.cursor = '';
  };

  const handleMouseDown = (e: React.MouseEvent, occ: PanelOcc) => {
    if (e.button !== 0) return;
    const start = { x: e.clientX, y: e.clientY };
    const tabEl = e.currentTarget as HTMLElement;
    const tabRect = tabEl.getBoundingClientRect();
    const offsetX = start.x - tabRect.left;
    const offsetY = start.y - tabRect.top;
    let dragStarted = false;
    let timer: number | undefined = window.setTimeout(() => { timer = undefined; }, CLICK_TIMER_MS);

    const cleanup = () => {
      window.removeEventListener('mousemove', onDragMove);
      window.removeEventListener('mouseup', onUp);
      if (timer !== undefined) window.clearTimeout(timer);
    };

    const onDragMove = (ev: MouseEvent) => {
      const dx = ev.clientX - start.x;
      const dy = ev.clientY - start.y;
      if (!dragStarted && Math.hypot(dx, dy) > DRAG_THRESHOLD) {
        dragStarted = true;
        draggingOcc.current = occ.occId;
        if (timer !== undefined) { window.clearTimeout(timer); timer = undefined; }
        e.preventDefault();
        setDragging(true);
        setGhostPos({ x: start.x - offsetX, y: start.y - offsetY });
        startDrag(
          { occId: occ.occId, panelId: occ.panel, fromGroupId: group.id, fromWindowId: windowId, x: start.x, y: start.y },
          () => { setDragging(false); setGhostPos(null); draggingOcc.current = ''; clearDrop(); restoreBody(); },
        );
        document.body.style.userSelect = 'none';
        document.body.style.cursor = 'grabbing';
      }
      if (dragStarted) {
        if (ghostRef.current) {
          ghostRef.current.style.left = `${ev.clientX - offsetX}px`;
          ghostRef.current.style.top = `${ev.clientY - offsetY}px`;
        }
        // cible = groupe survolé (peut être CE groupe ou un autre)
        setDrop(computeDrop(ev.clientX, ev.clientY, occ.occId));
      }
    };

    const onUp = (ev: MouseEvent) => {
      if (dragStarted) {
        const d = computeDrop(ev.clientX, ev.clientY, occ.occId);
        if (d.zone === 'bar' && d.targetGroupId) {
          onMove(group.id, d.targetGroupId, occ.occId, d.insertIndex ?? undefined);
        } else if (d.zone === 'center' && d.targetGroupId) {
          onMove(group.id, d.targetGroupId, occ.occId); // fin de file
        } else if ((d.zone === 'left' || d.zone === 'right') && d.targetGroupId) {
          onSplit(d.targetGroupId, 'horizontal', occ.occId, group.id);
        } else if ((d.zone === 'top' || d.zone === 'bottom') && d.targetGroupId) {
          onSplit(d.targetGroupId, 'vertical', occ.occId, group.id);
        }
        setDragging(false); setGhostPos(null); draggingOcc.current = ''; clearDrop(); restoreBody();
      }
      cleanup();
    };

    window.addEventListener('mousemove', onDragMove);
    window.addEventListener('mouseup', onUp);
  };

  const isSplit = isTarget && (drop.zone === 'left' || drop.zone === 'right' || drop.zone === 'top' || drop.zone === 'bottom');

  // Position du marqueur d'insertion dans LA barre de CE groupe.
  const markerLeft = (() => {
    const bar = barRef.current;
    if (!isTarget || drop.zone !== 'bar' || drop.insertIndex === null || !bar) return null;
    const tabs = Array.from(bar.querySelectorAll<HTMLElement>('[data-occ]'))
      .filter((el) => el.dataset.occ !== drop.excludeOccId);
    const barRect = bar.getBoundingClientRect();
    if (drop.insertIndex === 0) {
      const first = tabs[0];
      if (first) return first.getBoundingClientRect().left - barRect.left - 1;
      return 4;
    }
    const prev = tabs[drop.insertIndex - 1];
    if (prev) return prev.getBoundingClientRect().right - barRect.left + 1;
    return bar.clientWidth - 4;
  })();

  // Aperçu du split (overlay sur le groupe cible).
  const splitPreview = (() => {
    if (!isSplit || !groupRef.current) return null;
    const grp = groupRef.current.getBoundingClientRect();
    const style: React.CSSProperties = {
      position: 'absolute', zIndex: 30, background: 'var(--mw-accent, #3b82f6)',
      opacity: 0.25, border: '2px dashed var(--mw-accent, #3b82f6)',
      borderRadius: 6, pointerEvents: 'none',
    };
    if (drop.zone === 'left') Object.assign(style, { left: 0, top: 0, bottom: 0, width: Math.round(grp.width * 0.35) });
    if (drop.zone === 'right') Object.assign(style, { right: 0, top: 0, bottom: 0, width: Math.round(grp.width * 0.35) });
    if (drop.zone === 'top') Object.assign(style, { left: 0, right: 0, top: 0, height: Math.round(grp.height * 0.35) });
    if (drop.zone === 'bottom') Object.assign(style, { left: 0, right: 0, bottom: 0, height: Math.round(grp.height * 0.35) });
    return style;
  })();

  const ghostOcc = draggingOcc.current ? group.tabs.find((o) => o.occId === draggingOcc.current) : null;

  return (
    <div ref={groupRef} className="mw-tab-group" style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, position: 'relative' }}>
      {/* Marqueurs : affichés sur le groupe CIBLE */}
      {isSplit && (
        <>
          <div data-testid="drop-split" style={splitPreview!} />
          <div data-testid="drop-split-label" style={{
            position: 'absolute', zIndex: 31, padding: '2px 10px', fontSize: 11,
            background: 'var(--mw-accent, #3b82f6)', color: '#fff', borderRadius: 4,
            left: '50%', top: '50%', transform: 'translate(-50%, -50%)', pointerEvents: 'none',
          }}>
            {drop.zone === 'left' || drop.zone === 'right' ? '⇄ Split horizontal' : '⇅ Split vertical'}
          </div>
        </>
      )}
      {isTarget && drop.zone === 'center' && (
        <div data-testid="drop-append" style={{
          position: 'absolute', zIndex: 30, inset: 0, background: 'var(--mw-accent, #3b82f6)',
          opacity: 0.10, border: '2px dashed var(--mw-accent, #3b82f6)', borderRadius: 6, pointerEvents: 'none',
          display: 'flex', alignItems: 'flex-start', justifyContent: 'flex-end',
        }}>
          <span style={{ padding: '2px 10px', fontSize: 11, background: 'var(--mw-accent, #3b82f6)', color: '#fff', borderRadius: 4, margin: 4 }}>
            + Onglet en fin de file
          </span>
        </div>
      )}

      <div
        ref={barRef}
        className="mw-tab-bar"
        style={{
          display: 'flex', flexWrap: 'wrap', gap: 2, padding: '4px 4px 0', position: 'relative',
          borderBottom: '1px solid var(--mw-border, #334155)', flexShrink: 0, minHeight: 32,
          background: 'var(--mw-bg, #0f172a)',
        }}
      >
        {group.tabs.map((occ) => {
          const active = group.active?.occId === occ.occId;
          const dimmed = dragging && draggingOcc.current === occ.occId;
          return (
            <div
              key={occ.occId}
              className={`mw-tab ${active ? 'mw-tab-active' : ''} ${dimmed ? 'mw-tab-dragging' : ''}`}
              data-testid={`tab-${occ.panel}`}
              data-occ={occ.occId}
              onMouseDown={(e) => handleMouseDown(e, occ)}
              onClick={() => onActivate(occ.occId)}
              style={{
                display: 'flex', alignItems: 'center', gap: 6, padding: '4px 10px',
                fontSize: 12, cursor: 'grab', userSelect: 'none', whiteSpace: 'nowrap',
                background: active ? 'var(--mw-bg-panel, #1e293b)' : 'transparent',
                color: active ? 'var(--mw-fg, #e2e8f0)' : '#94a3b8',
                borderRadius: '6px 6px 0 0', border: '1px solid transparent',
                boxSizing: 'border-box', minWidth: 60,
                opacity: dimmed ? 0.35 : 1,
              }}
            >
              <span>{labelOf(occ)}</span>
              <span
                data-testid={`tab-close-${occ.panel}`}
                onClick={(e) => { e.stopPropagation(); onClose(occ.occId); }}
                style={{ color: '#64748b', fontSize: 11, cursor: 'pointer' }}
                title={t('menu.fermerOnglet') || 'Fermer'}
              >✕</span>
            </div>
          );
        })}
        {isTarget && drop.zone === 'bar' && markerLeft !== null && (
          <div
            data-testid="drop-insert"
            style={{ position: 'absolute', top: 2, bottom: 2, width: 2, background: 'var(--mw-accent, #3b82f6)', left: markerLeft, zIndex: 15, borderRadius: 1 }}
          />
        )}
      </div>
      <div className="mw-panel-body" style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
        {group.active ? renderPanel(group.active) : null}
      </div>

      {/* Ghost flottant : clone de l'onglet dragué, suit le curseur */}
      {dragging && ghostPos && ghostOcc && (
        <div
          ref={ghostRef}
          data-testid="drag-ghost"
          style={{
            position: 'fixed', left: ghostPos.x, top: ghostPos.y, zIndex: 1000,
            display: 'flex', alignItems: 'center', gap: 6, padding: '4px 10px',
            fontSize: 12, whiteSpace: 'nowrap',
            background: 'var(--mw-bg-panel, #1e293b)', color: 'var(--mw-fg, #e2e8f0)',
            borderRadius: 6, border: '1px solid var(--mw-accent, #3b82f6)',
            boxShadow: '0 4px 16px rgba(0,0,0,.45)', pointerEvents: 'none', opacity: 0.95,
          }}
        >
          <span>{labelOf(ghostOcc)}</span>
        </div>
      )}
    </div>
  );
}
