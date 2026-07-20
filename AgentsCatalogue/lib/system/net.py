"""Requêtes HTTP (GET / POST / PUT / PATCH / DELETE).

Migrées depuis services/skill_manager.py (_exec_http_* + _http_request).
"""

import requests


def _http_request(method: str, inputs: dict) -> dict:
    url = inputs.get("url", "")
    if not url or not url.startswith(("http://", "https://")):
        return {"status_code": 0, "headers": {}, "body": "",
                "error": "url invalide (http/https requis)"}
    headers = dict(inputs.get("headers", {}) or {})
    timeout = int(inputs.get("timeout", 15))
    max_bytes = int(inputs.get("max_bytes", 1048576))
    verify_ssl = bool(inputs.get("verify_ssl", True))
    body_text = inputs.get("body_text")
    json_body = inputs.get("body")
    req_kwargs = dict(headers=headers, timeout=timeout,
                      verify=verify_ssl, stream=True)
    if method != "GET":
        if body_text:
            req_kwargs["data"] = body_text
        elif json_body is not None:
            req_kwargs["json"] = json_body
    try:
        resp = requests.request(method, url, **req_kwargs)
        body = b""
        for chunk in resp.iter_content(chunk_size=8192):
            body += chunk
            if len(body) >= max_bytes:
                break
        resp_headers = {k: v for k, v in resp.headers.items()}
        text = body[:max_bytes].decode("utf-8", errors="replace")
        return {"status_code": resp.status_code,
                "headers": resp_headers, "body": text, "error": ""}
    except Exception as e:
        return {"status_code": 0, "headers": {}, "body": "",
                "error": str(e)}


def http_get(inputs: dict, ws: str) -> dict:
    return _http_request("GET", inputs)


def http_post(inputs: dict, ws: str) -> dict:
    method = str(inputs.get("method", "POST")).upper()
    if method not in ("POST", "PUT", "PATCH", "DELETE"):
        return {"status_code": 0, "headers": {}, "body": "",
                "error": f"méthode non supportée: {method}"}
    return _http_request(method, inputs)


__skills__ = ["http_get", "http_post"]
