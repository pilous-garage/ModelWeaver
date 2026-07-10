#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::process::Command;
use std::path::PathBuf;
use serde::Serialize;

#[derive(Serialize)]
struct PlatformInfo {
    os: String,
    arch: String,
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

fn current_exe_name() -> String {
    std::env::current_exe()
        .ok()
        .and_then(|p| p.file_name().map(|n| n.to_string_lossy().to_string()))
        .unwrap_or_else(|| "modelweaver-bootstrap".to_string())
}

#[tauri::command]
fn get_platform() -> PlatformInfo {
    PlatformInfo {
        os: std::env::consts::OS.to_string(),
        arch: std::env::consts::ARCH.to_string(),
    }
}

#[tauri::command]
async fn check_update() -> Result<String, String> {
    let os = std::env::consts::OS;
    let arch = std::env::consts::ARCH;
    let url = format!(
        "https://api.github.com/repos/pilous-garage/ModelWeaver/releases/latest"
    );

    let output = Command::new("curl")
        .args(["-s", "-H", "Accept: application/json", &url])
        .output()
        .map_err(|e| format!("Erreur vérification mise à jour: {}", e))?;

    if !output.status.success() {
        return Err("Impossible de contacter GitHub".to_string());
    }

    let body = String::from_utf8_lossy(&output.stdout);
    let json: serde_json::Value = serde_json::from_str(&body)
        .map_err(|e| format!("Erreur parsing JSON: {}", e))?;

    let tag = json["tag_name"].as_str().unwrap_or("unknown");
    Ok(format!("{}-{}-{}", tag, os, arch))
}

#[tauri::command]
async fn self_update(dry_run: bool) -> Result<String, String> {
    let os = std::env::consts::OS;
    let arch = std::env::consts::ARCH;
    let exe_name = current_exe_name();
    let url = format!(
        "https://github.com/pilous-garage/ModelWeaver/releases/latest/download/{}-{}-{}",
        exe_name, os, arch
    );

    if dry_run {
        return Ok(format!("Téléchargerait: {}", url));
    }

    let current_path = std::env::current_exe()
        .map_err(|e| format!("Erreur chemin binaire: {}", e))?;
    let backup_path = current_path.with_extension("bak");

    // Téléchargement
    let tmp_path = get_home_dir().join(".modelweaver").join("bootstrap_update");
    let output = Command::new("curl")
        .args(["-L", "-o", tmp_path.to_str().unwrap(), &url])
        .output()
        .map_err(|e| format!("Erreur téléchargement mise à jour: {}", e))?;

    if !output.status.success() {
        return Err("Échec du téléchargement de la mise à jour".to_string());
    }

    // Remplacer le binaire courant
    std::fs::rename(&current_path, &backup_path)
        .map_err(|e| format!("Erreur backup: {}", e))?;
    std::fs::rename(&tmp_path, &current_path)
        .map_err(|e| format!("Erreur remplacement: {}", e))?;

    Ok("Mise à jour installée, veuillez relancer l'application".to_string())
}

#[tauri::command]
async fn download_release(url: String) -> Result<String, String> {
    let home = get_home_dir();
    let cache_dir = home.join(".modelweaver").join("cache");
    std::fs::create_dir_all(&cache_dir).map_err(|e| e.to_string())?;

    let archive_path = cache_dir.join("modelweaver.tar.gz");
    let output = Command::new("curl")
        .args(["-L", "-o", archive_path.to_str().unwrap(), &url])
        .output()
        .map_err(|e| format!("Erreur téléchargement: {}", e))?;

    if !output.status.success() {
        return Err("Échec du téléchargement du release".to_string());
    }

    Ok(archive_path.to_string_lossy().to_string())
}

#[tauri::command]
async fn unpack_release(archive_path: String) -> Result<String, String> {
    let home = get_home_dir();
    let target = home.join(".modelweaver");
    std::fs::create_dir_all(&target).map_err(|e| e.to_string())?;

    let output = Command::new("tar")
        .args(["-xzf", &archive_path, "-C", target.to_str().unwrap()])
        .output()
        .map_err(|e| format!("Erreur extraction: {}", e))?;

    if !output.status.success() {
        return Err("Échec de l'extraction de l'archive".to_string());
    }

    // Nettoyage
    let _ = std::fs::remove_file(&archive_path);

    Ok(target.to_string_lossy().to_string())
}

#[tauri::command]
async fn launch_main() -> Result<String, String> {
    let home = get_home_dir();
    let main_path = home.join(".modelweaver").join("modelweaver");

    if !main_path.exists() {
        return Err("Le binaire principal est introuvable".to_string());
    }

    Command::new(&main_path)
        .spawn()
        .map_err(|e| format!("Erreur lancement: {}", e))?;

    Ok("ModelWeaver lancé".to_string())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![
            get_platform,
            check_update,
            self_update,
            download_release,
            unpack_release,
            launch_main,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
