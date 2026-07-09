import yaml # Added for validation
from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import List, Optional
import os
from pathlib import Path
from dotenv import load_dotenv
import requests
import base64

load_dotenv()

app = FastAPI(title="ModelWeaver Community Backend")
auth_scheme = HTTPBearer()

# Config
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO = "pilous-garage/ModelWeaver"
DATA_BRANCH = "yaml-data"

class PackageMetadata(BaseModel):
    name: str
    version: str
    description: Optional[str] = None
    author: str

# --- Validation ---
def validate_recipe_yaml(content: str) -> bool:
    """Valide que le YAML est correct et respecte la structure minimale."""
    try:
        data = yaml.safe_load(content)
        if not isinstance(data, dict):
            return False
        # Basic required fields for a recipe
        required = ["name", "version"] 
        # Depending on the spec, we can add more
        return all(k in data for k in required)
    except Exception:
        return False

# --- GitHub Service ---
def push_yaml_to_github(package_name: str, version: str, content: str):
    path = f"packages/{package_name}/{version}.yaml"
    url = f"https://api.github.com/repos/{REPO}/contents/{path}?ref={DATA_BRANCH}"
    
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    
    sha = None
    res = requests.get(url, headers=headers)
    if res.status_code == 200:
        sha = res.json().get("sha")

    data = {
        "message": f"Upload package {package_name} v{version}",
        "content": base64.b64encode(content.encode()).decode(),
        "branch": DATA_BRANCH,
        "sha": sha
    }

    res = requests.put(url, json=data, headers=headers)
    return res.status_code in (200, 201)

def fetch_yaml_from_github(package_name: str, version: str) -> Optional[str]:
    path = f"packages/{package_name}/{version}.yaml"
    url = f"https://api.github.com/repos/{REPO}/contents/{path}?ref={DATA_BRANCH}"
    
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    res = requests.get(url, headers=headers)
    
    if res.status_code == 200:
        content_b64 = res.json().get("content")
        return base64.b64decode(content_b64).decode("utf-8")
    return None

# --- Endpoints ---

@app.get("/")
def read_root():
    return {"status": "ModelWeaver Community Backend is running"}

@app.post("/upload-yaml")
async def upload_yaml(
    metadata: PackageMetadata, 
    file: UploadFile = File(...)
):
    content = (await file.read()).decode("utf-8")
    
    # 1. Strict Validation (Untrusted input)
    if not validate_recipe_yaml(content):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Invalid YAML format or missing required fields (name, version)"
        )
    
    # 2. Push to GitHub via Admin Token
    success = push_yaml_to_github(metadata.name, metadata.version, content)
    
    if not success:
        raise HTTPException(status_code=500, detail="Failed to push YAML to GitHub")
    
    return {"message": f"Package {metadata.name} v{metadata.version} uploaded successfully"}

@app.get("/download-yaml/{package}/{version}")
def download_yaml(package: str, version: str):
    content = fetch_yaml_from_github(package, version)
    if not content:
        raise HTTPException(status_code=404, detail="Package or version not found")
    return {"package": package, "version": version, "content": content}

@app.get("/packages")
def list_packages():
    # TODO: Fetch from Turso
    return {"packages": ["example-pkg-1", "example-pkg-2"]}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
