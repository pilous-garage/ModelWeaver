import os
import re

# Chercher toutes les occurrences de 'ollama' en tant que provider_type dans le code source (hors tests)
# et les renommer en 'local_ollama'.

def rename_ollama():
    for file in os.listdir():
        if file.endswith('.py') and not file.startswith('test_'): # hors tests
            with open(file, 'r') as f:
                content = f.read()
            # Recherche de toutes les occurrences de 'ollama'
            new_content = re.sub(r'provider_type: ollama', 'provider_type: local_ollama', content)
            with open(file, 'w') as f:
                f.write(new_content)

if __name__ == '__main__':
    rename_ollama()
