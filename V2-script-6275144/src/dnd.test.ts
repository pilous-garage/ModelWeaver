// Tests du drag & drop (dnd.ts) : zones de drop, annulation (clic droit/Echap).

import { describe, it, expect } from 'vitest';
import { computeDropZone, startDrag, cancelDrag, isDragging, getDragState } from './dnd.ts';

const box = { x: 0, y: 0, w: 100, h: 100 };

describe('computeDropZone', () => {
  it('centre = center', () => {
    expect(computeDropZone(50, 50, box)).toBe('center');
  });
  it('bord gauche = left', () => {
    expect(computeDropZone(5, 50, box)).toBe('left');
  });
  it('bord droit = right', () => {
    expect(computeDropZone(95, 50, box)).toBe('right');
  });
  it('bord haut = top', () => {
    expect(computeDropZone(50, 5, box)).toBe('top');
  });
  it('bord bas = bottom', () => {
    expect(computeDropZone(50, 95, box)).toBe('bottom');
  });
  it('hors boîte = outside', () => {
    expect(computeDropZone(200, 200, box, true)).toBe('outside');
  });
});

describe('startDrag / cancelDrag', () => {
  it('démarre un drag et le suit', () => {
    const state = { occId: 'o1', panelId: 'ressources', fromGroupId: 'pg-a', fromWindowId: 'w1', x: 10, y: 10 };
    startDrag(state, () => {});
    expect(isDragging()).toBe(true);
    expect(getDragState()?.occId).toBe('o1');
    cancelDrag();
  });

  it('annule au clic droit (bouton 2)', () => {
    let cancelled = false;
    const state = { occId: 'o2', panelId: 'etat', fromGroupId: 'pg-b', fromWindowId: 'w1', x: 10, y: 10 };
    startDrag(state, () => { cancelled = true; });
    // simule un contextmenu (clic droit)
    window.dispatchEvent(new MouseEvent('contextmenu', { button: 2, bubbles: true }));
    expect(cancelled).toBe(true);
    expect(isDragging()).toBe(false);
  });

  it('annule à Echap', () => {
    let cancelled = false;
    const state = { occId: 'o3', panelId: 'chat', fromGroupId: 'pg-c', fromWindowId: 'w1', x: 10, y: 10 };
    startDrag(state, () => { cancelled = true; });
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(cancelled).toBe(true);
    expect(isDragging()).toBe(false);
  });
});
