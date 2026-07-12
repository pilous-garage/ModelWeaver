#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::Emitter;
use std::process::Command;
use std::path::PathBuf;
use serde::{Serialize, Deserialize};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::sync::atomic::{AtomicBool, Ordering};
#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;

static ULTRA_DEBUG: AtomicBool = AtomicBool::new(false);

#[derive(Serialize, Deserialize)]
pub struct Config {
    pub warning_size_download_mb: u64,
    pub restart_delay_seconds: u64,
    pub dont_close_old_bootstrap: bool,
}

impl Default for Config {
    fn default() -> Self {
        Config {
            warning_size_download_mb: 20,
            restart_delay_seconds: 10,
            dont_close_old_bootstrap: false,
        }
    }
}

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

fn logger(level: &str, msg: &str) {
    let log_path = PathBuf::from("/tmp/bootstrap.log");
    let timestamp = chrono::Local::now().format("%Y-%m-%d %H:%M:%S").to_string();
    let formatted_msg = format!("[{}] [{}] {}\n", timestamp, level, msg);

    eprint!("{}", formatted_msg);

    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(log_path) {
        let _ = file.write_all(formatted_msg.as_bytes());
        let _ = file.flush();
    }
}

fn log_event(handle: &tauri::AppHandle, level: &str, msg: &str) {
    logger(level, msg);
    let _ = handle.emit("log-event", serde_json::json!({ "level": level, "msg": msg }));
}



fn current_exe_name() -> String {
    std::env::current_exe()
        .ok()
        .and_then(|p| p.file_name().map(|n| n.to_string_lossy().to_string()))
        .unwrap_or_else(|| "modelweaver-bootstrap".to_string())
}

#[tauri::command]
fn get_main_version() -> Result<String, String> {
    let home = get_home_dir();
    let main_path = home.join(".modelweaver").join("modelweaver");
    if !main_path.exists() {
        return Err("Main app not installed".to_string());
    }
    let output = Command::new(&main_path)
        .arg("--version")
        .output()
        .map_err(|e| format!("Erreur version: {}", e))?;
    if !output.status.success() {
        return Err("Erreur execution --version".to_string());
    }
    let version = String::from_utf8_lossy(&output.stdout).trim().to_string();
    // Expecting something like "modelweaver 0.1.0" or "v0.1.0"
    // We want just the tag part
    Ok(version.split_whitespace().last().unwrap_or(&version).to_string())
}

/// Version du MAIN installé, lue depuis le fichier version.txt posé lors de
/// l'installation (Cargo ne permet que 3 segments, donc la version complète
/// de release vit ici). Renvoie None si le main n'est pas encore installé.
fn read_installed_main_version() -> Option<String> {
    let p = get_home_dir().join(".modelweaver").join("version.txt");
    std::fs::read_to_string(&p).ok().map(|s| s.trim().to_string())
}

#[tauri::command]
fn get_platform() -> PlatformInfo {
    logger("INFO", "Backend: get_platform called");
    let info = PlatformInfo {
        os: std::env::consts::OS.to_string(),
        arch: std::env::consts::ARCH.to_string(),
    };
    info
}
#[tauri::command]
fn get_current_version() -> String {
    format!("v{}", env!("CARGO_PKG_VERSION"))
}

fn run_http_get(url: &str) -> Result<std::process::Output, String> {
    // Vérifier curl
    let curl_check = Command::new("which").arg("curl").output();
    if curl_check.map(|o| o.status.success()).unwrap_or(false) {
        return Command::new("curl")
            .args(["-s", "-H", "Accept: application/json", "-H", "User-Agent: ModelWeaver", url])
            .output()
            .map_err(|e| format!("Erreur curl: {}", e));
    }
    // Vérifier wget
    let wget_check = Command::new("which").arg("wget").output();
    if wget_check.map(|o| o.status.success()).unwrap_or(false) {
        return Command::new("wget")
            .args(["-q", "-O-", "--header=Accept: application/json", "--header=User-Agent: ModelWeaver", url])
            .output()
            .map_err(|e| format!("Erreur wget: {}", e));
    }
    Err("Ni curl ni wget n'est installé".to_string())
}

fn run_download(url: &str, dest: &str) -> Result<std::process::Output, String> {
    let curl_check = Command::new("which").arg("curl").output();
    if curl_check.map(|o| o.status.success()).unwrap_or(false) {
        return Command::new("curl")
            .args(["-L", "-o", dest, url])
            .output()
            .map_err(|e| format!("Erreur curl: {}", e));
    }
    let wget_check = Command::new("which").arg("wget").output();
    if wget_check.map(|o| o.status.success()).unwrap_or(false) {
        return Command::new("wget")
            .args(["-q", "-O", dest, url])
            .output()
            .map_err(|e| format!("Erreur wget: {}", e));
    }
    Err("Ni curl ni wget n'est installé".to_string())
}

#[tauri::command]
fn get_release_size(url: String) -> Result<u64, String> {
    let output = Command::new("curl")
        .args(["-sI", "-L", &url])
        .output()
        .map_err(|e| format!("Erreur curl HEAD: {}", e))?;

    if !output.status.success() {
        return Err("Impossible de récupérer la taille du fichier".to_string());
    }

    let headers = String::from_utf8_lossy(&output.stdout);
    let mut last_size: Option<u64> = None;
    for line in headers.lines() {
        if line.to_lowercase().starts_with("content-length:") {
            let size_str = line.split(':').nth(1).unwrap_or("0").trim();
            if let Ok(s) = size_str.parse::<u64>() {
                if s > 0 {
                    last_size = Some(s);
                }
            }
        }
    }
    last_size.ok_or_else(|| "En-tête Content-Length non trouvé".to_string())
}

fn parse_version(v: &str) -> Vec<u32> {
    v.trim_start_matches('v')
     .split('.')
     .filter_map(|s| s.parse::<u32>().ok())
     .collect()
}

fn is_newer_tag(latest: &str, current: &str) -> bool {
    let mut lv = parse_version(latest);
    let mut cv = parse_version(current);
    let max_len = lv.len().max(cv.len());
    lv.resize(max_len, 0);
    cv.resize(max_len, 0);
    lv > cv
}

#[derive(Serialize)]
struct UpdateInfo {
    latest_tag: String,
    current_tag: String,
    bootstrap_version: String,
    needs_update: bool,
    assets: Vec<serde_json::Value>,
}

/// Version COMPLÈTE du bootstrap (release tag, ex: 0.6.0.6). Cargo ne permet
/// que 3 segments, donc on l'embarque via include_str! depuis un fichier généré
/// par auto-bump avant le build. Sans ça, le bootstrap se verrait en "v0.6.0"
/// et se croirait toujours obsolète face au latest "v0.6.0.x".
fn bootstrap_version() -> &'static str {
    include_str!("bootstrap-version.txt").trim()
}

#[tauri::command]
async fn check_update() -> Result<UpdateInfo, String> {
    // Version propre du bootstrap (embarquée, release tag complet).
    let bootstrap_version = format!("v{}", bootstrap_version());
    // Version du MAIN réellement installé (source de vérité = version.txt).
    // Si absent (main pas encore installé), on considère v0.0.0 → une MAJ sera
    // proposée. Cela corrige l'ancien faux-positif « un point de moins » où le
    // bootstrap se comparait à sa propre version Cargo tronquée.
    let current_tag = match read_installed_main_version() {
        Some(v) if !v.is_empty() => {
            if v.starts_with('v') { v } else { format!("v{}", v) }
        }
        _ => "v0.0.0".to_string(),
    };
    logger("INFO", &format!("check_update: bootstrap={}, installed_main={}, fetching latest from GitHub", bootstrap_version, current_tag));
    let url = format!(
        "https://api.github.com/repos/pilous-garage/ModelWeaver/releases/latest"
    );

    let output = run_http_get(&url).map_err(|e| e.to_string())?;

    if !output.status.success() {
        return Err("GitHub a retourné une erreur".to_string());
    }

    let body = String::from_utf8_lossy(&output.stdout);
    let json: serde_json::Value = serde_json::from_str(&body).map_err(|e| e.to_string())?;

    let latest_tag = json["tag_name"].as_str().unwrap_or("v0.0.0").to_string();
    // needs_update compare le main installé au latest — gère un nombre
    // arbitraire de segments (vX.Y.Z.W...), robuste jusqu'à 6+ points.
    let needs_update = is_newer_tag(&latest_tag, &current_tag);
    logger("INFO", &format!("check_update: latest={}, current={}, needs_update={}", latest_tag, current_tag, needs_update));
    let assets = json["assets"].as_array().cloned().unwrap_or_default();

    Ok(UpdateInfo {
        latest_tag,
        current_tag,
        bootstrap_version,
        needs_update,
        assets,
    })
}

#[tauri::command]
async fn self_update(dry_run: bool) -> Result<String, String> {
    let os = std::env::consts::OS;
    let arch = std::env::consts::ARCH;
    let exe_name = current_exe_name();
    let current = format!("v{}", env!("CARGO_PKG_VERSION"));
    let url = format!(
        "https://github.com/pilous-garage/ModelWeaver/releases/latest/download/{}-{}-{}",
        exe_name, os, arch
    );
    logger("INFO", &format!("self_update: version={}, url={}, dry_run={}", current, url, dry_run));

    if dry_run {
        return Ok(format!("Téléchargerait: {}", url));
    }

    let current_path = std::env::current_exe()
        .map_err(|e| format!("Erreur chemin binaire: {}", e))?;
    let backup_path = current_path.with_extension("bak");

    // Téléchargement
    let tmp_path = get_home_dir().join(".modelweaver").join("bootstrap_update");
    let output = run_download(&url, tmp_path.to_str().unwrap())?;

    if !output.status.success() {
        return Err("Échec du téléchargement de la mise à jour".to_string());
    }

    #[cfg(unix)]
    std::fs::set_permissions(&tmp_path, std::fs::Permissions::from_mode(0o755))
        .map_err(|e| format!("Erreur chmod: {}", e))?;

    // Backup par copie (rename casserait /proc/self/exe → " (deleted)")
    let _ = std::fs::copy(&current_path, &backup_path);
    // On garde rename ici car copy échoue avec "Text file busy" sur le binaire en cours
    std::fs::rename(&tmp_path, &current_path)
        .map_err(|e| format!("Erreur remplacement: {}", e))?;

    Ok("Mise à jour installée, veuillez relancer l'application".to_string())
}

#[tauri::command]
async fn self_update_from_path(new_binary: String) -> Result<String, String> {
    let current_path = std::env::current_exe()
        .map_err(|e| format!("Erreur chemin: {}", e))?;
    let backup_path = current_path.with_extension("bak");
    let version = format!("v{}", env!("CARGO_PKG_VERSION"));
    logger("INFO", &format!("self_update_from_path: version={}, from={}, to={}", version, new_binary, current_path.display()));

    let new_path = std::path::PathBuf::from(&new_binary);
    if !new_path.exists() {
        return Err(format!("Binaire introuvable: {}", new_binary));
    }

    #[cfg(unix)]
    std::fs::set_permissions(&new_path, std::fs::Permissions::from_mode(0o755))
        .map_err(|e| format!("Erreur chmod: {}", e))?;

    // Backup par copie (rename casserait /proc/self/exe → " (deleted)")
    let _ = std::fs::copy(&current_path, &backup_path);
    std::fs::rename(&new_path, &current_path)
        .map_err(|e| format!("Erreur remplacement: {}", e))?;

    Ok("Mise à jour installée depuis le fichier local".to_string())
}

#[tauri::command]
async fn download_release(app_handle: tauri::AppHandle, url: String) -> Result<String, String> {
    log_event(&app_handle, "INFO", &format!("Téléchargement du release depuis: {}", url));
    let home = get_home_dir();
    let cache_dir = home.join(".modelweaver").join("cache");
    std::fs::create_dir_all(&cache_dir).map_err(|e| e.to_string())?;

    let archive_path = cache_dir.join("modelweaver.tar.gz");
    let output = run_download(&url, archive_path.to_str().unwrap())?;

    if !output.status.success() {
        let err = "Échec du téléchargement du release".to_string();
        log_event(&app_handle, "ERROR", &err);
        return Err(err);
    }

    log_event(&app_handle, "SUCCESS", &format!("Téléchargement terminé: {}", archive_path.display()));
    Ok(archive_path.to_string_lossy().to_string())
}

#[tauri::command]
async fn unpack_release(app_handle: tauri::AppHandle, archive_path: String) -> Result<String, String> {
    log_event(&app_handle, "INFO", &format!("Extraction de l'archive: {}", archive_path));
    let home = get_home_dir();
    let target = home.join(".modelweaver");
    std::fs::create_dir_all(&target).map_err(|e| e.to_string())?;

    // Vérifier tar
    let tar_check = Command::new("which").arg("tar").output();
    if !tar_check.map(|o| o.status.success()).unwrap_or(false) {
        let err = "tar n'est pas installé".to_string();
        log_event(&app_handle, "ERROR", &err);
        return Err(err);
    }
    
    let output = Command::new("tar")
        .args(["-xzf", &archive_path, "-C", target.to_str().unwrap()])
        .output()
        .map_err(|e| format!("Erreur extraction: {}", e))?;

    if !output.status.success() {
        let err = "Échec de l'extraction de l'archive".to_string();
        log_event(&app_handle, "ERROR", &err);
        return Err(err);
    }

    // Nettoyage
    let _ = std::fs::remove_file(&archive_path);

    log_event(&app_handle, "SUCCESS", &format!("Extraction terminée dans: {}", target.display()));
    Ok(target.to_string_lossy().to_string())
}

#[tauri::command]
async fn launch_main(app_handle: tauri::AppHandle) -> Result<String, String> {
    let version = format!("v{}", env!("CARGO_PKG_VERSION"));
    log_event(&app_handle, "INFO", &format!("launch_main: bootstrap v{}", version));
    let home = get_home_dir();
    let main_path = home.join(".modelweaver").join("modelweaver");

    if !main_path.exists() {
        let err = "Le binaire principal est introuvable".to_string();
        log_event(&app_handle, "ERROR", &err);
        return Err(err);
    }

    // Éviter de se relancer soi-même (vortex)
    if let Ok(exe) = std::env::current_exe() {
        if exe.canonicalize().ok() == main_path.canonicalize().ok() {
            let err = "Le binaire principal est identique au bootstrap — vortex évité".to_string();
            log_event(&app_handle, "ERROR", &err);
            return Err(err);
        }
    }

    Command::new(&main_path)
        .spawn()
        .map_err(|e| format!("Erreur lancement: {}", e))?;

    log_event(&app_handle, "SUCCESS", "ModelWeaver lancé avec succès");
    Ok("ModelWeaver lancé".to_string())
}

#[tauri::command]
fn load_config() -> Result<Config, String> {
    let config_path = get_home_dir().join(".modelweaver").join("config.json");
    if config_path.exists() {
        let config_str = fs::read_to_string(&config_path).map_err(|e| e.to_string())?;
        serde_json::from_str(&config_str).map_err(|e| e.to_string())
    } else {
        let default_config = Config::default();
        let config_str = serde_json::to_string_pretty(&default_config).map_err(|e| e.to_string())?;
        let _ = fs::create_dir_all(config_path.parent().unwrap());
        fs::write(&config_path, config_str).map_err(|e| e.to_string())?;
        Ok(default_config)
    }
}

#[tauri::command]
async fn log_error(message: String) -> Result<(), String> {
    let log_path = get_home_dir().join(".modelweaver").join("bootstrap.log");
    let _ = fs::create_dir_all(log_path.parent().unwrap());
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .map_err(|e| e.to_string())?;
    writeln!(file, "LOG: {}", message).map_err(|e| e.to_string())?;
    eprintln!("LOG ERROR: {}", message);
    Ok(())
}

#[tauri::command]
fn load_terms() -> String {
    include_str!("../terms/TOS.md").to_string()
}

#[tauri::command]
fn get_home_dir_cmd() -> String {
    get_home_dir().to_string_lossy().to_string()
}

#[tauri::command]
fn get_repository_info() -> String {
    "https://github.com/pilous-garage/ModelWeaver".to_string()
}

#[tauri::command]
fn open_url(url: String) -> Result<(), String> {
    std::process::Command::new("xdg-open")
        .arg(&url)
        .spawn()
        .or_else(|_| std::process::Command::new("open").arg(&url).spawn())
        .or_else(|_| std::process::Command::new("start").arg(&url).spawn())
        .map_err(|e| format!("Impossible d'ouvrir l'URL: {}", e))?;
    Ok(())
}

#[tauri::command]
async fn restart_app(app_handle: tauri::AppHandle, dont_close: bool) -> Result<String, String> {
    let exe = std::env::current_exe().map_err(|e| format!("Erreur chemin: {}", e))?;
    // Après rename dans self_update, /proc/self/exe ajoute " (deleted)" au path
    let exe_str = exe.to_string_lossy().trim_end_matches(" (deleted)").to_string();
    let cleaned_path = std::path::PathBuf::from(&exe_str);
    logger("INFO", &format!("restart_app: spawning {} (cleaned from: {})", cleaned_path.display(), exe.display()));
    std::process::Command::new(&cleaned_path)
        .spawn()
        .map_err(|e| format!("Erreur lancement: {}", e))?;
    if !dont_close {
        std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(300));
            app_handle.exit(0);
        });
    }
    Ok("Nouveau bootstrap lancé".to_string())
}

fn main() {
    let version = format!("v{}", env!("CARGO_PKG_VERSION"));
    logger("INFO", &format!("Lancement de ModelWeaver Bootstrap {}", version));
    let args: Vec<String> = std::env::args().collect();
    if args.contains(&"--ultra-debug".to_string()) {
        ULTRA_DEBUG.store(true, Ordering::SeqCst);
        logger("DEBUG", "Mode ULTRA-DEBUG activé");
    }

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![
            get_platform,
            get_current_version,
            get_main_version,
            get_release_size,
            check_update,
            self_update,
            self_update_from_path,
            download_release,
            unpack_release,
            launch_main,
            load_config,
            log_error,
            get_repository_info,
            get_home_dir_cmd,
            load_terms,
            open_url,
            restart_app,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
