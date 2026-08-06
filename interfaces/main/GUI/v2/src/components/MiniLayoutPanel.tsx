import React, { useMemo } from 'react';
import type { Layout, PanelOcc, TreeNode } from '../layout/types.ts';
import { mapMiniLayout, activateTab, closeTab, moveTab, splitGroup, renameTab, setZoomLocal, toggleZoomLock, resizeSplit, effectiveZoom } from '../layout/ops.ts';
import { resolveLayout } from '../layout/resolve.ts';
import { SplitTree, type RenderCtx } from './SplitTree.tsx';

/**
 * MINI-LAYOUT : un onglet spécial (PanelOcc.tree) qui rend un sous-arbre
 * (splits + groupes) au lieu d'un composant. C'est un "panneau comme les
 * autres" avec un layout interne : plusieurs mini-layouts cohabitent dans un
 * groupe (un onglet chacun) et le drag/drop/reorder/cross-group fonctionnent
 * nativement (ce sont de simples onglets).
 *
 * Les mutations passent par `mapMiniLayout(layout, occId, fn)` : elles ciblent
 * le tree de CET onglet (jamais le layout racine), en réutilisant les ops du
 * module layout (activateTab, moveTab, splitGroup, resizeSplit…).
 */
export function MiniLayoutPanel(props: {
  occ: PanelOcc;
  windowId: string;
  t: (k: string) => string;
  mutate: (fn: (l: Layout) => Layout) => void;
  renderPanel: (occ: PanelOcc) => React.ReactNode;
  /** produit des zooms des ancêtres (global × mini-layouts au-dessus de CET onglet). */
  zoomFactor: number;
}) {
  const { occ, windowId, t, mutate, renderPanel, zoomFactor } = props;

  // Applique une op layout au tree INTERNE de cet onglet mini-layout.
  const inMini = useMemo(() => (op: (l: Layout) => Layout) =>
    mutate((l) => mapMiniLayout(l, occ.occId, (tree) => {
      const fake: Layout = { id: 'mini', tree };
      const next = op(fake);
      return next.tree;
    })), [occ.occId, mutate]);

  // facteur des ancêtres pour les panels DANS ce mini-layout = l'effectif de ce
  // mini-layout (zoomFactor × son zoom propre, ou sa valeur si locké).
  const innerFactor = effectiveZoom(zoomFactor, occ);

  const ctx = useMemo<RenderCtx>(() => ({
    windowId,
    t,
    onActivate: (g, o) => inMini((l) => activateTab(l, g, o)),
    onClose: (g, o) => inMini((l) => closeTab(l, g, o)),
    onMove: (from, to, o, idx) => inMini((l) => moveTab(l, from, to, o, idx)),
    onSplit: (g, dir, o, from) => inMini((l) => splitGroup(l, g, dir, o, from)),
    onExtract: (g, o) => inMini((l) => closeTab(l, g, o)),
    onRename: (g, o, label) => inMini((l) => renameTab(l, g, o, label)),
    onZoom: (g, o, localValue) => inMini((l) => setZoomLocal(l, o, localValue)),
    onZoomLock: (g, o, ancestorFactor) => inMini((l) => toggleZoomLock(l, o, ancestorFactor)),
    onResize: (sid, idx, sepPos, total) => inMini((l) => resizeSplit(l, sid, idx, sepPos, total)),
    renderPanel,
  }), [windowId, t, inMini, renderPanel]);

  const resolved = useMemo(
    () => resolveLayout({ id: 'mini', tree: occ.tree as TreeNode }, {}, null),
    [occ.tree],
  );

  return (
    <div
      className="mw-mini-layout"
      data-testid={`mini-${occ.occId}`}
      style={{ height: '100%', minHeight: 0, overflow: 'hidden' }}
    >
      {resolved.root ? <SplitTree node={resolved.root} ctx={ctx} zoomFactor={innerFactor} /> : (
        <div data-testid="mini-empty" style={{ padding: 12, color: '#64748b', fontSize: 12, textAlign: 'center' }}>
          Mini-layout vide — déposez un panneau ici.
        </div>
      )}
    </div>
  );
}
