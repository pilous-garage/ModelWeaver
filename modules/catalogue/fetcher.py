import requests
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("modelweaver.catalogue.discovery")

class Fetcher:
    def __init__(self, models_dev_url: str = "https://models.dev/api.json", nvidia_url: str = "https://integrate.api.nvidia.com/v1/models"):
        self.models_dev_url = models_dev_url
        self.nvidia_url = nvidia_url

    def fetch_models_dev(self) -> Dict[str, Any]:
        """Fetches models from models.dev."""
        try:
            response = requests.get(self.models_dev_url, timeout=30, headers={"User-Agent": "ModelWeaver/1.0"})
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error("models.dev discovery failed", extra={
                "source": "models.dev",
                "error_type": type(e).__name__,
                "error": str(e),
            })
            return {}

    def fetch_nvidia_models(self) -> List[Dict[str, Any]]:
        """Fetches models directly from NVIDIA API."""
        try:
            response = requests.get(self.nvidia_url, timeout=20)
            response.raise_for_status()
            data = response.json()
            return data.get("data", [])
        except Exception as e:
            logger.error("NVIDIA discovery failed", extra={
                "source": "nvidia",
                "error_type": type(e).__name__,
                "error": str(e),
            })
            return []

    def is_chat_model(self, model_id: str) -> bool:
        """Simple check to see if model is likely a chat model."""
        mid = model_id.lower()
        non_chat_keywords = [
            "embedding", "embed", "bge", "tts", "speech", "whisper",
            "imagen", "veo", "lyria", "sora",
            "robotics", "learnlm",
            "prompt-guard", "safeguard",
        ]
        for kw in non_chat_keywords:
            if kw in mid:
                return False
        return True
