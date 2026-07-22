"""Catalogue listing skill — get_all_skills."""

from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None


_SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"


def get_all_skills(inputs: dict, ws: str) -> dict:
    skills = []
    if not _SKILLS_DIR.is_dir():
        return {"skills": []}
    for ypath in sorted(_SKILLS_DIR.rglob("*.yaml")):
        try:
            text = ypath.read_text(encoding="utf-8")
        except Exception:
            continue
        data = yaml.safe_load(text) if yaml else _naive_parse(text)
        if not data or not isinstance(data, dict):
            continue
        name = data.get("name") or ypath.stem
        skills.append({
            "name": name,
            "fn": name,
            "description": (data.get("description") or "").strip(),
            "category": data.get("category", ypath.parent.name),
            "inputs": list(data.get("inputs", {}).keys()),
            "outputs": list(data.get("outputs", {}).keys()),
        })
    return {"skills": skills}


def _naive_parse(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("name:"):
            out["name"] = stripped.split(":", 1)[1].strip().strip("\"'")
        elif stripped.startswith("description:"):
            out["description"] = stripped.split(":", 1)[1].strip().strip("\"'")
        elif stripped.startswith("category:"):
            out["category"] = stripped.split(":", 1)[1].strip().strip("\"'")
        elif stripped.startswith("inputs:"):
            break
    return out


__skills__ = ["get_all_skills"]