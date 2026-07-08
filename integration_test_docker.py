import subprocess
import time
import os
from pathlib import Path

def run_command(cmd, cwd=None, env=None, print_output=False):
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: {result.stderr}")
    if print_output:
        print(result.stdout)
    return result

def main():
    base_dir = Path(__file__).resolve().parent
    dockerfile_bare = base_dir / "Dockerfile.bare"
    install_script = base_dir / "install_in_docker.py"
    project_dir = base_dir
    cache_dir = base_dir / ".modelweaver" / "cache"
    env_file = base_dir / ".env"

    # Ensure cache directory exists
    cache_dir.mkdir(parents=True, exist_ok=True)

    # 1. Build the image
    print("🔨 Building Docker image 'mw-bare'...")
    start_time = time.time()
    build_res = run_command(["docker", "build", "-t", "mw-bare", "-f", str(dockerfile_bare), "."])
    if build_res.returncode != 0:
        print("❌ Build failed!")
        print(build_res.stderr)
        return
    print(f"✅ Build finished in {time.time() - start_time:.2f}s")

    # 2. First run (COLD)
    print("\n🚀 Running FIRST pass (COLD installation)...")
    start_time = time.time()
    
    docker_run_cmd = [
        "docker", "run", "--rm",
        "-v", f"{project_dir}:/app",
        "-v", f"{cache_dir}:/app/.modelweaver_cache",
        "-v", f"{env_file}:/app/.env",
        "mw-bare",
        "python3", str(install_script.name)
    ]
    
    cold_res = run_command(docker_run_cmd, cwd=str(project_dir), print_output=True)
    cold_duration = time.time() - start_time
    
    if cold_res.returncode != 0:
        print("❌ Cold installation failed!")
        print(cold_res.stdout)
        print(cold_res.stderr)
        return
    print(f"✅ Cold installation finished in {cold_duration:.2f}s")

    # 3. Second run (WARM)
    print("\n🚀 Running SECOND pass (WARM installation)...")
    start_time = time.time()
    
    warm_res = run_command(docker_run_cmd, cwd=str(project_dir), print_output=True)
    warm_duration = time.time() - start_time
    
    if warm_res.returncode != 0:
        print("❌ Warm installation failed!")
        print(warm_res.stdout)
        print(warm_res.stderr)
        return
    print(f"✅ Warm installation finished in {warm_duration:.2f}s")
    
    if warm_duration < cold_duration:
        print(f"✨ Cache SUCCESS: Warm ({warm_duration:.2f}s) < Cold ({cold_duration:.2f}s)")
    else:
        print(f"⚠️  Cache WARNING: Warm ({warm_duration:.2f}s) is not faster than Cold ({cold_duration:.2f}s)")

    # 4. Test Opencode/Proxy (The ultimate test)
    print("\n🚀 Testing Opencode/Proxy inside container...")
    
    test_proxy_script = """
import requests
try:
    # The proxy should be listening on localhost:8000 in the container
    response = requests.get('http://localhost:8000/v1/models')
    print(f"Proxy models endpoint: {response.status_code}")
    if response.status_code == 200:
        print("✅ Proxy is alive and responding!")
    else:
        print(f"❌ Proxy returned {response.status_code}")
except Exception as e:
    print(f"❌ Proxy connection failed: {e}")
"""
    test_script_path = project_dir / "test_proxy_in_docker.py"
    test_script_path.write_text(test_proxy_script)

    # We'll run the proxy in the background, redirect its output to a log file,
    # then run the test script.
    log_file = project_dir / "proxy_container.log"
    if log_file.exists():
        log_file.unlink()

    # Use the virtualenv python for both the proxy and the test script
    venv_python = "/app/.venv/bin/python3"

    docker_test_cmd = [
        "docker", "run", "--rm",
        "-v", f"{project_dir}:/app",
        "-v", f"{cache_dir}:/app/.modelweaver_cache",
        "-v", f"{env_file}:/app/.env",
        "mw-bare",
        "sh", "-c", f"python3 {install_script.name} && {venv_python} /app/.modelweaver/litellm_router_proxy.py > /app/proxy_container.log 2>&1 & sleep 15 && {venv_python} /app/test_proxy_in_docker.py"
    ]

    print("Running proxy test (with installation in same container)...")
    proxy_test_res = run_command(docker_test_cmd, cwd=str(project_dir), print_output=True)
    
    # Print proxy logs if it failed
    if "✅ Proxy is alive" not in proxy_test_res.stdout:
        print("\n--- Proxy Logs ---")
        if log_file.exists():
            print(log_file.read_text())
        else:
            print("Could not find proxy_container.log")
        print("------------------\n")

    if "✅ Proxy is alive" in proxy_test_res.stdout:
        print("\n🌟 ALL TESTS PASSED! THE ASSEMBLY IS SOLID. 🌟")
    else:
        print("\n❌ Proxy test failed.")

    # Cleanup the temporary test script
    if test_script_path.exists():
        test_script_path.unlink()

    if log_file.exists():
        log_file.unlink()

if __name__ == "__main__":
    main()
