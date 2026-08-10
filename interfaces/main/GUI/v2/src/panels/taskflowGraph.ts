// taskflowGraph.ts — commandes de pliage/dépliage (zip/unzip) du taskflow.
//
// Renvoient sur les algorithmes de clipping (taskflowClip) : zip_all clippe
// tout ce qui est clippable (nœuds visibles), unzip_all dé-clippe.
// slow_collapse : DÉMONSTRATION/VÉRIFICATION — plie UN clip à la fois, en
// faisant blinquer le nœud qui va disparaître (~5 s), et log_graph l'état du
// graphe à chaque étape.

import { findClippable, zip, isVisible, createClipTable, type Clip, type ClipTable } from './taskflowClip.ts';
export { zipAll, unzipAll, createClipTable } from './taskflowClip.ts';
export type { ClipTable, Clip } from './taskflowClip.ts';

/** zip_all / unzip_all globaux : le graphe courant est géré par le
 *  GrapheSubPanel (tfDoc local) — voir les handlers doZipAll/doUnzipAll du
 *  panel. Conservés pour compat. */
export function zip_all(): void { /* TODO : brancher le doc courant. */ }
export function unzip_all(): void { /* TODO : brancher le doc courant. */ }

/** Log console de l'état du graphe : chaque nœud (par niveau) avec
 *  visible/zippable/in_group/out_group + la liste des clips possibles. */
export function log_graph(g: { nodes: any[]; edges: any[] }): void {
  const walk = (nodes: any[], path: string[]) => {
    for (const n of nodes) {
      const v = n.vars ?? {};
      const level = path.length ? `@${path.join('/')} ` : '';
      const groups = `in=[${(v.in_group ?? []).join(',')}] out=[${(v.out_group ?? []).join(',')}]`;
      const places = typeof v.innerPlacesVisible === 'number' ? ` places=${v.innerPlacesVisible}` : '';
      console.log(`  ${level}${n.id} [${n.type}] visible=${isVisible(n)} zippable=${v.zippable ?? '?'} ${groups}${places}`);
      if (n.vars?.inner?.nodes) walk(n.vars.inner.nodes, [...path, n.id]);
    }
  };
  console.log('=== log_graph ===');
  walk(g.nodes, []);
  const clips = findClippable(g);
  console.log(`clippable : ${clips.length} clip(s)`);
  for (const c of clips) {
    console.log(`  - ${c.type} : on=[${c.on.join(',')}] → zipped=[${c.zipped.join(',')}] (${c.newId})${c.path?.length ? ` @${c.path.join('/')}` : ''}`);
  }
  console.log('===============');
}

export interface SlowCollapseOptions {
  delay?: number;        // ms allumé par blink (défaut 800)
  blinks?: number;       // nb de blinks avant le fold (défaut 5 → ~5 s)
  onBlink?: (ids: string[], on: boolean) => void;
  onStep?: (clip: Clip, zips: number) => void;
}

/** Slow collapse : plie UN clip à la fois. Boucle :
 *   log_graph → trouve le 1er clip → blinque ses nœuds `on` (~5 s) → fold
 *   → on recommence jusqu'à ce qu'il n'y ait plus de clip.
 * Retourne le nombre de zips appliqués. */
export async function slow_collapse(g: { nodes: any[]; edges: any[] }, opts: SlowCollapseOptions = {}): Promise<number> {
  const delay = opts.delay ?? 800;
  const blinks = opts.blinks ?? 5;
  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
  let zips = 0;
  for (;;) {
    log_graph(g);
    const clips = findClippable(g);
    if (!clips.length) {
      console.log(`slow_collapse : plus aucun clip — ${zips} zip(s) appliqué(s).`);
      return zips;
    }
    const clip = clips[0];
    console.log(`slow_collapse : zip #${zips + 1} → ${clip.type} on=[${clip.on.join(',')}] zipped=[${clip.zipped.join(',')}]${clip.path?.length ? ` @${clip.path.join('/')}` : ''}`);
    for (let i = 0; i < blinks; i++) {
      opts.onBlink?.(clip.on, true);
      await sleep(delay);
      opts.onBlink?.(clip.on, false);
      await sleep(delay / 2);
    }
    opts.onBlink?.(clip.on, false);
    zip(g, clip);
    zips++;
    opts.onStep?.(clip, zips);
  }
}
