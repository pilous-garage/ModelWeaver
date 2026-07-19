import json
import psutil
import sys

def check_disk_space(min_gb=5):
    try:
        disk = psutil.disk_usage('/')
        free_gb = disk.free / (1024**3)
        
        if free_gb >= min_gb:
            return {
                "status": "success",
                "data": {"free_gb": round(free_gb, 2), "enough": True},
                "error": None
            }
        else:
            return {
                "status": "error",
                "data": {"free_gb": round(free_gb, 2), "enough": False},
                "error": f"Espace disque insuffisant: {round(free_gb, 2)} GB disponibles (min {min_gb} GB requis)"
            }
    except Exception as e:
        return {
            "status": "error",
            "data": None,
            "error": str(e)
        }

if __name__ == "__main__":
    result = check_disk_space()
    print(json.dumps(result))
