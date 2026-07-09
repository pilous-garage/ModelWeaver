import sys
import json
import re
import os
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent.parent.parent))

from sql.db import ModelWeaverDB


def detect_manager(commands: list):
    """Trouve le premier manager reconnu parmi toutes les commandes."""
    for cmd in commands:
        cmd_clean = cmd.lower().strip()
        if cmd_clean.startswith('sudo '):
            cmd_clean = cmd_clean[5:].strip()
        patterns = [
        (r'^apt-get\s+install\s+(?:-y\s+)?(\S+)', 'apt', 'apt-get install -y'),
        (r'^apt\s+install\s+(?:-y\s+)?(\S+)', 'apt', 'apt install -y'),
        (r'^brew\s+install\s+(\S+)', 'brew', 'brew install'),
        (r'^pip\s+install\s+(\S+)', 'pip', 'pip install'),
        (r'^pip3\s+install\s+(\S+)', 'pip', 'pip3 install'),
        (r'^winget\s+install.*--id\s+(\S+)', 'winget', 'winget install --id'),
        (r'^winget\s+install\s+(\S+)', 'winget', 'winget install --id'),
        (r'^choco\s+install\s+(?:-y\s+)?(\S+)', 'choco', 'choco install -y'),
        (r'^snap\s+install\s+(\S+)', 'snap', 'snap install'),
        (r'^npm\s+install\s+-g\s+(\S+)', 'npm', 'npm install -g'),
        (r'^cargo\s+install\s+(\S+)', 'cargo', 'cargo install'),
        (r'^go\s+install\s+(\S+)', 'go', 'go install'),
        (r'^flatpak\s+install\s+(\S+)', 'flatpak', 'flatpak install'),
    ]
    for pattern, manager, base_cmd in patterns:
            m = re.search(pattern, cmd_clean)
            if m:
                return manager, m.group(1), base_cmd
    return None, None, None


def detect_tool_name(commands: list):
    for cmd in commands:
        _, pkg, _ = detect_manager([cmd])
        if pkg:
            # Clean version suffix e.g. curl=7.68 -> curl
            name = re.split(r'[=@><]', pkg)[0].strip()
            # Remove version numbers
            name = re.sub(r'\d+\.\d+.*$', '', name).strip().rstrip('-').rstrip('_')
            if name and len(name) > 1:
                return name
    return "custom-tool"


def detect_version(commands: list, tool_name: str):
    for cmd in commands:
        # Look for version patterns like tool-1.2.3, tool=1.2.3, v1.2.3
        m = re.search(r'(?:' + re.escape(tool_name) + r'[=@-])?(\d+\.\d+\.\d+)', cmd)
        if m:
            return m.group(1)
        m = re.search(r'(?:v|version=)(\d+\.\d+\.\d+)', cmd)
        if m:
            return m.group(1)
    return None


def detect_tool_type(commands: list):
    for cmd in commands:
        c = cmd.lower().strip()
        if c.startswith('sudo '):
            c = c[5:].strip()
        if any(x in c for x in ['pip install', 'pip3 install']):
            return 'python-module'
        if any(x in cmd for x in ['tar ', './configure', 'make ', 'cmake']):
            return 'source'
        if any(x in cmd for x in ['docker ', 'podman ']):
            return 'container'
    return 'binary'


def generate_recipe(ref: str, commands: list, tool_name: str, version: str,
                     manager: str, pkg: str, tool_type: str):
    os_key = 'linux'
    if sys.platform == 'darwin':
        os_key = 'macos'
    elif sys.platform == 'win32':
        os_key = 'windows'

    # Generate mw.yaml content
    lines = []
    lines.append(f"name: {tool_name}")
    lines.append(f"description: \"{tool_name} - auto-generated recipe\"")
    lines.append("class: other")
    lines.append("timeout: 120")
    lines.append("")
    lines.append("pre_install:")
    lines.append(f"  - command: echo \"Installing {tool_name}...\"")
    lines.append("")
    lines.append("post_install:")
    lines.append(f"  - command: {tool_name} --version")
    lines.append("")
    lines.append("post_uninstall:")
    lines.append(f"  - \"rm -rf {{home}}/.{tool_name}\"")
    lines.append("")
    lines.append("versions:")
    ver = version or "1.0.0"
    lines.append(f"  default: \"{ver}\"")
    lines.append(f'  "{ver}":')
    lines.append(f"    {os_key}:")

    if manager:
        lines.append(f"      {manager}:")
        lines.append(f"        package: \"{pkg}\"")
        lines.append(f"        install:")
        for cmd in commands:
            lines.append(f"          - \"{cmd}\"")
        lines.append(f"        uninstall:")
        if manager == 'apt':
            lines.append(f"          - \"apt-get remove -y {pkg}\"")
            lines.append(f"          - \"apt-get autoremove -y\"")
        elif manager == 'brew':
            lines.append(f"          - \"brew uninstall {pkg}\"")
        elif manager == 'pip':
            lines.append(f"          - \"pip uninstall -y {pkg}\"")
        else:
            lines.append(f"          - \"echo 'uninstall not defined for {manager}'\"")
    else:
        lines.append(f"      binary:")
        lines.append(f"        install:")
        for cmd in commands:
            lines.append(f"          - \"{cmd}\"")
        lines.append(f"        uninstall:")
        lines.append(f"          - \"echo 'uninstall not defined'\"")
        lines.append(f"          - \"rm -f /usr/local/bin/{tool_name}\"")

    lines.append("")
    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"status": "error", "error": "Missing JSON argument"}), flush=True)
        return

    try:
        data = json.loads(sys.argv[1])
    except json.JSONDecodeError as e:
        print(json.dumps({"status": "error", "error": f"Invalid JSON: {e}"}), flush=True)
        return

    raw = data.get("commands", "")
    ref_hint = data.get("ref", "")

    commands = [c.strip() for c in raw.strip().split("\n") if c.strip()]
    if not commands:
        print(json.dumps({"status": "error", "error": "No commands provided"}), flush=True)
        return

    manager, pkg, base_cmd = detect_manager(commands)
    tool_name = data.get("name") or detect_tool_name(commands) or ref_hint or "custom-tool"
    version = detect_version(commands, tool_name)
    tool_type = detect_tool_type(commands)
    ref = ref_hint or tool_name.lower().replace(" ", "-").replace("_", "-")

    # Generate and save recipe
    recipe_content = generate_recipe(ref, commands, tool_name, version, manager, pkg, tool_type)
    recipe_path = Path(__file__).resolve().parent.parent.parent.parent / "install_recipe" / f"{ref}.mw.yaml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    recipe_path.write_text(recipe_content)

    # Save to BDD
    db = ModelWeaverDB()
    existing = db.conn.execute("SELECT id FROM tools WHERE ref = ?", (ref,)).fetchone()
    if existing:
        db.close()
        print(json.dumps({"status": "error",
                          "error": f"Tool '{ref}' already exists"}), flush=True)
        return

    db.tools.save({
        "ref": ref,
        "name": tool_name,
        "description": f"{tool_name} - auto-detected from commands",
        "tool_type": tool_type,
        "install_method": manager or "direct-url",
        "current_version": version,
        "recipe_path": f"install_recipe/{ref}.mw.yaml",
        "class": data.get("class", "other"),
    })
    db.commit()
    db.close()

    # Update index
    index_path = Path(__file__).resolve().parent.parent.parent.parent / "install_recipe" / "index.mw.json"
    if index_path.exists():
        import json as _json
        idx = _json.loads(index_path.read_text())
    else:
        idx = {}
    idx[ref] = {
        "file": f"{ref}.mw.yaml",
        "latest": version or "1.0.0",
        "class": data.get("class", "other"),
        "description": f"{tool_name} - auto-generated recipe",
    }
    index_path.write_text(_json.dumps(idx, indent=2))

    print(json.dumps({"status": "success",
                      "data": {
                          "ref": ref,
                          "name": tool_name,
                          "manager": manager,
                          "package": pkg,
                          "version": version,
                          "tool_type": tool_type,
                          "recipe_path": f"install_recipe/{ref}.mw.yaml",
                      }}), flush=True)


if __name__ == "__main__":
    main()