import json
import subprocess
import os

def install_core():
    try:
        # Ici, on pourrait appeler le bootstrap script : 
        # subprocess.run(["bash", "projetclient/modelweaver.sh", "--autoinstall"], check=True)
        
        # Pour le moment, on simule une installation réussie
        return {
            "status": "success",
            "data": {"installed": True},
            "error": None
        }
    except Exception as e:
        return {
            "status": "error",
            "data": None,
            "error": str(e)
        }

if __name__ == "__main__":
    result = install_core()
    print(json.dumps(result))
