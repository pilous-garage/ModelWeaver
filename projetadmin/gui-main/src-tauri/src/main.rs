#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::process::Command;
use std::path::PathBuf;
use std::sync::{Arc, Mutex, atomic::{AtomicU64, Ordering}};
use std::collections::VecDeque;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use std::fs::OpenOptions;
use std::io::Write;
use serde::{Serialize, Deserialize};
use tauri::Manager;
use regex::Regex;

#[derive(Serialize, Deserialize, Clone)]
struct PythonResponse {
    status: String,
    data: serde_json::Value,
    error: Option<String>,
}

#[derive(Serialize, Clone)]
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

#[derive(Serialize, Clone)]
struct InstallJob {
    id: u64,
    name: String,
    job_type: String,
    status: String,
    log: String,
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

fn log_path() -> PathBuf {
    let home = get_home_dir();
    let dir = home.join(".modelweaver");
    let _ = std::fs::create_dir_all(&dir);
    dir.join("gui.log")
}

fn log_to_file(level: &str, msg: &str) {
    let path = log_path();
    let ts = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(f, "[{}] [{}] {}", ts, level, msg);
    }
}

fn log_cmd(name: &str) {
    log_to_file("CMD", name);
}

fn python_bin() -> &'static str {
    if std::env::consts::OS == "windows" { "python" } else { "python3" }
}

fn find_helper_path() -> PathBuf {
    let home = get_home_dir();
    let production = home.join(".modelweaver").join("gui_helper.py");
    if production.exists() {
        return production;
    }
    if let Ok(exe) = std::env::current_exe() {
        let p = exe.parent().and_then(|p| p.parent())
            .and_then(|p| p.parent())
            .and_then(|p| p.parent());
        if let Some(ref path) = p {
            let guess = path.join("gui_helper.py");
            if guess.exists() {
                return guess;
            }
        }
    }
    production
}

fn run_python_helper(helper_path: &PathBuf, args: &[&str]) -> Result<serde_json::Value, String> {
    let cmd_str = format!("python3 {} {}", helper_path.display(), args.join(" "));
    log_to_file("PYTHON", &cmd_str);
    let output = Command::new(python_bin())
        .arg(helper_path)
        .args(args)
        .output()
        .map_err(|e| { log_to_file("ERROR", &format!("python helper error: {}", e)); format!("Erreur exécution helper: {}", e) })?;
    if output.status.success() {
        let stdout = String::from_utf8_lossy(&output.stdout);
        serde_json::from_str(&stdout)
            .map_err(|e| format!("Erreur parse JSON: {}", e))
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr);
        log_to_file("ERROR", &format!("python helper stderr: {}", stderr));
        Err(stderr.to_string())
    }
}

struct InstallManager {
    queue: Mutex<VecDeque<InstallJob>>,
    next_id: AtomicU64,
    helper_path: PathBuf,
}

impl InstallManager {
    fn new(helper_path: PathBuf) -> Arc<Self> {
        Arc::new(Self {
            queue: Mutex::new(VecDeque::new()),
            next_id: AtomicU64::new(1),
            helper_path,
        })
    }

    fn add_job(&self, name: String, job_type: String) -> u64 {
        let id = self.next_id.fetch_add(1, Ordering::SeqCst);
        log_to_file("QUEUE", &format!("add_job #{} {} ({})", id, name, job_type));
        let mut q = self.queue.lock().unwrap();
        q.push_back(InstallJob {
            id,
            name,
            job_type,
            status: "queued".to_string(),
            log: String::new(),
        });
        id
    }

    fn get_status(&self) -> Vec<InstallJob> {
        let q = self.queue.lock().unwrap();
        q.iter().cloned().collect()
    }

    fn clear_completed(&self) {
        log_to_file("QUEUE", "clear_completed");
        let mut q = self.queue.lock().unwrap();
        q.retain(|j| j.status == "queued" || j.status == "running");
    }

    fn spawn_worker(self: &Arc<Self>) {
        let m = self.clone();
        log_to_file("WORKER", "background worker started");
        std::thread::spawn(move || loop {
            std::thread::sleep(Duration::from_millis(500));
            let job_id = {
                let mut q = m.queue.lock().unwrap();
                if let Some(job) = q.iter_mut().find(|j| j.status == "queued") {
                    job.status = "running".to_string();
                    Some((job.id, job.name.clone(), job.job_type.clone()))
                } else {
                    None
                }
            };
            if let Some((job_id, name, job_type)) = job_id {
                log_to_file("WORKER", &format!("processing job #{} {} ({})", job_id, name, job_type));
                let output = Command::new("python3")
                    .args([m.helper_path.to_str().unwrap_or(""), "install_pip", &name])
                    .output();
                let mut q = m.queue.lock().unwrap();
                if let Some(job) = q.iter_mut().find(|j| j.id == job_id) {
                    match output {
                        Ok(out) if out.status.success() => {
                            job.status = "completed".to_string();
                            job.log = String::from_utf8_lossy(&out.stdout).to_string();
                            log_to_file("WORKER", &format!("job #{} completed", job_id));
                        }
                        Ok(out) => {
                            job.status = "failed".to_string();
                            job.log = String::from_utf8_lossy(&out.stderr).to_string();
                            log_to_file("WORKER", &format!("job #{} failed: {}", job_id, job.log));
                        }
                        Err(e) => {
                            job.status = "failed".to_string();
                            job.log = format!("Process error: {}", e);
                            log_to_file("WORKER", &format!("job #{} error: {}", job_id, e));
                        }
                    }
                }
            }
        });
    }
}

#[tauri::command]
fn log_message(level: String, message: String) -> Result<(), String> {
    log_to_file(&level, &message);
    Ok(())
}

#[tauri::command]
fn get_system_info() -> SystemInfo {
    log_cmd("get_system_info");
    SystemInfo {
        os: std::env::consts::OS.to_string(),
        arch: std::env::consts::ARCH.to_string(),
        home: get_home_dir().to_string_lossy().to_string(),
    }
}

#[tauri::command]
fn check_dependencies() -> Result<Vec<DependencyStatus>, String> {
    log_cmd("check_dependencies");
    let mut deps = Vec::new();
    let py_out = Command::new(python_bin()).arg("--version").output();
    let py_ok = py_out.is_ok();
    deps.push(DependencyStatus {
        name: "python".to_string(),
        installed: py_ok,
        version: py_out.ok().and_then(|o| {
            String::from_utf8(o.stdout).ok().map(|s| s.trim().to_string())
        }),
        min_version: Some("3.10".to_string()),
    });
    let sql_out = Command::new("sqlite3").arg("--version").output();
    let sql_ok = sql_out.is_ok();
    deps.push(DependencyStatus {
        name: "sqlite3".to_string(),
        installed: sql_ok,
        version: sql_out.ok().and_then(|o| {
            String::from_utf8_lossy(&o.stdout).split_whitespace().next().map(|v| v.to_string())
        }),
        min_version: Some("3.30".to_string()),
    });
    log_to_file("INFO", &format!("check_dependencies: python={} sqlite={}", py_ok, sql_ok));
    Ok(deps)
}

#[tauri::command]
fn install_dependency(name: String) -> Result<String, String> {
    log_cmd(&format!("install_dependency({})", name));
    let cmd = match name.as_str() {
        "python" | "python3" => {
            "which apt && apt install -y python3 python3-pip 2>&1 || which brew && brew install python3 2>&1 || echo 'Non supporté'"
        }
        "sqlite3" | "sqlite" => {
            "which apt && apt install -y sqlite3 2>&1 || which brew && brew install sqlite3 2>&1 || echo 'Non supporté'"
        }
        _ => return Err(format!("Dépendance inconnue: {}", name)),
    };
    log_to_file("INSTALL", &format!("running: {}", cmd));
    let out = Command::new("bash")
        .args(["-c", cmd])
        .output()
        .map_err(|e| { log_to_file("ERROR", &format!("install error: {}", e)); format!("Erreur: {}", e) })?;
    let _stdout = String::from_utf8_lossy(&out.stdout);
    let stderr = String::from_utf8_lossy(&out.stderr);
    if out.status.success() {
        log_to_file("INSTALL", &format!("{} installed OK", name));
        Ok(format!("{} installé", name))
    } else {
        log_to_file("ERROR", &format!("{} install failed: {}", name, stderr));
        Err(stderr.to_string())
    }
}

#[tauri::command]
fn run_python_script(script_path: String, args: Vec<String>) -> Result<PythonResponse, String> {
    log_cmd(&format!("run_python_script({})", script_path));
    let home = get_home_dir();
    let root = home.join(".modelweaver");
    let full_path = root.join(&script_path);
    let output = Command::new(python_bin())
        .arg(&full_path)
        .args(&args)
        .current_dir(&root)
        .output()
        .map_err(|e| { log_to_file("ERROR", &format!("python script error: {}", e)); format!("Erreur exécution Python: {}", e) })?;
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
        log_to_file("ERROR", &format!("python script stderr: {}", stderr));
        Err(stderr.to_string())
    }
}

#[tauri::command]
fn check_databases(state: tauri::State<'_, Arc<InstallManager>>) -> Result<serde_json::Value, String> {
    log_cmd("check_databases");
    run_python_helper(&state.helper_path, &["check_databases"])
}

#[tauri::command]
fn init_databases(state: tauri::State<'_, Arc<InstallManager>>) -> Result<serde_json::Value, String> {
    log_cmd("init_databases");
    run_python_helper(&state.helper_path, &["init_databases"])
}

#[tauri::command]
fn check_python_deps(state: tauri::State<'_, Arc<InstallManager>>) -> Result<serde_json::Value, String> {
    log_cmd("check_python_deps");
    run_python_helper(&state.helper_path, &["check_python_deps"])
}

#[tauri::command]
fn get_system_state(state: tauri::State<'_, Arc<InstallManager>>) -> Result<serde_json::Value, String> {
    log_cmd("get_system_state");
    run_python_helper(&state.helper_path, &["get_system_state"])
}

#[tauri::command]
fn seed_catalogue(state: tauri::State<'_, Arc<InstallManager>>) -> Result<serde_json::Value, String> {
    log_cmd("seed_catalogue");
    run_python_helper(&state.helper_path, &["seed_catalogue"])
}

#[tauri::command]
fn get_catalogue_tools(state: tauri::State<'_, Arc<InstallManager>>) -> Result<serde_json::Value, String> {
    log_cmd("get_catalogue_tools");
    run_python_helper(&state.helper_path, &["get_catalogue_tools"])
}

#[tauri::command]
fn get_installed_tools(state: tauri::State<'_, Arc<InstallManager>>) -> Result<serde_json::Value, String> {
    log_cmd("get_installed_tools");
    run_python_helper(&state.helper_path, &["get_installed_tools"])
}

#[tauri::command]
fn save_system_state(state: tauri::State<'_, Arc<InstallManager>>) -> Result<serde_json::Value, String> {
    log_cmd("save_system_state");
    run_python_helper(&state.helper_path, &["save_system_state"])
}

#[tauri::command]
fn sync_catalogue(state: tauri::State<'_, Arc<InstallManager>>, url: String) -> Result<serde_json::Value, String> {
    log_cmd(&format!("sync_catalogue({})", url));
    run_python_helper(&state.helper_path, &["sync_catalogue_remote", &url])
}

#[tauri::command]
fn install_tool(state: tauri::State<'_, Arc<InstallManager>>, ref_: String) -> Result<serde_json::Value, String> {
    log_cmd(&format!("install_tool({})", ref_));
    run_python_helper(&state.helper_path, &["install_tool", &ref_])
}

#[tauri::command]
fn uninstall_tool(state: tauri::State<'_, Arc<InstallManager>>, ref_: String) -> Result<serde_json::Value, String> {
    log_cmd(&format!("uninstall_tool({})", ref_));
    run_python_helper(&state.helper_path, &["uninstall_tool", &ref_])
}

#[tauri::command]
fn install_queue_add(state: tauri::State<'_, Arc<InstallManager>>, name: String, job_type: String) -> Result<u64, String> {
    log_cmd(&format!("install_queue_add({}, {})", name, job_type));
    Ok(state.add_job(name, job_type))
}

#[tauri::command]
fn install_queue_status(state: tauri::State<'_, Arc<InstallManager>>) -> Result<Vec<InstallJob>, String> {
    Ok(state.get_status())
}

#[tauri::command]
fn install_queue_clear(state: tauri::State<'_, Arc<InstallManager>>) -> Result<(), String> {
    log_cmd("install_queue_clear");
    state.clear_completed();
    Ok(())
}

#[tauri::command]
fn run_command(command: String, args: Vec<String>) -> Result<CommandOutput, String> {
    let full_cmd = format!("{} {}", command, args.join(" "));
    log_cmd(&format!("run_command({})", full_cmd));
    let output = Command::new(&command)
        .args(&args)
        .output()
        .map_err(|e| {
            log_to_file("ERROR", &format!("run_command failed: {} - {}", full_cmd, e));
            format!("Failed to run command: {}", e)
        })?;
    
    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    let success = output.status.success();
    
    log_to_file("CMD_RESULT", &format!("command: {} | success: {} | stdout_len: {} | stderr_len: {}", full_cmd, success, stdout.len(), stderr.len()));
    if !stdout.is_empty() {
        log_to_file("CMD_STDOUT", &format!("{}: {}", full_cmd, stdout.trim()));
    }
    if !stderr.is_empty() {
        log_to_file("CMD_STDERR", &format!("{}: {}", full_cmd, stderr.trim()));
    }
    
    Ok(CommandOutput {
        stdout,
        stderr,
        success,
    })
}

#[derive(Serialize)]
struct CommandOutput {
    stdout: String,
    stderr: String,
    success: bool,
}

#[tauri::command]
fn get_platform() -> String {
    log_cmd("get_platform");
    std::env::consts::OS.to_string()
}

#[tauri::command]
fn read_debug_logs() -> Result<String, String> {
    log_cmd("read_debug_logs");
    let path = log_path();
    std::fs::read_to_string(path)
        .map_err(|e| format!("Failed to read logs: {}", e))
}

#[tauri::command]
fn close_splashscreen() {
    std::process::exit(0);
}

fn copy_dir_recursive(src: &PathBuf, dst: &PathBuf) -> std::io::Result<()> {
    std::fs::create_dir_all(dst)?;
    for entry in std::fs::read_dir(src)? {
        let entry = entry?;
        let path = entry.path();
        let target = dst.join(entry.file_name());
        if path.is_dir() {
            copy_dir_recursive(&path, &target)?;
        } else {
            let _ = std::fs::copy(&path, &target);
        }
    }
    Ok(())
}

/// Copie gui_helper.py + projetclient depuis le dossier de ressources Tauri vers
/// ~/.modelweaver afin que le helper Python puisse importer sql.db / modules.* .
fn ensure_bundled_resources(app: &tauri::App) {
    if let Ok(res_dir) = app.path().resource_dir() {
        let home = get_home_dir();
        let dest = home.join(".modelweaver");
        let _ = std::fs::create_dir_all(&dest);

        let src_helper = res_dir.join("gui_helper.py");
        if src_helper.exists() {
            let _ = std::fs::copy(&src_helper, dest.join("gui_helper.py"));
        }

        let src_pc = res_dir.join("projetclient");
        let dst_pc = dest.join("projetclient");
        if src_pc.exists() && !dst_pc.exists() {
            let _ = copy_dir_recursive(&src_pc, &dst_pc);
            log_to_file("INIT", &format!("copied projetclient from {}", res_dir.display()));
        } else if src_pc.exists() {
            log_to_file("INIT", "projetclient already present, skip copy");
        }
    } else {
        log_to_file("INIT", "resource_dir unavailable, skip bundled resources");
    }
}

#[tauri::command]
async fn check_dependencies_with_config(config: serde_json::Value) -> Result<serde_json::Value, String> {
    log_cmd("check_dependencies_with_config");
    let mut results = serde_json::Map::new();
    
    // Check required dependencies
    if let Some(required) = config.get("required").and_then(|r| r.as_array()) {
        for dep in required {
            let name = dep.get("name").and_then(|n| n.as_str()).unwrap_or("unknown");
            let check_command = dep.get("check_command").and_then(|c| c.as_str()).unwrap_or("");
            let version_regex = dep.get("version_regex").and_then(|v| v.as_str()).unwrap_or("");
            
            log_to_file("DEBUG", &format!("Checking dependency: {}", name));
            log_to_file("DEBUG", &format!("  Command: {}", check_command));
            
            let result = match run_command(
                check_command.split(' ').next().unwrap_or("").to_string(),
                check_command.split(' ').skip(1).map(String::from).collect(),
            ) {
                Ok(res) => res,
                Err(e) => {
                    log_to_file("DEBUG", &format!("  Command failed: {}", e));
                    CommandOutput {
                        stdout: String::new(),
                        stderr: e.to_string(),
                        success: false,
                    }
                }
            };
            
            log_to_file("DEBUG", &format!("  Success: {}", result.success));
            log_to_file("DEBUG", &format!("  Stdout: {}", result.stdout));
            log_to_file("DEBUG", &format!("  Stderr: {}", result.stderr));
            
            let mut dep_result = serde_json::Map::new();
            dep_result.insert("installed".to_string(), serde_json::Value::Bool(result.success));
            
            if result.success {
                if let Ok(re) = regex::Regex::new(version_regex) {
                    log_to_file("DEBUG", &format!("  Regex: {}", version_regex));
                    if let Some(version_match) = re.captures(&result.stdout) {
                        if let Some(version) = version_match.get(1) {
                            log_to_file("DEBUG", &format!("  Version detected: {}", version.as_str()));
                            dep_result.insert("version".to_string(), serde_json::Value::String(version.as_str().to_string()));
                        } else {
                            log_to_file("DEBUG", "  No version match found")
                        }
                    } else {
                        log_to_file("DEBUG", "  Regex did not match stdout")
                    }
                } else {
                    log_to_file("DEBUG", "  Invalid regex pattern")
                }
            } else {
                dep_result.insert("error".to_string(), serde_json::Value::String(result.stderr));
            }
            
            results.insert(name.to_string(), serde_json::Value::Object(dep_result));
        }
    }
    
    // Check package managers
    if let Some(pms) = config.get("package_managers").and_then(|p| p.as_object()) {
        let mut pm_results = serde_json::Map::new();
        for (pm, pm_config) in pms {
            let check_command = pm_config.get("check_command").and_then(|c| c.as_str()).unwrap_or("");
            let result = run_command(
                check_command.split(' ').next().unwrap_or("").to_string(),
                check_command.split(' ').skip(1).map(String::from).collect(),
            )?;
            
            let mut pm_result = serde_json::Map::new();
            pm_result.insert("available".to_string(), serde_json::Value::Bool(result.success));
            pm_result.insert("description".to_string(), pm_config.get("description").unwrap_or(&serde_json::Value::Null).clone());
            pm_results.insert(pm.clone(), serde_json::Value::Object(pm_result));
        }
        results.insert("package_managers".to_string(), serde_json::Value::Object(pm_results));
    }
    
    // Check Python package managers (only if Python is installed)
    if results.get("python3").and_then(|p| p.get("installed")).and_then(|i| i.as_bool()) == Some(true) {
        if let Some(python_pms) = config.get("python_package_managers").and_then(|p| p.as_object()) {
            let mut python_pm_results = serde_json::Map::new();
            for (pm, pm_config) in python_pms {
                let check_command = pm_config.get("check_command").and_then(|c| c.as_str()).unwrap_or("");
            let result = match run_command(
                check_command.split(' ').next().unwrap_or("").to_string(),
                check_command.split(' ').skip(1).map(String::from).collect(),
            ) {
                Ok(res) => res,
                Err(_) => CommandOutput {
                    stdout: String::new(),
                    stderr: String::new(),
                    success: false,
                },
            };
                
                let mut pm_result = serde_json::Map::new();
                pm_result.insert("available".to_string(), serde_json::Value::Bool(result.success));
                pm_result.insert("description".to_string(), pm_config.get("description").unwrap_or(&serde_json::Value::Null).clone());
                if result.success {
                    pm_result.insert("version".to_string(), serde_json::Value::String(result.stdout));
                }
                python_pm_results.insert(pm.clone(), serde_json::Value::Object(pm_result));
            }
            results.insert("python_package_managers".to_string(), serde_json::Value::Object(python_pm_results));
        }
    }
    
    Ok(serde_json::Value::Object(results))
}

fn main() {
    let helper_path = find_helper_path();
    log_to_file("INIT", &format!("ModelWeaver main starting, helper={}", helper_path.display()));
    log_to_file("INIT", &format!("OS={}, ARCH={}", std::env::consts::OS, std::env::consts::ARCH));

    let manager = InstallManager::new(helper_path);
    manager.spawn_worker();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            ensure_bundled_resources(app);
            Ok(())
        })
        .manage(manager)
        .invoke_handler(tauri::generate_handler![
            log_message,
            get_system_info,
            check_dependencies,
            install_dependency,
            run_python_script,
            check_databases,
            init_databases,
            check_python_deps,
            get_system_state,
            seed_catalogue,
            get_catalogue_tools,
            get_installed_tools,
            save_system_state,
            sync_catalogue,
            install_tool,
            uninstall_tool,
            install_queue_add,
            install_queue_status,
            install_queue_clear,
            run_command,
            get_platform,
            check_dependencies_with_config,
            read_debug_logs,
            close_splashscreen,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
