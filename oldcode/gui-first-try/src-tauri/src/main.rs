#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::process::Command;
use std::path::PathBuf;
use std::fs;
use serde::{Serialize, Deserialize};

#[derive(Serialize, Deserialize)]
struct PythonResponse {
    status: String,
    data: serde_json::Value,
    error: Option<String>,
}

#[derive(Serialize, Deserialize)]
struct DependencyStatus {
    name: String,
    installed: bool,
    version: Option<String>,
}

// --- Gestionnaire de Commandes Multisystème ---
struct CommandManager;

impl CommandManager {
    fn os() -> &'static str {
        std::env::consts::OS
    }

    fn download_cmd(&self, url: &str, output: &str) -> Command {
        let cmd = match Self::os() {
            "windows" => {
                let mut c = Command::new("powershell");
                c.args(["-Command", &format!("Invoke-WebRequest -Uri '{}' -OutFile '{}'", url, output)]);
                c
            },
            _ => {
                let mut c = Command::new("curl");
                c.arg("-L").arg(url).arg("-o").arg(output);
                c
            }
        };
        cmd
    }

    fn unpack_cmd(&self, archive: &str, target: &str) -> Command {
        let cmd = match Self::os() {
            "windows" => {
                let mut c = Command::new("powershell");
                c.args(["-Command", &format!("Expand-Archive -Path '{}' -DestinationPath '{}' -Force", archive, target)]);
                c
            },
            _ => {
                let mut c = Command::new("tar");
                c.arg("-xzf").arg(archive).arg("-C").arg(target);
                c
            }
        };
        cmd
    }

    fn bootstrap_cmd(&self, script_path: &str, args: &[String]) -> Command {
        let cmd = match Self::os() {
            "windows" => {
                let mut c = Command::new("powershell");
                c.args(["-ExecutionPolicy", "Bypass", "-File", &format!("{}\\.modelweaver\\modelweaver.ps1", get_home_dir().to_str().unwrap()), "-autoinstall"]);
                c
            },
            "macos" => {
                let mut c = Command::new("osascript");
                c.args(["-e", &format!("do shell script 'bash {} {}' with administrator privileges", script_path, args.join(" "))]);
                c
            },
            _ => {
                let mut c = Command::new("pkexec");
                c.arg("bash").arg(script_path).args(args);
                c
            }
        };
        cmd
    }

    fn python_cmd(&self) -> String {
        if Self::os() == "windows" { "python".to_string() } else { "python3".to_string() }
    }
}

// --- Fonctions utilitaires ---
fn get_home_dir() -> PathBuf {
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            std::env::var_os("USERPROFILE")
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("/root"))
        })
}

// --- Commandes Tauri ---
#[tauri::command]
async fn download_and_unpack(url: String, install_dir: String) -> Result<String, String> {
    let manager = CommandManager;
    let home = get_home_dir();
    let target_path = home.join(&install_dir);
    let archive_path = home.join("modelweaver_latest.tar.gz");

    fs::create_dir_all(&target_path).map_err(|e| e.to_string())?;

    manager.download_cmd(&url, archive_path.to_str().unwrap())
        .status()
        .map_err(|e| format!("Erreur téléchargement: {}", e))?;

    manager.unpack_cmd(archive_path.to_str().unwrap(), target_path.to_str().unwrap())
        .status()
        .map_err(|e| format!("Erreur extraction: {}", e))?;

    let _ = fs::remove_file(archive_path);

    Ok(format!("Projet installé avec succès dans {}", target_path.display()))
}

#[tauri::command]
fn run_bootstrap_script(install_dir: String) -> Result<String, String> {
    let manager = CommandManager;
    let home = get_home_dir();
    let script_path = home.join(&install_dir).join("modelweaver.sh");

    if !script_path.exists() {
        return Err("Le script de bootstrap est introuvable".to_string());
    }

    let output = manager.bootstrap_cmd(script_path.to_str().unwrap(), &["--autoinstall".to_string()])
        .output()
        .map_err(|e| format!("Erreur lors du lancement du bootstrap: {}", e))?;

    if output.status.success() {
        Ok("Dépendances système installées avec succès".to_string())
    } else {
        Err(String::from_utf8_lossy(&output.stderr).to_string())
    }
}

#[tauri::command]
fn check_dependencies() -> Result<Vec<DependencyStatus>, String> {
    let manager = CommandManager;
    let mut deps = Vec::new();
    let py_bin = manager.python_cmd();
    
    let py_check = Command::new(&py_bin).arg("--version").output();
    deps.push(DependencyStatus {
        name: "python".to_string(),
        installed: py_check.is_ok(),
        version: py_check.ok().and_then(|o| Some(String::from_utf8_lossy(&o.stdout).to_string().trim().to_string())),
    });

    let sql_check = Command::new("sqlite3").arg("--version").output();
    deps.push(DependencyStatus {
        name: "sqlite3".to_string(),
        installed: sql_check.is_ok(),
        version: sql_check.ok().and_then(|o| Some(String::from_utf8_lossy(&o.stdout).to_string().trim().to_string())),
    });

    Ok(deps)
}

#[tauri::command]
fn run_python_script(script_path: String, args: Vec<String>) -> Result<PythonResponse, String> {
    let manager = CommandManager;
    let home = get_home_dir();
    let root = home.join(".modelweaver");
    let full_script_path = root.join(&script_path);
    
    let output = Command::new(manager.python_cmd())
        .arg(&full_script_path)
        .args(&args)
        .current_dir(&root)
        .output()
        .map_err(|e| format!("L'interpréteur Python est introuvable: {}", e))?;

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);

    if output.status.success() {
        if let Ok(json) = serde_json::from_str::<PythonResponse>(&stdout) {
            Ok(json)
        } else {
            Ok(PythonResponse {
                status: "success".to_string(),
                data: serde_json::Value::String(stdout.to_string()),
                error: None,
            })
        }
    } else {
        Err(stderr.to_string())
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![
            check_dependencies, 
            run_python_script, 
            download_and_unpack, 
            run_bootstrap_script
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
