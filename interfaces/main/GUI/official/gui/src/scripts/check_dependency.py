#!/usr/bin/env python3
import subprocess
import json
import sys
import re

def run_command(command):
    """Run a shell command and return (success, stdout, stderr)."""
    try:
        result = subprocess.run(
            command, 
            shell=True, 
            check=False, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            text=True
        )
        return (result.returncode == 0, result.stdout.strip(), result.stderr.strip())
    except Exception as e:
        return (False, "", str(e))

def check_dependency(dep):
    """Check if a dependency is installed and extract its version."""
    success, stdout, stderr = run_command(dep["check_command"])
    if not success:
        return {
            "installed": False,
            "version": None,
            "error": stderr or "Command failed"
        }
    
    # Extract version using regex
    version_match = re.search(dep["version_regex"], stdout)
    if version_match:
        version = version_match.group(1)
        return {
            "installed": True,
            "version": version,
            "error": None
        }
    else:
        return {
            "installed": False,
            "version": None,
            "error": "Version not detected"
        }

def check_package_managers(pm_config):
    """Check which package managers are available."""
    detected = {}
    for pm, config in pm_config.items():
        success, _, _ = run_command(config["check_command"])
        detected[pm] = {
            "available": success,
            "description": config["description"]
        }
    return detected

def check_python_package_managers(python_pms):
    """Check which Python package managers are available."""
    detected = {}
    for pm, config in python_pms.items():
        success, stdout, stderr = run_command(config["check_command"])
        detected[pm] = {
            "available": success,
            "description": config["description"],
            "version": stdout if success else stderr
        }
    return detected

if __name__ == "__main__":
    # Load config from stdin (JSON)
    config = json.load(sys.stdin)
    
    # Check dependencies
    results = {}
    for dep in config.get("required", []):
        results[dep["name"]] = check_dependency(dep)
    
    # Check package managers
    results["package_managers"] = check_package_managers(config.get("package_managers", {}))
    
    # Check Python package managers (only if Python is installed)
    if results.get("python3", {}).get("installed", False):
        results["python_package_managers"] = check_python_package_managers(config.get("python_package_managers", {}))
    else:
        results["python_package_managers"] = {}
    
    # Output results as JSON
    print(json.dumps(results, indent=2))