/** discover-panels.ts — Découverte automatique des *.panel.tsx
 *
 * Parcourt src/panels/, valide les id, génère index.ts.
 * Usage : npx tsx scripts/discover-panels.ts
 */

import * as fs from "fs";
import * as path from "path";

const PANELS_DIR = path.resolve(__dirname, "../src/panels");
const INDEX_OUT = path.join(PANELS_DIR, "index.ts");

interface PanelInfo {
  filePath: string;
  relativePath: string;
  expectedId: string;
  declaredId?: string;
  idWarning?: boolean | string;
}

function computeExpectedId(relativePath: string): string {
  // src/panels/Installator/catalogue-outils.panel.tsx
  // → installator-catalogue-outils
  const noExt = relativePath.replace(/\.panel\.tsx$/, "");
  const parts = noExt.split("/");
  return parts.join("-").toLowerCase();
}

function scanPanels(dir: string, baseDir: string): PanelInfo[] {
  const results: PanelInfo[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      results.push(...scanPanels(fullPath, baseDir));
    } else if (entry.isFile() && entry.name.endsWith(".panel.tsx")) {
      const relativePath = path.relative(baseDir, fullPath);
      results.push({
        filePath: fullPath,
        relativePath,
        expectedId: computeExpectedId(relativePath),
      });
    }
  }
  return results;
}

async function main(): Promise<number> {
  const panels = scanPanels(PANELS_DIR, PANELS_DIR);
  let errors = 0;
  let warnings = 0;

  console.log(`[discover-panels] ${panels.length} panel(s) trouvé(s)\n`);

  // Phase 1 : importer chaque fichier et vérifier son id
  for (const p of panels) {
    try {
      const mod = await import(p.filePath);
      const def = mod.Panel || mod.default;
      if (!def || !def.id) {
        console.error(`[discover-panels] ERREUR ${p.relativePath}: pas d'export Panel.id`);
        errors++;
        continue;
      }
      p.declaredId = def.id;
      p.idWarning = def.idWarning;

      if (def.id !== p.expectedId && def.idWarning !== false) {
        const msg = def.idWarning
          ? `(raison: ${def.idWarning})`
          : "(ajoutez idWarning: false si c'est intentionnel)";
        console.warn(`[discover-panels] WARN ${p.relativePath}: id="${def.id}" mais ${p.expectedId} attendu ${msg}`);
        warnings++;
      }

      // Vérifier les champs obligatoires
      const required = ["label", "version", "description", "daemonRoutes", "menu", "declaration", "component"];
      for (const field of required) {
        if (def[field] === undefined) {
          console.error(`[discover-panels] ERREUR ${p.relativePath}: champ obligatoire '${field}' manquant`);
          errors++;
        }
      }
    } catch (e: any) {
      console.error(`[discover-panels] ERREUR ${p.relativePath}: ${e.message}`);
      errors++;
    }
  }

  // Phase 2 : vérifier l'unicité des id
  const idMap = new Map<string, string[]>();
  for (const p of panels) {
    if (!p.declaredId) continue;
    const list = idMap.get(p.declaredId) || [];
    list.push(p.relativePath);
    idMap.set(p.declaredId, list);
  }
  for (const [id, files] of idMap.entries()) {
    if (files.length > 1) {
      console.error(`[discover-panels] ERREUR: id="${id}" utilisé ${files.length} fois : ${files.join(", ")}`);
      errors++;
    }
  }

  // Phase 3 : générer index.ts
  const imports: string[] = [];
  const registry: string[] = [];
  for (const p of panels) {
    if (!p.declaredId) continue;
    const varName = `_${p.declaredId.replace(/[^a-zA-Z0-9]/g, "_")}`;
    const importPath = `./${p.relativePath.replace(/\.tsx$/, "")}`;
    imports.push(`import { Panel as ${varName} } from "${importPath}";`);
    registry.push(`  "${p.declaredId}": ${varName},`);
  }

  const indexContent = `// ⚡ Généré automatiquement par scripts/discover-panels.ts
// Ne pas modifier à la main.

${imports.join("\n")}

export const PANEL_REGISTRY: Record<string, PanelDef> = {
${registry.join("\n")}
};

export function getPanelDeclaration(id: string): string | null {
  const panel = PANEL_REGISTRY[id];
  if (!panel) return null;
  return panel.declaration ? panel.declaration() : JSON.stringify(panel, null, 2);
}

export function listAllPanelDeclarations(): string[] {
  return Object.entries(PANEL_REGISTRY).map(([id, panel]) => {
    const decl = panel.declaration ? panel.declaration() : JSON.stringify(panel, null, 2);
    return \`--- \${id} ---\n\${decl}\`;
  });
}
`;

  fs.writeFileSync(INDEX_OUT, indexContent, "utf-8");
  console.log(`\n[discover-panels] ${INDEX_OUT} généré (${panels.length} panels)`);

  if (errors > 0) {
    console.error(`[discover-panels] ${errors} erreur(s), ${warnings} warning(s)`);
    return 1;
  }
  console.log(`[discover-panels] ✅ ${warnings} warning(s)`);
  return 0;
}

main().then((code) => process.exit(code));
