// Groupe une liste d'outils par classe métier (t.classe || t.tool_type),
// tri alpha sur le nom de classe puis sur le nom d'outil.
// Retourne [[classeNom, outils[]], ...] prêt à .map().
export function groupByClass(tools: any[]): [string, any[]][] {
  const groups: Record<string, any[]> = {};
  for (const t of tools) {
    const c = (t.classe || t.tool_type || 'Other');
    (groups[c] ||= []).push(t);
  }
  return Object.keys(groups)
    .sort((a: string, b: string) => a.localeCompare(b))
    .map((c: string) => {
      const items = (groups[c] || []).slice().sort(
        (a: any, b: any) => (a.name || '').localeCompare(b.name || '')
      );
      return [c, items] as [string, any[]];
    });
}

export const maskKey = (k: string) => k.length > 4 ? k.slice(0, 2) + '****' + k.slice(-2) : '****';
