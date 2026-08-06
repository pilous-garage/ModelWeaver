#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::{Mutex, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};
use std::fs::OpenOptions;
use std::io::Write;
use serde::Serialize;
use tauri::Manager;

#[cfg(unix)]
use std::os::unix::process::CommandExt;

// ============================================================
//  ModelWeaver GUI v2 — binaire Tauri minimal.
//  - Délègue au superviseur Python s'il existe (sinon ne lance rien).
//  - Injecte le label de la fenêtre dans chaque webview (le frontend le lit
//    via window.__MW_WINDOW_LABEL).
//  - Crée les fenêtres d'une session (layout/session YAML).
// ============================================================

fn get_home_dir() -> PathBuf {
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            std::env::var_os("USERPROFILE")
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("/root"))
        })
}

fn mw_home() -> PathBuf {
    if let Some(v) = std::env::var_os("MODELWEAVER_HOME") {
        if !v.as_os_str().is_empty() {
            return PathBuf::from(v);
        }
    }
    let opt = PathBuf::from("/opt/modelweaver");
    if opt.exists() {
        return opt;
    }
    get_home_dir().join(".modelweaver")
}

fn log_path() -> PathBuf {
    let dir = mw_home();
    let _ = std::fs::create_dir_all(&dir);
    dir.join("gui-v2.log")
}

fn log_to_file(level: &str, msg: &str) {
    let path = log_path();
    let ts = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs();
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(f, "[{}] [{}] {}", ts, level, msg);
    }
}

fn python_bin() -> &'static str {
    if std::env::consts::OS == "windows" { "python" } else { "python3" }
}

/// Racine du dépôt (contient services/).
fn find_repo_root() -> PathBuf {
    let mw = mw_home();
    if mw.join("services").is_dir() {
        return mw;
    }
    if let Ok(exe) = std::env::current_exe() {
        let mut dir = exe.parent();
        while let Some(d) = dir {
            if d.join("services").is_dir() {
                return d.to_path_buf();
            }
            dir = d.parent();
        }
    }
    PathBuf::from(".")
}

/// Vrai si un lock valide (PID vivant) existe.
fn lock_taken(name: &str) -> bool {
    let p = mw_home().join("run").join(format!("{}.pid", name));
    if let Ok(s) = std::fs::read_to_string(&p) {
        if let Ok(pid) = s.trim().parse::<i32>() {
            if pid > 0 && std::fs::metadata(format!("/proc/{}", pid)).is_ok() {
                return true;
            }
        }
    }
    false
}

/// Délègue au superviseur Python installé (source de vérité des services).
fn delegate_to_python_supervisor() -> bool {
    let repo_root = find_repo_root();
    let supervisor_py = repo_root.join("services").join("supervisor").join("main.py");
    if !supervisor_py.exists() {
        log_to_file("SUPERVISOR", "pas de superviseur Python installé — GUI autonome");
        return false;
    }
    log_to_file("SUPERVISOR", &format!("superviseur Python trouvé: {}", supervisor_py.display()));
    if lock_taken("supervisor") {
        log_to_file("SUPERVISOR", "superviseur Python déjà en cours — délégation");
        return true;
    }
    log_to_file("SUPERVISOR", "démarrage du superviseur Python…");
    let mut cmd = Command::new(python_bin());
    cmd.arg(&supervisor_py)
        .env("PYTHONPATH", repo_root.to_string_lossy().to_string());
    #[cfg(unix)]
    cmd.process_group(0);
    let logp = mw_home().join("logs").join("service-supervisor.log");
    if let Ok(f) = OpenOptions::new().create(true).write(true).truncate(true).open(&logp) {
        if let Ok(f2) = f.try_clone() {
            cmd.stdout(Stdio::from(f2));
            cmd.stderr(Stdio::from(f));
        }
    }
    match cmd.spawn() {
        Ok(child) => {
            log_to_file("SUPERVISOR", &format!("superviseur Python lancé (pid {})", child.id()));
            std::mem::forget(child);
            true
        }
        Err(e) => {
            log_to_file("SUPERVISOR", &format!("échec lancement superviseur Python: {}", e));
            false
        }
    }
}

// ── Profils de fenêtres (session) ────────────────────────────────────

#[derive(Serialize, Clone)]
struct WindowSpec {
    id: String,
    title: String,
    width: f64,
    height: f64,
    x: Option<f64>,
    y: Option<f64>,
}

static WINDOWS: OnceLock<Mutex<Vec<WindowSpec>>> = OnceLock::new();

fn windows_reg() -> &'static Mutex<Vec<WindowSpec>> {
    WINDOWS.get_or_init(|| Mutex::new(Vec::new()))
}

/// Ajoute une fenêtre à créer (appelé avant l'init Tauri).
fn add_window(spec: WindowSpec) {
    windows_reg().lock().unwrap().push(spec);
}

/// Charge les fenêtres d'une session YAML (format simple : liste de fenêtres).
fn load_session_windows() {
    // Session par défaut : 2 fenêtres (main + ide). En production : lecture
    // du .session.yaml actif via le daemon. MVP : fenêtres codées en dur.
    add_window(WindowSpec { id: "main".into(), title: "Principale".into(), width: 1200.0, height: 800.0, x: Some(40.0), y: Some(40.0) });
    add_window(WindowSpec { id: "ide".into(), title: "IDE".into(), width: 1000.0, height: 700.0, x: Some(500.0), y: Some(60.0) });
    log_to_file("SESSION", "session par défaut : main + ide");
}

// ── Commandes Tauri ──────────────────────────────────────────────────

#[tauri::command]
fn window_label(window: tauri::WebviewWindow) -> String {
    window.label().to_string()
}

/// Retourne la config daemon (token + port) pour le frontend.
#[tauri::command]
fn daemon_config() -> serde_json::Value {
    let home = mw_home();
    let token = std::fs::read_to_string(home.join("api.token")).unwrap_or_default();
    let port = std::fs::read_to_string(home.join("api.port"))
        .unwrap_or_else(|_| "8770".to_string());
    serde_json::json!({"token": token.trim(), "port": port.trim().parse::<u16>().unwrap_or(8770)})
}

/// Crée une fenêtre Tauri dynamique (menu Fenêtre → Nouvelle fenêtre / template).
/// Paramètres optionnels : size {width,height}, pos {x,y}, title.
#[tauri::command]
fn create_window(app: tauri::AppHandle, label: String, size: Option<serde_json::Value>, pos: Option<serde_json::Value>, title: Option<String>) -> Result<serde_json::Value, String> {
    log_to_file("WINDOW", &format!("create_window({})", label));
    if let Some(win) = app.get_webview_window(&label) {
        let _ = win.show();
        let _ = win.set_focus();
        return Ok(serde_json::json!({"status": "ok", "label": label, "existing": true}));
    }
    let def_w = size.as_ref().and_then(|s| s.get("width").and_then(|v| v.as_f64())).unwrap_or(1000.0);
    let def_h = size.as_ref().and_then(|s| s.get("height").and_then(|v| v.as_f64())).unwrap_or(700.0);
    let pos_x = pos.as_ref().and_then(|p| p.get("x").and_then(|v| v.as_f64()));
    let pos_y = pos.as_ref().and_then(|p| p.get("y").and_then(|v| v.as_f64()));
    let win_title = title.unwrap_or_else(|| format!("ModelWeaver — {}", label));
    let mut builder = tauri::WebviewWindowBuilder::new(&app, &label, tauri::WebviewUrl::App("index.html".into()))
        .title(&win_title)
        .inner_size(def_w, def_h)
        .min_inner_size(600.0, 400.0);
    if let (Some(x), Some(y)) = (pos_x, pos_y) {
        builder = builder.position(x, y);
    }
    let win = builder.build().map_err(|e| format!("create_window échec: {}", e))?;
    let script = format!("window.__MW_WINDOW_LABEL = '{}';", label.replace('\'', ""));
    let _ = win.eval(&script);
    log_to_file("WINDOW", &format!("create_window OK {}", label));
    Ok(serde_json::json!({"status": "ok", "label": label, "title": win_title}))
}

/// Ferme une fenêtre précise par son label.
#[tauri::command]
fn close_window(app: tauri::AppHandle, label: String) -> Result<serde_json::Value, String> {
    log_to_file("WINDOW", &format!("close_window({})", label));
    if let Some(win) = app.get_webview_window(&label) {
        win.close().map_err(|e| format!("close échec: {}", e))?;
        return Ok(serde_json::json!({"status": "ok", "label": label, "closed": true}));
    }
    Ok(serde_json::json!({"status": "ok", "label": label, "closed": false, "exists": false}))
}

/// Ferme la fenêtre COURANTE (action de menu "Quitter").
#[tauri::command]
fn close_current_window(window: tauri::WebviewWindow) -> Result<serde_json::Value, String> {
    log_to_file("WINDOW", &format!("close_current_window({})", window.label()));
    let label = window.label().to_string();
    window.close().map_err(|e| format!("close échec: {}", e))?;
    Ok(serde_json::json!({"status": "ok", "label": label, "closed": true}))
}

/// Met une fenêtre précise au premier plan (menu Fenêtre → liste → clic).
#[tauri::command]
fn focus_window(app: tauri::AppHandle, label: String) -> Result<serde_json::Value, String> {
    log_to_file("WINDOW", &format!("focus_window({})", label));
    if let Some(win) = app.get_webview_window(&label) {
        let _ = win.show();
        let _ = win.set_focus();
        return Ok(serde_json::json!({"status": "ok", "label": label, "focused": true}));
    }
    Ok(serde_json::json!({"status": "ok", "label": label, "focused": false}))
}

/// Liste les fenêtres Tauri réellement ouvertes (labels + titres).
#[tauri::command]
fn list_windows(app: tauri::AppHandle) -> Result<serde_json::Value, String> {
    let wins: Vec<serde_json::Value> = app
        .webview_windows()
        .iter()
        .map(|(label, w)| {
            serde_json::json!({"label": label, "title": w.title().unwrap_or_default()})
        })
        .collect();
    Ok(serde_json::json!({"windows": wins, "count": wins.len()}))
}

/// Position/taille/état de la fenêtre courante (pour persistance).
#[tauri::command]
fn window_state(window: tauri::WebviewWindow) -> Result<serde_json::Value, String> {
    let pos = window.outer_position().ok().map(|p| (p.x as f64, p.y as f64));
    let size = window.inner_size().ok().map(|s| (s.width as f64, s.height as f64));
    Ok(serde_json::json!({
        "x": pos.map(|p| p.0),
        "y": pos.map(|p| p.1),
        "width": size.map(|s| s.0),
        "height": size.map(|s| s.1),
        "fullscreen": window.is_fullscreen().unwrap_or(false),
        "maximized": window.is_maximized().unwrap_or(false),
    }))
}

/// Change le niveau de plein écran de la fenêtre courante.
#[tauri::command]
fn window_fullscreen(window: tauri::WebviewWindow) -> Result<serde_json::Value, String> {
    let full = window.is_fullscreen().unwrap_or(false);
    let next = !full;
    log_to_file("WINDOW", &format!("window_fullscreen({}) -> {}", window.label(), next));
    window.set_fullscreen(next).map_err(|e| format!("fullscreen échec: {}", e))?;
    Ok(serde_json::json!({"status": "ok", "fullscreen": next}))
}

/// Plein écran SESSION : applique (fullscreen=true) ou sort (false) le plein
/// écran sur TOUTES les fenêtres. F11 sort les deux (fenêtre + session).
#[tauri::command]
fn fullscreen_session(app: tauri::AppHandle, fullscreen: bool) -> Result<serde_json::Value, String> {
    let mut count = 0usize;
    for (_, win) in app.webview_windows() {
        let _ = win.set_fullscreen(fullscreen);
        count += 1;
    }
    log_to_file("WINDOW", &format!("fullscreen_session({}) sur {} fenêtres", fullscreen, count));
    Ok(serde_json::json!({"status": "ok", "fullscreen": fullscreen, "windows": count}))
}

fn main() {
    let _ = std::fs::create_dir_all(mw_home().join("logs"));
    log_to_file("INIT", &format!("ModelWeaver v2 starting, home={}", mw_home().display()));
    log_to_file("INIT", &format!("OS={}, ARCH={}", std::env::consts::OS, std::env::consts::ARCH));

    // Délégation au superviseur Python (source de vérité des services).
    let _delegated = delegate_to_python_supervisor();

    // Session par défaut.
    load_session_windows();

    tauri::Builder::default()
        .setup(|app| {
            // Crée les fenêtres de la session + injecte le label.
            let specs = windows_reg().lock().unwrap().clone();
            for spec in specs {
                let mut builder = tauri::WebviewWindowBuilder::new(app, &spec.id, tauri::WebviewUrl::App("index.html".into()))
                    .title(&spec.title)
                    .inner_size(spec.width, spec.height)
                    .min_inner_size(600.0, 400.0);
                if let (Some(x), Some(y)) = (spec.x, spec.y) {
                    builder = builder.position(x, y);
                }
                match builder.build() {
                    Ok(win) => {
                        let label = win.label().to_string();
                        let script = format!("window.__MW_WINDOW_LABEL = '{}';", label.replace('\'', ""));
                        let _ = win.eval(&script);
                        log_to_file("WINDOW", &format!("fenêtre créée: {}", label));
                    }
                    Err(e) => log_to_file("WINDOW", &format!("échec fenêtre {}: {}", spec.id, e)),
                }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            window_label,
            daemon_config,
            create_window,
            close_window,
            close_current_window,
            focus_window,
            list_windows,
            window_state,
            window_fullscreen,
            fullscreen_session,
        ])
        .run(tauri::generate_context!())
        .expect("error while running modelweaver v2");
}
