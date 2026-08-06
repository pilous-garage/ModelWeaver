// zoom.ts — graduation du zoom + helpers.
//
// Le zoom est stocké en FLOAT (×) : 1 = 100%, 1.5 = 150%, 0.66 = 66%.
// Les boutons +/− suivent une liste DISCÈTE de paliers (plus propre visuellement
// que des multiplications ×1.1). L'édition manuelle reste libre (au-delà des
// bornes si besoin : graphe gigantesque).

/** Paliers de zoom en % (de 10% à 1000%). */
export const ZOOM_STEPS = [
  10, 20, 30, 40, 50, 60, 70, 80, 90, 100,
  110, 120, 150, 200, 250, 300, 400, 500, 600, 700, 800, 900, 1000,
];

/** Bornes « souples » (édition libre autorisée, mais ces bornes guident +/-). */
export const MIN_ZOOM_PCT = 10;
export const MAX_ZOOM_PCT = 1000;

/** Convertisseur × → % (arrondi à 1 décimale). */
export function zoomToPct(value: number): number {
  return Math.round(value * 1000) / 10;
}

/** % → × (toujours ≥ 1e-6). */
export function pctToZoom(pct: number): number {
  return Math.max(1e-6, pct / 100);
}

/** Palier SUIVANT (+, roulette haut). Reste au plafond si déjà au max. */
export function nextZoom(value: number): number {
  const pct = zoomToPct(value);
  const next = ZOOM_STEPS.find((s) => s > pct + 0.0001);
  return pctToZoom(next ?? MAX_ZOOM_PCT);
}

/** Palier PRÉCÉDENT (−, roulette bas). Reste au plancher si déjà au min. */
export function prevZoom(value: number): number {
  const pct = zoomToPct(value);
  const prev = [...ZOOM_STEPS].reverse().find((s) => s < pct - 0.0001);
  return pctToZoom(prev ?? MIN_ZOOM_PCT);
}
