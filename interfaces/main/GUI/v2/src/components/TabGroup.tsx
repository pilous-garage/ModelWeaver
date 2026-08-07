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
import { effectiveZoom, occZoom } from '../layout/ops.ts';
import { zoomToPct, nextZoom, prevZoom } from '../zoom.ts';
import { ZoomBar } from './ZoomBar.tsx';

interface Props {
  group: ResolvedGroup;
  windowId: string;
  t: (k: string) => string;
  /** produit des zooms des ancêtres (global × mini-layouts) pour ce groupe. */
  zoomFactor: number;
  onActivate(occId: string): void;
  onClose(occId: string): void;
  onMove(fromGroup: string, toGroup: string, occId: string, index?: number): void;
  onSplit(groupId: string, dir: 'horizontal' | 'vertical', occId: string, fromGroup?: string): void;
  onExtract(groupId: string, occId: string): void;
  onRename?(groupId: string, occId: string, label: string | null): void;
  onZoom?(groupId: string, occId: string, localValue: number): void;
  onZoomLock?(groupId: string, occId: string, ancestorFactor: number): void;
  highlight?: string | null;
  renderPanel(occ: PanelOcc, ancestorFactor?: number): React.ReactNode;
}

// Seuil de mouvement (px) : au-delà, c'est un drag ; en-deçà, un clic.
const DRAG_THRESHOLD = 5;
// Délai (ms) : si le pointeur ne bouge pas dans ce délai, ce n'est pas un drag.
const CLICK_TIMER_MS = 180;

export function TabGroup({ group, windowId, t, zoomFactor, onActivate, onClose, onMove, onSplit, onExtract, onRename, onZoom, onZoomLock, highlight, renderPanel }: Props) {
  const barRef = useRef<HTMLDivElement>(null);
  const groupRef = useRef<HTMLDivElement>(null);
  const ghostRef = useRef<HTMLDivElement>(null);
  const draggingOcc = useRef<string>('');
  const [dragging, setDragging] = useState(false);
  const [ghostPos, setGhostPos] = useState<{ x: number; y: number } | null>(null);
  // Onglet en cours d'édition du titre (double-clic) + valeur saisie.
  const [editing, setEditing] = useState<{ occId: string; value: string } | null>(null);
  // La barre d'onglets déborde-t-elle (nécessite un scroll horizontal) ?
  const [canScroll, setCanScroll] = useState(false);
  const [scrollAtStart, setScrollAtStart] = useState(true);
  const [scrollAtEnd, setScrollAtEnd] = useState(true);
  const drop = useDropState();
  // est-ce que CE groupe est la cible du drag en cours ?
  const isTarget = drop.targetGroupId === group.id;

  const labelOf = (occ: PanelOcc) => {
    if (occ.label) return occ.label;
    if (occ.tree) return t('menu.miniLayout') !== 'menu.miniLayout' ? t('menu.miniLayout') : 'Mini-layout';
    const def = getPanel(occ.panel);
    return def ? (t(def.labelKey) !== def.labelKey ? t(def.labelKey) : def.id) : occ.panel;
  };

  // ── Édition du titre (double-clic) ──────────────────────────────────
  const commitRename = useCallback((occId: string, value: string) => {
    setEditing(null);
    if (!onRename) return;
    const trimmed = value.trim();
    onRename(group.id, occId, trimmed || null);
  }, [group.id, onRename]);

  const editInputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (editing && editInputRef.current) {
      editInputRef.current.focus();
      editInputRef.current.select();
    }
  }, [editing]);

  // Commit au clic n'importe où pendant l'édition : le mousedown des onglets
  // fait preventDefault (anti-sélection drag) ce qui EMPÊCHE le blur de
  // l'input → le titre ne serait validé qu'au blur naturel (rare). On force
  // donc le commit en écoute document (capture) dès qu'un clic sort de l'input.
  useEffect(() => {
    if (!editing) return;
    const onDocMouseDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (editInputRef.current && editInputRef.current.contains(t)) return;
      commitRename(editing.occId, editing.value);
    };
    document.addEventListener('mousedown', onDocMouseDown, true);
    return () => document.removeEventListener('mousedown', onDocMouseDown, true);
  }, [editing, commitRename]);

  // Détection d'overflow de la barre d'onglets → affiche les flèches de scroll
  // et met à jour les positions start/end quand on scroll.
  useEffect(() => {
    const bar = barRef.current;
    if (!bar) return;
    const update = () => {
      const over = bar.scrollWidth > bar.clientWidth + 2;
      setCanScroll(over);
      setScrollAtStart(bar.scrollLeft <= 2);
      setScrollAtEnd(bar.scrollLeft + bar.clientWidth >= bar.scrollWidth - 2);
    };
    update();
    // ResizeObserver : la largeur de la barre change avec le layout.
    let ro: ResizeObserver | null = null;
    try {
      ro = new ResizeObserver(update);
      ro.observe(bar);
    } catch { /* fallback */ }
    bar.addEventListener('scroll', update, { passive: true });
    window.addEventListener('resize', update);
    return () => {
      ro?.disconnect();
      bar.removeEventListener('scroll', update);
      window.removeEventListener('resize', update);
    };
  }, [group.tabs.length]);

  const scrollTabs = (dir: 'left' | 'right') => {
    const bar = barRef.current;
    if (!bar) return;
    bar.scrollBy({ left: dir === 'left' ? -140 : 140, behavior: 'smooth' });
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
    // Empêche la sélection de texte dès le mousedown (avant le seuil de drag) :
    // sinon le navigateur sélectionne entre le point de départ et d'arrivée.
    e.preventDefault();
    document.body.style.userSelect = 'none';
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
      restoreBody(); // restaure userSelect même pour un simple clic
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

      <div style={{ display: 'flex', alignItems: 'stretch', flexShrink: 0, position: 'relative' }}>
        {canScroll && (
          <button
            onClick={() => scrollTabs('left')}
            title="Défiler à gauche"
            style={{
              flexShrink: 0, width: 22, border: 'none', cursor: scrollAtStart ? 'default' : 'pointer',
              background: 'transparent', color: scrollAtStart ? '#334155' : '#94a3b8',
              fontSize: 14, display: 'flex', alignItems: 'center', justifyContent: 'center',
              borderRight: '1px solid var(--mw-border, #334155)',
            }}
            disabled={scrollAtStart}
          >‹</button>
        )}
        <div
          ref={barRef}
          className="mw-tab-bar"
          style={{
            display: 'flex', flexWrap: 'nowrap', overflowX: 'auto', overflowY: 'hidden',
            scrollbarWidth: 'thin', gap: 2, padding: '4px 4px 0', position: 'relative',
            borderBottom: '1px solid var(--mw-border, #334155)', flexShrink: 0, minHeight: 32,
            background: 'var(--mw-bg, #0f172a)', flex: 1,
          }}
          onWheel={(e) => {
            // Molette verticale → défilement horizontal de la barre d'onglets
            // (les onglets sont sur UNE seule ligne, scrollables).
            if (barRef.current && Math.abs(e.deltaY) > 0) {
              barRef.current.scrollLeft += e.deltaY;
            }
          }}
        >
        {group.tabs.map((occ) => {
          const active = group.active?.occId === occ.occId;
          const dimmed = dragging && draggingOcc.current === occ.occId;
          const hl = highlight === occ.occId;
          return (
            <div
              key={occ.occId}
              className={`mw-tab ${active ? 'mw-tab-active' : ''} ${dimmed ? 'mw-tab-dragging' : ''} ${hl ? 'mw-tab-highlight' : ''}`}
              data-testid={`tab-${occ.panel}`}
              data-occ={occ.occId}
              onMouseDown={(e) => handleMouseDown(e, occ)}
              onClick={() => onActivate(occ.occId)}
              onDoubleClick={() => {
                if (onRename) setEditing({ occId: occ.occId, value: labelOf(occ) });
              }}
              style={{
                display: 'flex', alignItems: 'center', gap: 6, padding: '4px 10px',
                fontSize: 12, cursor: 'grab', userSelect: 'none', whiteSpace: 'nowrap',
                background: active ? 'var(--mw-bg-panel, #1e293b)' : 'transparent',
                color: active ? 'var(--mw-fg, #e2e8f0)' : '#94a3b8',
                borderRadius: '6px 6px 0 0', border: '1px solid transparent',
                boxSizing: 'border-box', minWidth: 60,
                opacity: dimmed ? 0.35 : 1,
                boxShadow: hl ? 'inset 0 -3px 0 var(--mw-accent, #3b82f6)' : undefined,
              }}
            >
              {editing?.occId === occ.occId ? (
                <input
                  ref={editInputRef}
                  data-testid="tab-rename-input"
                  value={editing.value}
                  onChange={(e) => setEditing({ ...editing, value: e.target.value })}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') commitRename(occ.occId, editing.value);
                    else if (e.key === 'Escape') setEditing(null);
                  }}
                  onBlur={() => commitRename(occ.occId, editing.value)}
                  onClick={(e) => e.stopPropagation()}
                  onMouseDown={(e) => e.stopPropagation()}
                  style={{
                    width: 100, fontSize: 12, padding: '1px 4px',
                    background: 'var(--mw-bg, #0f172a)', color: 'var(--mw-fg, #e2e8f0)',
                    border: '1px solid var(--mw-accent, #3b82f6)', borderRadius: 4,
                    outline: 'none',
                  }}
                />
              ) : (
                <span>{labelOf(occ)}</span>
              )}
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
        {group.active && (onZoom || onZoomLock) && (
          <div style={{ flexShrink: 0, padding: '0 2px', display: 'flex', alignItems: 'center', borderBottom: '1px solid var(--mw-border, #334155)' }}>
            <ZoomBar
              // valeur LOCALE du zoom (le calcul effectif = ancêtres × locale)
              value={occZoom(group.active)}
              locked={group.active.zoom?.locked}
              onChange={(v) => onZoom?.(group.id, group.active!.occId, v)}
              onToggleLock={onZoomLock ? () => onZoomLock!(group.id, group.active!.occId, zoomFactor) : undefined}
              testidPrefix="group-"
            />
          </div>
        )}
        {canScroll && (
          <button
            onClick={() => scrollTabs('right')}
            title="Défiler à droite"
            style={{
              flexShrink: 0, width: 22, border: 'none', cursor: scrollAtEnd ? 'default' : 'pointer',
              background: 'transparent', color: scrollAtEnd ? '#334155' : '#94a3b8',
              fontSize: 14, display: 'flex', alignItems: 'center', justifyContent: 'center',
              borderLeft: '1px solid var(--mw-border, #334155)',
            }}
            disabled={scrollAtEnd}
          >›</button>
        )}
      </div>
      <div
        className="mw-panel-body"
        data-testid="mw-panel-body"
        style={{
          flex: 1, minHeight: 0, overflow: 'auto',
          ...(highlight && highlight === group.active?.occId
            ? { outline: '2px solid var(--mw-accent, #3b82f6)', outlineOffset: -2, borderRadius: 4 }
            : {}),
        }}
        onWheel={(e) => {
          // Ctrl+roulette → zoom du panel de l'onglet actif (le plus imbriqué).
          if (!e.ctrlKey || !onZoom || !group.active) return;
          e.preventDefault();
          e.stopPropagation();
          const local = occZoom(group.active);
          onZoom(group.id, group.active.occId, e.deltaY < 0 ? nextZoom(local) : prevZoom(local));
        }}
      >
        {group.tabs.length === 0 ? (
          <div data-testid="group-empty" style={{ padding: 16, color: '#64748b', fontSize: 12, textAlign: 'center' }}>
            Mini-layout vide — déposez un panneau ici.
          </div>
        ) : group.active ? (
          <div
            data-testid="zoom-body"
            style={{
              height: '100%', minHeight: 0, transformOrigin: 'top left',
              // zoom CSS (recalcule layout + scroll natif) — l'affichage du
              // CONTENU du panel uniquement (les barres d'onglets restent 100%).
              // Un mini-layout (onglet avec tree) n'est PAS scalé ici : son zoom
              // est propagé aux panels internes (innerFactor) pour ne pas agrandir
              // ses propres barres d'onglets.
              zoom: group.active.tree ? 1 : effectiveZoom(zoomFactor, group.active),
            }}
          >
            {renderPanel(group.active, zoomFactor)}
          </div>
        ) : null}
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
