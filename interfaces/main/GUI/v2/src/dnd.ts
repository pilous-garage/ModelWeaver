// Gestionnaire de drag & drop des onglets.
//
// Drag CUSTOM (mousedown/mousemove/mouseup), pas de DragEvent natif :
// évite le crash WebKitGTK headless rencontré en v1. L'annulation se fait
// par clic droit (contextmenu / mousedown bouton 2) ou Echap.
//
// Zones de drop : un onglet (réordonner/insérer), une barre (ajouter au
// groupe), un bord (split), le bureau (extraire en fenêtre).

export interface DragState {
  occId: string;
  panelId: string;
  fromGroupId: string;
  fromWindowId: string;
  x: number;
  y: number;
}

export type DropZone = 'tab' | 'bar' | 'top' | 'bottom' | 'left' | 'right' | 'center' | 'outside';

let _drag: DragState | null = null;
let _cancelHandlers: (() => void)[] = [];

export function isDragging(): boolean {
  return _drag !== null;
}

export function getDragState(): DragState | null {
  return _drag;
}

function attachCancelListeners(onCancel: () => void) {
  const trigger = () => {
    onCancel();
    cancelDrag();
  };
  const onContext = (e: MouseEvent) => {
    if (e.button === 2) {
      e.preventDefault();
      trigger();
    }
  };
  const onMouseDown = (e: MouseEvent) => {
    if (e.button === 2) trigger();
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'Escape') trigger();
  };
  window.addEventListener('contextmenu', onContext);
  window.addEventListener('mousedown', onMouseDown);
  window.addEventListener('keydown', onKey);
  const detach = () => {
    window.removeEventListener('contextmenu', onContext);
    window.removeEventListener('mousedown', onMouseDown);
    window.removeEventListener('keydown', onKey);
  };
  _cancelHandlers.push(detach);
  return detach;
}

/** Démarre un drag d'onglet. Retourne une fonction d'annulation. */
export function startDrag(state: DragState, onCancel: () => void): () => void {
  cancelDrag(); // nettoie tout drag en cours
  _drag = state;
  const detach = attachCancelListeners(onCancel);
  return () => {
    detach();
    _drag = null;
  };
}

/** Annule le drag en cours (clic droit / Echap). */
export function cancelDrag(): void {
  _drag = null;
  for (const h of _cancelHandlers) h();
  _cancelHandlers = [];
}

/** Détermine la zone de drop selon la position relative à une boîte. */
export function computeDropZone(x: number, y: number, box: { x: number; y: number; w: number; h: number }, outside = false): DropZone {
  if (outside) return 'outside';
  const relX = (x - box.x) / box.w;
  const relY = (y - box.y) / box.h;
  const EDGE = 0.12;
  if (relX < EDGE) return 'left';
  if (relX > 1 - EDGE) return 'right';
  if (relY < EDGE) return 'top';
  if (relY > 1 - EDGE) return 'bottom';
  return 'center';
}

/** Adapte un DOMRect à {x,y,w,h}. */
export function rectToBox(rect: DOMRect): { x: number; y: number; w: number; h: number } {
  return { x: rect.x, y: rect.y, w: rect.width, h: rect.height };
}
