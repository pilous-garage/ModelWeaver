// Recherche floue légère : tolérante à la casse, aux accents et aux fautes de frappe.
// Renvoie un score (plus haut = meilleur) ou null si aucune correspondance.
// Aucune dépendance externe.

export function normalize(s: string): string {
  return (s || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '');
}

function levenshtein(a: string, b: string): number {
  const m = a.length, n = b.length;
  if (m === 0) return n;
  if (n === 0) return m;
  let prev = new Array(n + 1);
  let cur = new Array(n + 1);
  for (let j = 0; j <= n; j++) prev[j] = j;
  for (let i = 1; i <= m; i++) {
    cur[0] = i;
    for (let j = 1; j <= n; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
    }
    [prev, cur] = [cur, prev];
  }
  return prev[n];
}

const BOUNDARY = /[\s._/\-:@]/;

function subsequenceScore(q: string, t: string): number | null {
  let ti = 0, score = 300, prev = -2;
  for (let qi = 0; qi < q.length; qi++) {
    const c = q[qi];
    let found = -1;
    for (let k = ti; k < t.length; k++) {
      if (t[k] === c) { found = k; break; }
    }
    if (found < 0) return null;
    if (found === prev + 1) score += 15;              // caractères consécutifs
    if (found === 0 || BOUNDARY.test(t[found - 1])) score += 10; // début de mot
    score -= (found - ti);                            // pénalité de saut
    prev = found;
    ti = found + 1;
  }
  return score;
}

function bestWordLevenshtein(q: string, t: string): number {
  const words = t.split(/[\s._/\-:@]+/).filter(Boolean);
  let best = levenshtein(q, t);
  for (const w of words) best = Math.min(best, levenshtein(q, w));
  return best;
}

// Score de correspondance entre une requête et une chaîne cible.
export function fuzzyScore(query: string, target: string): number | null {
  const q = normalize((query || '').trim());
  const t = normalize(target);
  if (!q) return 0;
  if (!t) return null;

  const idx = t.indexOf(q);
  if (idx >= 0) {
    let score = 2000 - idx;
    if (idx === 0) score += 300;
    else if (BOUNDARY.test(t[idx - 1])) score += 150;
    return score;
  }

  const sub = subsequenceScore(q, t);
  if (sub != null) return sub;

  // Tolérance aux fautes de frappe
  const lev = bestWordLevenshtein(q, t);
  const maxDist = q.length <= 3 ? 1 : q.length <= 6 ? 2 : 3;
  if (lev <= maxDist) return 120 - lev * 25;

  return null;
}

// Filtre + tri par pertinence sur une liste, via un extracteur de texte.
export function fuzzyFilter<T>(
  query: string,
  items: T[],
  getText: (item: T) => string,
): T[] {
  if (!query || !query.trim()) return items;
  const scored: { item: T; score: number }[] = [];
  for (const item of items) {
    const score = fuzzyScore(query, getText(item));
    if (score != null) scored.push({ item, score });
  }
  scored.sort((a, b) => b.score - a.score);
  return scored.map((s) => s.item);
}
