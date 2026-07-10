#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::process::Command;
use std::path::PathBuf;
use serde::{Serialize, Deserialize};

#[derive(Serialize, Deserialize)]
struct PythonResponse {
    status: String,
    data: serde_json::Value,
    error: Option<String>,
}

#[derive(Serialize)]
struct DependencyStatus {
    name: String,
    installed: bool,
    version: Option<String>,
    min_version: Option<String>,
}

#[derive(Serialize)]
struct SystemInfo {
    os: String,
    arch: String,
    home: String,
}

fn get_home_dir() -> PathBuf {
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            std::env::var_os("USERPROFILE")
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("/root"))
        })
}

fn python_bin() -> &'static str {
    if std::env::consts::OS == "windows" { "python" } else { "python3" }
}

#[tauri::command]
fn get_system_info() -> SystemInfo {
    SystemInfo {
        os: std::env::consts::OS.to_string(),
        arch: std::env::consts::ARCH.to_string(),
        home: get_home_dir().to_string_lossy().to_string(),
    }
}

#[tauri::command]
fn check_dependencies() -> Result<Vec<DependencyStatus>, String> {
    let mut deps = Vec::new();

    // Python
    let py_out = Command::new(python_bin()).arg("--version").output();
    deps.push(DependencyStatus {
        name: "python".to_string(),
        installed: py_out.is_ok(),
        version: py_out.ok().and_then(|o| {
            String::from_utf8(o.stdout).ok()
                .map(|s| s.trim().to_string())
        }),
        min_version: Some("3.10".to_string()),
    });

    // SQLite
    let sql_out = Command::new("sqlite3").arg("--version").output();
    deps.push(DependencyStatus {
        name: "sqlite3".to_string(),
        installed: sql_out.is_ok(),
        version: sql_out.ok().and_then(|o| {
            let s = String::from_utf8_lossy(&o.stdout);
            s.split_whitespace().next().map(|v| v.to_string())
        }),
        min_version: Some("3.30".to_string()),
    });

    // Git
    let git_out = Command::new("git").arg("--version").output();
    deps.push(DependencyStatus {
        name: "git".to_string(),
        installed: git_out.is_ok(),
        version: git_out.ok().and_then(|o| {
            let s = String::from_utf8_lossy(&o.stdout);
            s.split_whitespace().nth(2).map(|v| v.to_string())
        }),
        min_version: Some("2.0".to_string()),
    });

    Ok(deps)
}

#[tauri::command]
fn install_dependency(name: String) -> Result<String, String> {
    match name.as_str() {
        "python" => {
            let output = Command::new("bash")
                .args(["-c", "which apt && sudo apt install -y python3 python3-pip || which brew && brew install python3 || echo 'Gestionnaire de paquets non supporté'"])
                .output()
                .map_err(|e| format!("Erreur installation {}: {}", name, e))?;
            if output.status.success() {
                Ok("Python installé".to_string())
            } else {
                Err(String::from_utf8_lossy(&output.stderr).to_string())
            }
        }
        "sqlite3" => {
            let output = Command::new("bash")
                .args(["-c", "which apt && sudo apt install -y sqlite3 || which brew && brew install sqlite3 || echo 'Gestionnaire de paquets non supporté'"])
                .output()
                .map_err(|e| format!("Erreur installation {}: {}", name, e))?;
            if output.status.success() {
                Ok("SQLite installé".to_string())
            } else {
                Err(String::from_utf8_lossy(&output.stderr).to_string())
            }
        }
        "git" => {
            let output = Command::new("bash")
                .args(["-c", "which apt && sudo apt install -y git || which brew && brew install git || echo 'Gestionnaire de paquets non supporté'"])
                .output()
                .map_err(|e| format!("Erreur installation {}: {}", name, e))?;
            if output.status.success() {
                Ok("Git installé".to_string())
            } else {
                Err(String::from_utf8_lossy(&output.stderr).to_string())
            }
        }
        _ => Err(format!("Dépendance inconnue: {}", name)),
    }
}

#[tauri::command]
fn run_python_script(script_path: String, args: Vec<String>) -> Result<PythonResponse, String> {
    let home = get_home_dir();
    let root = home.join(".modelweaver");
    let full_path = root.join(&script_path);

    let output = Command::new(python_bin())
        .arg(&full_path)
        .args(&args)
        .current_dir(&root)
        .output()
        .map_err(|e| format!("Erreur exécution Python: {}", e))?;

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);

    if output.status.success() {
        match serde_json::from_str::<PythonResponse>(&stdout) {
            Ok(json) => Ok(json),
            Err(_) => Ok(PythonResponse {
                status: "success".to_string(),
                data: serde_json::Value::String(stdout.to_string()),
                error: None,
            }),
        }
    } else {
        Err(stderr.to_string())
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![
            get_system_info,
            check_dependencies,
            install_dependency,
            run_python_script,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
