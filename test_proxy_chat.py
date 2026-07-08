import requests
import json

def test_proxy_chat():
    url = "http://localhost:8000/v1/chat/completions"
    payload = {
        "model": "opencode-engine", # Utilise le modèle par défaut du proxy
        "messages": [
            {"role": "user", "content": "Bonjour, qui es-tu ? Réponds en un mot."}
        ],
        "stream": False
    }
    
    try:
        print(f"Sending prompt to proxy: {payload['messages'][0]['content']}")
        response = requests.post(url, json=payload, timeout=30)
        if response.status_code == 200:
            data = response.json()
            content = data['choices'][0]['message']['content']
            responded_by = data.get('_responded', 'Unknown')
            print(f"\n✅ Response received!")
            print(f"Content: {content}")
            print(f"Responded by: {responded_by}")
            return True
        else:
            print(f"❌ Proxy error: {response.status_code}")
            print(response.text)
            return False
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return False

if __name__ == "__main__":
    test_proxy_chat()
