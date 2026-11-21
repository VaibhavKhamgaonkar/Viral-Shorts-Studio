import os
import sys
import shutil
import subprocess
import requests

OLLAMA_API = "http://localhost:11434"

def get_installed_ollama_models():
    """Returns list of model names currently pulled in Ollama."""
    try:
        res = requests.get(f"{OLLAMA_API}/api/tags", timeout=3)
        if res.status_code == 200:
            data = res.json()
            models = [m.get("name", "").split(":")[0] + ":" + m.get("name", "").split(":")[-1] for m in data.get("models", [])]
            return models
    except Exception:
        pass
    return []

def is_ollama_running():
    try:
        res = requests.get(f"{OLLAMA_API}/api/tags", timeout=2)
        return res.status_code == 200
    except Exception:
        return False

def ensure_model_available(target_model="qwen2.5:7b"):
    """
    Checks if Ollama is running and target model is pulled.
    If Ollama is installed but model is missing, pulls it automatically.
    """
    ollama_bin = shutil.which("ollama")
    if not ollama_bin:
        # Check standard default AppData path on Windows
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        fallback_path = os.path.join(local_app_data, "Programs", "Ollama", "ollama.exe")
        if os.path.exists(fallback_path):
            ollama_bin = fallback_path

    if not is_ollama_running() and ollama_bin:
        print("[*] Launching Ollama in background...")
        try:
            subprocess.Popen([ollama_bin, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            import time
            time.sleep(3)
        except Exception:
            pass

    installed_models = get_installed_ollama_models()
    base_target = target_model.split(":")[0]

    has_model = any(target_model in m or base_target in m for m in installed_models)
    
    if not has_model and ollama_bin:
        print(f"\n[*] Model '{target_model}' not found locally.")
        print(f"[*] Automatically pulling '{target_model}' via Ollama (one-time download ~4.5GB)...")
        try:
            subprocess.run([ollama_bin, "pull", target_model], check=True)
            print(f"[+] Successfully pulled '{target_model}'!\n")
            return True
        except Exception as e:
            print(f"[!] Failed to auto-pull model: {e}")
            return False
            
    return has_model

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "qwen2.5:7b"
    print(f"Checking Ollama and model '{target}'...")
    ready = ensure_model_available(target)
    if ready:
        print(f"[OK] Ollama and '{target}' are ready to use.")
    else:
        print(f"[!] Ollama or '{target}' not ready.")
