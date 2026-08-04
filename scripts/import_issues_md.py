"""Importe les issues de analyse-auto/ling/issues.md dans le workspace mw-swarm.

Utilise la route daemon workspace/issues/add (via dispatch, in-process).

Priorités : 🔴 Critique=5, 🟠 Haute=4, 🟡 Moyenne=3, 🟢 Basse=2.
Les issues marquées `human-choice` sont conservées telles quelles (le swarm
pourra les traiter ; la décision humaine reste nécessaire).
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, "/home/pierreloup2/PilousGarage/ModelWeaver")

PRIO = {"🔴": 5, "🟠": 4, "🟡": 3, "🟢": 2}


def parse_issues(md_path):
    text = Path(md_path).read_text(encoding="utf-8")
    sections = re.split(r"\n## ", text)
    issues = []
    for sec in sections[1:]:
        title_m = re.match(r".*?([🔴🟠🟡🟢]).*?—\s*(.*)", sec)
        sec_prio = 3
        m = re.search(r"[🔴🟠🟡🟢]", sec)
        if m:
            sec_prio = PRIO.get(m.group(0), 3)
        # chaque ### N.N
        for block in re.split(r"\n### ", sec)[1:]:
            head = block.split("\n", 1)[0]
            body = block.split("\n", 1)[1] if "\n" in block else ""
            title = head.strip()
            # enlever le préfixe "N.N "
            title = re.sub(r"^[\d.]+\s*", "", title).strip()
            # résumé : la ligne "**Résumé** : ..." ou "**Type** : ..."
            summary = ""
            rm = re.search(r"\*\*Résumé\*\*\s*:?\s*(.+)", body)
            tm = re.search(r"\*\*Type\*\*\s*:?\s*(.+)", body)
            if rm:
                summary = rm.group(1).strip()
            elif tm:
                summary = f"[{tm.group(1).strip()}]"
            desc_parts = []
            for line in body.splitlines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith("**") or line.startswith("```") or line.startswith("---"):
                    desc_parts.append(line)
                elif line.startswith("-") or line.startswith("*"):
                    desc_parts.append(line)
            description = "\n".join(desc_parts[:40])[:2000] or summary
            issues.append({
                "title": title,
                "description": f"{summary}\n\n{description}",
                "priority": sec_prio,
            })
    return issues


def main():
    md = Path("/home/pierreloup2/PilousGarage/ModelWeaver/analyse-auto/ling/issues.md")
    issues = parse_issues(md)
    print(f"issues parsées: {len(issues)}")
    for it in issues[:5]:
        print(f"  [p{it['priority']}] {it['title'][:70]}")

    from services.api.handlers.workspace import op_workspace_issues_add
    r = op_workspace_issues_add({
        "workspace_id": "mw-swarm",
        "issues": issues,
        "team_id": -1,
    })
    print("result:", r.get("status"), "added:", r.get("added"))
    if r.get("status") != "ok":
        print("error:", r.get("error"))


if __name__ == "__main__":
    main()
