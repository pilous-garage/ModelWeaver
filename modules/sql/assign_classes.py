import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from modules.sql.db import ModelWeaverDB

CLASSES = {
    "language":  {"label": "Languages",       "sort_order": 10},
    "dev-tool":  {"label": "Dev Tools",       "sort_order": 20},
    "ide":       {"label": "IDEs",            "sort_order": 30},
    "chat-llm":  {"label": "Chat LLM",        "sort_order": 40},
    "agent":     {"label": "Agents",          "sort_order": 50},
    "engine":    {"label": "LLM Engines",     "sort_order": 60},
    "router":    {"label": "Routers",         "sort_order": 70},
    "context":   {"label": "Context Tools",   "sort_order": 80},
    "system":    {"label": "System Tools",    "sort_order": 90},
    "other":     {"label": "Other",           "sort_order": 999},
}

CLASS_MAP = {
    "python3": "language",
    "git": "dev-tool",
    "curl": "dev-tool",
    "ollama": "engine",
    "litellm": "router",
    "opencode": "chat-llm",
    "open-webui": "chat-llm",
    "gitingest": "context",
}

def main():
    db = ModelWeaverDB()

    for ref, info in CLASSES.items():
        existing = db.tool_classes.get(ref)
        if existing:
            db.tool_classes.save({"ref": ref, **info})
        else:
            db.tool_classes.save({"ref": ref, **info})
        print(f"  Class {ref} → {info['label']}")

    for ref, cls in CLASS_MAP.items():
        tool = db.tools.get(ref)
        if tool:
            db.tools.save({**tool, "class": cls})

    for tool in db.tools.list_all():
        cls = tool.get("class")
        if not cls or cls not in CLASSES:
            db.tools.save({**tool, "class": "other"})

    db.commit()
    db.close()
    print("✅ Classes seeded & tools assigned.")

if __name__ == "__main__":
    main()