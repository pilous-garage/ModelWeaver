#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::process::{Command, Stdio};
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};
use std::collections::HashMap;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use std::fs::OpenOptions;
use std::io::{Write, Read};
use serde::{Serialize, Deserialize};
use tauri::Manager;

#[cfg(unix)]
use std::os::unix::process::CommandExt;

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
    #[serde(rename = "ref")]
    ref_: String,
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

/// Racine d'installation ModelWeaver (code app + données).
/// Résolution (partagée avec le backend Python) :
///   1. MODELWEAVER_HOME si défini
///   2. /opt/modelweaver si présent (install system-wide)
///   3. sinon ~/.modelweaver (dev / user local, sans sudo)
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
    let home = get_home_dir();
    let dir = mw_home();
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

// ============================================================
//  Process registry — global named child-process manager
//  Every client-side child process goes through this registry
//  so a human can see a call tree, statuses and on-disk logs.
// ============================================================

#[derive(Clone, Serialize)]
struct ProcInfo {
    id: u64,
    name: String,
    pid: Option<u32>,
    parent_id: Option<u64>,
    status: String,            // running | done | failed | cancelled
    command: String,
    log_path: String,
    started_at: u64,
    ended_at: Option<u64>,
    cpu: f64,                  // % CPU (per-core scaled)
    rss_kb: u64,
}

struct TrackedProcess {
    info: ProcInfo,
    child: Option<std::process::Child>,
    prev_utime: u64,
    prev_stime: u64,
    prev_ts: u64,
}

struct ProcessRegistry {
    procs: Vec<TrackedProcess>,
    next_id: u64,
}

static PROC_REG: OnceLock<Mutex<ProcessRegistry>> = OnceLock::new();

fn proc_reg() -> &'static Mutex<ProcessRegistry> {
    PROC_REG.get_or_init(|| Mutex::new(ProcessRegistry { procs: Vec::new(), next_id: 1 }))
}

fn now_secs() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs()
}

fn logs_dir() -> PathBuf {
    let dir = mw_home().join("logs");
    let _ = std::fs::create_dir_all(&dir);
    dir
}

fn safe_name(name: &str) -> String {
    name.chars()
        .map(|c| if c.is_alphanumeric() || c == '-' || c == '_' || c == ':' { c } else { '_' })
        .collect()
}

/// Register a process entry (status already set) and return its id.
fn proc_register(name: &str, parent_id: Option<u64>, pid: Option<u32>, command: &str, log_path: String, status: &str) -> u64 {
    let mut reg = proc_reg().lock().unwrap();
    let id = reg.next_id;
    reg.next_id += 1;
    reg.procs.push(TrackedProcess {
        info: ProcInfo {
            id,
            name: name.to_string(),
            pid,
            parent_id,
            status: status.to_string(),
            command: command.to_string(),
            log_path,
            started_at: now_secs(),
            ended_at: None,
            cpu: 0.0,
            rss_kb: 0,
        },
        child: None,
        prev_utime: 0,
        prev_stime: 0,
        prev_ts: now_secs(),
    });
    id
}

/// Begin a tracked process (registered as "running"); returns its id.
fn proc_begin(name: &str, parent_id: Option<u64>, command: &str) -> u64 {
    let id = { proc_reg().lock().unwrap().next_id };
    let logp = logs_dir().join(format!("proc-{}-{}.log", id, safe_name(name)));
    proc_register(name, parent_id, None, command, logp.to_string_lossy().to_string(), "running")
}

fn proc_set_pid(id: u64, pid: u32) {
    let mut reg = proc_reg().lock().unwrap();
    if let Some(p) = reg.procs.iter_mut().find(|p| p.info.id == id) {
        p.info.pid = Some(pid);
    }
}

fn proc_set_status(id: u64, status: &str) {
    let mut reg = proc_reg().lock().unwrap();
    if let Some(p) = reg.procs.iter_mut().find(|p| p.info.id == id) {
        p.info.status = status.to_string();
        if status != "running" {
            p.info.ended_at = Some(now_secs());
        }
    }
}

/// Finalize a tracked process and flush its output to the on-disk log.
fn proc_finish(id: u64, success: bool, sout: &str, serr: &str) {
    let mut reg = proc_reg().lock().unwrap();
    if let Some(p) = reg.procs.iter_mut().find(|p| p.info.id == id) {
        p.info.status = if success { "done".to_string() } else { "failed".to_string() };
        p.info.ended_at = Some(now_secs());
        p.child = None;
        let _ = OpenOptions::new().create(true).write(true).truncate(true).open(&p.info.log_path)
            .and_then(|mut f| writeln!(f, "=== STDOUT ===\n{}\n=== STDERR ===\n{}", sout, serr));
    }
}

fn proc_cancel(id: u64) {
    let mut reg = proc_reg().lock().unwrap();
    if let Some(p) = reg.procs.iter_mut().find(|p| p.info.id == id) {
        if p.info.status == "running" {
            p.info.status = "cancelled".to_string();
            p.info.ended_at = Some(now_secs());
        }
    }
}

/// Fire-and-forget tracked child: stdout+stderr go to an on-disk log.
fn track_process(name: &str, parent_id: Option<u64>, command: &str, args: &[&str]) -> u64 {
    let id = { proc_reg().lock().unwrap().next_id };
    let logp = logs_dir().join(format!("proc-{}-{}.log", id, safe_name(name)));
    let log_path = logp.to_string_lossy().to_string();
    let cmdstr = format!("{} {}", command, args.join(" "));
    let file = OpenOptions::new().create(true).write(true).truncate(true).open(&logp);
    let mut cmd = Command::new(command);
    cmd.args(args);
    #[cfg(unix)]
    cmd.process_group(0);
    if let Ok(f) = file {
        if let Ok(f2) = f.try_clone() {
            cmd.stdout(Stdio::from(f2));
            cmd.stderr(Stdio::from(f));
        }
    }
    match cmd.spawn() {
        Ok(child) => {
            let pid = child.id();
            let reg_id = proc_register(name, parent_id, Some(pid), &cmdstr, log_path, "running");
            let mut reg = proc_reg().lock().unwrap();
            if let Some(p) = reg.procs.iter_mut().find(|p| p.info.id == reg_id) {
                p.child = Some(child);
            }
            reg_id
        }
        Err(e) => {
            log_to_file("PROC", &format!("spawn error {}: {}", name, e));
            proc_register(name, parent_id, None, &cmdstr, log_path, "failed")
        }
    }
}

/// Return a snapshot of all tracked processes for the UI.
fn proc_snapshot() -> Vec<ProcInfo> {
    let reg = proc_reg().lock().unwrap();
    reg.procs.iter().map(|p| p.info.clone()).collect()
}

/// Read the last `lines` lines of a process on-disk log.
fn proc_log_tail(id: u64, lines: usize) -> String {
    let path = {
        let reg = proc_reg().lock().unwrap();
        reg.procs.iter().find(|p| p.info.id == id).map(|p| p.info.log_path.clone())
    };
    if let Some(path) = path {
        if let Ok(content) = std::fs::read_to_string(&path) {
            let all: Vec<&str> = content.lines().collect();
            let start = all.len().saturating_sub(lines);
            return all[start..].join("\n");
        }
    }
    String::new()
}

/// Read the last `lines` lines of a supervised service on-disk log
/// (logs_dir()/service-{name}.log).
fn service_log_tail(name: &str, lines: usize) -> String {
    let path = logs_dir().join(format!("service-{}.log", safe_name(name)));
    if let Ok(content) = std::fs::read_to_string(&path) {
        let all: Vec<&str> = content.lines().collect();
        let start = all.len().saturating_sub(lines);
        return all[start..].join("\n");
    }
    String::new()
}

/// 1 Hz monitor: reap finished children, sample CPU/RSS, mirror to DB.
fn start_proc_monitor(db_path: PathBuf) {
    std::thread::spawn(move || loop {
        let start = SystemTime::now();
        {
            let mut reg = proc_reg().lock().unwrap();
            for p in reg.procs.iter_mut() {
                if p.info.status != "running" { continue; }
                // reap if we own the child handle
                if let Some(child) = p.child.as_mut() {
                    match child.try_wait() {
                        Ok(Some(code)) => {
                            p.info.status = if code.success() { "done".to_string() } else { "failed".to_string() };
                            p.info.ended_at = Some(now_secs());
                            p.child = None;
                        }
                        Ok(None) => {}
                        Err(_) => { p.child = None; }
                    }
                }
                // sample resources for still-running processes (unix /proc)
                #[cfg(unix)]
                if p.info.status == "running" {
                    if let Some(pid) = p.info.pid {
                        if let Some((utime, stime, rss_pages)) = read_proc_stat(pid) {
                            let ts = now_secs();
                            let dclk = (utime.saturating_sub(p.prev_utime) + stime.saturating_sub(p.prev_stime)) as f64;
                            let dts = (ts.saturating_sub(p.prev_ts)) as f64;
                            if dts > 0.0 && p.prev_ts > 0 {
                                let clk = 100.0; // USER_HZ
                                let ncpu = num_cpus_procfs() as f64;
                                p.info.cpu = (dclk / dts) * (1000.0 / clk) / ncpu;
                            }
                            p.info.rss_kb = (rss_pages as u64) * 4; // 4 KiB pages
                            p.prev_utime = utime;
                            p.prev_stime = stime;
                            p.prev_ts = ts;
                        }
                    }
                }
            }
            // prune transient single-use processes (forget when done > 10s)
            let nowp = now_secs();
            reg.procs.retain(|p| {
                if p.info.status != "done" { return true; }
                if p.info.name == "modelweaver-main" || p.info.name.starts_with("watch:")
                    || p.info.name == "catalogue" || p.info.name == "installer"
                    || p.info.name.starts_with("install:") || p.info.name.starts_with("uninstall:") {
                    return true;
                }
                if let Some(e) = p.info.ended_at { if nowp.saturating_sub(e) <= 10 { return true; } }
                false
            });
        }
            // mirror snapshot to local DB (best-effort)
            mirror_processes_to_db(&db_path, &proc_snapshot());
        // keep the tick under 1 second
        if let Ok(elapsed) = start.elapsed() {
            let ms = elapsed.as_millis() as u64;
            if ms < 900 {
                std::thread::sleep(Duration::from_millis(900 - ms));
            }
        } else {
            std::thread::sleep(Duration::from_millis(900));
        }
    });
}

#[cfg(unix)]
fn read_proc_stat(pid: u32) -> Option<(u64, u64, u64)> {
    let content = std::fs::read_to_string(format!("/proc/{}/stat", pid)).ok()?;
    let closing = content.rfind(')')?;
    let rest = &content[closing + 1..];
    let parts: Vec<&str> = rest.split_whitespace().collect();
    // state, ppid, pgrp, session, tty, tpgid, flags, minflt, cminflt, majflt, cmajflt,
    // utime(14), stime(15), cutime, cstime, ..., rss(24)
    let utime = parts.get(12)?.parse::<u64>().ok()?;   // index 12 -> field 14
    let stime = parts.get(13)?.parse::<u64>().ok()?;   // index 13 -> field 15
    let rss = parts.get(21)?.parse::<u64>().ok()?;     // index 21 -> field 22 (rss, in pages)
    Some((utime, stime, rss))
}

#[cfg(unix)]
fn num_cpus_procfs() -> usize {
    std::fs::read_to_string("/proc/cpuinfo")
        .ok()
        .and_then(|s| s.lines().filter(|l| l.starts_with("processor")).count().into())
        .unwrap_or(1)
        .max(1)
}

#[cfg(unix)]
fn mirror_processes_to_db(db_path: &PathBuf, snap: &[ProcInfo]) {
    use std::fmt::Write as _;
    let mut sql = String::new();
    let _ = writeln!(sql, "CREATE TABLE IF NOT EXISTS processes (\n\
        id INTEGER PRIMARY KEY,\n\
        name TEXT NOT NULL,\n\
        pid INTEGER,\n\
        parent_id INTEGER,\n\
        status TEXT,\n\
        command TEXT,\n\
        log_path TEXT,\n\
        cpu REAL,\n\
        rss_kb INTEGER,\n\
        started_at INTEGER,\n\
        ended_at INTEGER,\n\
        updated_at INTEGER DEFAULT (strftime('%s','now'))\n\
    );");
    for p in snap {
        let esc = |s: &str| s.replace('\'', "''");
        let ended = p.ended_at.map(|v| v as i64).unwrap_or(-1);
        let pid = p.pid.map(|v| v as i64).unwrap_or(-1);
        let parent = p.parent_id.map(|v| v as i64).unwrap_or(-1);
        let _ = writeln!(sql, "INSERT INTO processes (id,name,pid,parent_id,status,command,log_path,cpu,rss_kb,started_at,ended_at) \
            VALUES ({},'{}',{},{},'{}','{}','{}',{},{},{},{}) \
            ON CONFLICT(id) DO UPDATE SET pid=excluded.pid,status=excluded.status,command=excluded.command,log_path=excluded.log_path,cpu=excluded.cpu,rss_kb=excluded.rss_kb,ended_at=excluded.ended_at,updated_at=strftime('%s','now');",
            p.id, esc(&p.name), pid, parent, esc(&p.status), esc(&p.command), esc(&p.log_path), p.cpu, p.rss_kb, p.started_at as i64, ended);
    }
    db_run(db_path, &sql);
}

#[cfg(not(unix))]
fn mirror_processes_to_db(_db_path: &PathBuf, _snap: &[ProcInfo]) {}

// ============================================================
//  Service manager — long-lived named services with auto-restart.
//  A "service" is a supervised child process. Loop services are
//  restarted automatically if they exit/crash (max 10 retries).
//  Each service is also registered in the process registry so it
//  shows up in the Debug tree.
// ============================================================

#[derive(Clone, Serialize)]
struct ServiceInfo {
    name: String,
    mode: String,            // loop | single-use
    command: String,
    args: Vec<String>,
    status: String,          // running | stopped | crashed | restarting
    pid: Option<u32>,
    parent: Option<String>,
    restart: bool,
    restarts: u32,
    last_exit: Option<i32>,
    started_at: u64,
    proc_id: u64,
}

struct ServiceEntry {
    info: ServiceInfo,
    child: Option<std::process::Child>,
    watch: bool,             // if true, stdout lines are cached (WATCH_CACHE)
    managed: bool,           // if true, supervisor spawns/restarts the child
}

static SERVICES: OnceLock<Mutex<Vec<ServiceEntry>>> = OnceLock::new();
static WATCH_CACHE: OnceLock<Mutex<HashMap<String, String>>> = OnceLock::new();

fn services_reg() -> &'static Mutex<Vec<ServiceEntry>> {
    SERVICES.get_or_init(|| Mutex::new(Vec::new()))
}

fn watch_cache() -> &'static Mutex<HashMap<String, String>> {
    WATCH_CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

fn define_service(name: &str, mode: &str, command: &str, args: Vec<String>, parent: Option<String>, restart: bool, watch: bool) {
    let mut reg = services_reg().lock().unwrap();
    if reg.iter().any(|s| s.info.name == name) { return; }
    let proc_id = proc_begin(name, None, &format!("{} {}", command, args.join(" ")));
    reg.push(ServiceEntry {
        info: ServiceInfo {
            name: name.to_string(),
            mode: mode.to_string(),
            command: command.to_string(),
            args,
            status: "stopped".to_string(),
            pid: None,
            parent,
            restart,
            restarts: 0,
            last_exit: None,
            started_at: now_secs(),
            proc_id,
        },
        child: None,
        watch,
        managed: true,
    });
}

/// Register a service implemented as a Rust thread (not a spawned child).
/// The supervisor only mirrors it; it does not spawn/restart it.
fn register_thread_service(name: &str) -> u64 {
    let proc_id = proc_begin(name, None, name);
    proc_set_status(proc_id, "running");
    let mut reg = services_reg().lock().unwrap();
    if reg.iter().any(|s| s.info.name == name) { return proc_id; }
    reg.push(ServiceEntry {
        info: ServiceInfo {
            name: name.to_string(),
            mode: "loop".to_string(),
            command: String::new(),
            args: vec![],
            status: "running".to_string(),
            pid: Some(std::process::id()),
            parent: None,
            restart: false,
            restarts: 0,
            last_exit: None,
            started_at: now_secs(),
            proc_id,
        },
        child: None,
        watch: false,
        managed: false,
    });
    proc_id
}

fn set_watch_cache(name: &str, value: &str) {
    watch_cache().lock().unwrap().insert(name.to_string(), value.to_string());
}

/// Service Rust léger : lit local_tools depuis la DB et met en cache le JSON.
fn watch_installed_tools_rust(interval: f64) {
    let db = mw_home().join("modelweaver.db");
    std::thread::spawn(move || loop {
        let sql = "SELECT lo.outil_ref AS tool_ref, lo.nom AS tool_name, \
            li.version_installee AS version, li.status, li.install_path, \
            c.nom AS classe, c.ref AS classe_ref \
            FROM local_outils lo \
            LEFT JOIN classes_outils c ON c.classe_id = lo.classe_outil_id \
            JOIN local_versions lv ON lv.local_outil_id = lo.local_outil_id \
            JOIN local_installs li ON li.local_version_id = lv.local_version_id;";
        let rows = db_query_json(&db, sql);
        let tools: Vec<serde_json::Value> = rows.iter().map(|r| serde_json::json!({
            "ref": r.get("tool_ref").and_then(|x| x.as_str()).unwrap_or(""),
            "name": r.get("tool_name").and_then(|x| x.as_str()).unwrap_or(""),
            "version": r.get("version").and_then(|x| x.as_str()).unwrap_or(""),
            "status": r.get("status").and_then(|x| x.as_str()).unwrap_or(""),
            "install_path": r.get("install_path").and_then(|x| x.as_str()).unwrap_or(""),
            "classe": r.get("classe").and_then(|x| x.as_str()).unwrap_or(""),
            "classe_ref": r.get("classe_ref").and_then(|x| x.as_str()).unwrap_or(""),
        })).collect();
        let out = serde_json::json!({ "tools": tools, "count": tools.len() });
        set_watch_cache("installed-tools", &out.to_string());
        std::thread::sleep(Duration::from_millis((interval * 1000.0) as u64));
    });
}

/// Service Rust (wrapper) : orchestre la collecte complexe en Python et cache le résultat.
fn watch_sys_state_rust(helper: PathBuf, interval: f64) {
    std::thread::spawn(move || loop {
        if let Ok(o) = Command::new(python_bin()).arg(&helper).arg("get_system_state")
            .env("PYTHONPATH", find_repo_root()).env("MODELWEAVER_HOME", mw_home()).output() {
            if o.status.success() {
                let s = String::from_utf8_lossy(&o.stdout);
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&s) {
                    set_watch_cache("sys-state", &v.to_string());
                }
            }
        }
        std::thread::sleep(Duration::from_millis((interval * 1000.0) as u64));
    });
}

fn spawn_service_child(entry: &mut ServiceEntry) {
    log_to_file("SUPERVISOR", &format!("spawn {}", entry.info.name));
    let mut cmd = Command::new(&entry.info.command);
    cmd.args(&entry.info.args);
    // PYTHONPATH = racine du repo pour que `import services` / `import modules` fonctionne.
    cmd.env("PYTHONPATH", mw_home());
    #[cfg(unix)]
    cmd.process_group(0);
    if entry.watch {
        cmd.stdout(Stdio::piped());
        cmd.stderr(Stdio::piped());
    } else {
        let logp = logs_dir().join(format!("service-{}.log", safe_name(&entry.info.name)));
        if let Ok(f) = OpenOptions::new().create(true).write(true).truncate(true).open(&logp) {
            if let Ok(f2) = f.try_clone() { cmd.stdout(Stdio::from(f2)); cmd.stderr(Stdio::from(f)); }
        }
    }
    match cmd.spawn() {
        Ok(child) => {
            let pid = child.id();
            entry.child = Some(child);
            entry.info.pid = Some(pid);
            entry.info.status = "running".to_string();
            entry.info.started_at = now_secs();
            proc_set_pid(entry.info.proc_id, pid);
            proc_set_status(entry.info.proc_id, "running");
            if entry.watch {
                let name = entry.info.name.clone();
                let mut out = entry.child.as_mut().unwrap().stdout.take();
                let mut err = entry.child.as_mut().unwrap().stderr.take();
                std::thread::spawn(move || {
                    use std::io::BufRead;
                    if let Some(o) = out.take() {
                        let reader = std::io::BufReader::new(o);
                        for line in reader.lines().map_while(Result::ok) {
                            watch_cache().lock().unwrap().insert(name.clone(), line);
                        }
                    }
                    let mut buf = Vec::new();
                    if let Some(mut e) = err.take() { let _ = e.read_to_end(&mut buf); }
                });
            }
        }
        Err(e) => {
            log_to_file("SERVICE", &format!("spawn error {}: {}", entry.info.name, e));
            entry.info.status = "crashed".to_string();
        }
    }
}

fn start_service_supervisor() {
    std::thread::spawn(move || loop {
        {
            let mut reg = services_reg().lock().unwrap();
            for entry in reg.iter_mut() {
                if !entry.managed { continue; }
                if entry.info.mode != "loop" { continue; }
                let exited = match entry.child.as_mut() {
                    Some(c) => match c.try_wait() {
                        Ok(Some(code)) => { entry.info.last_exit = Some(code.code().unwrap_or(-1)); true }
                        Ok(None) => false,
                        Err(_) => { entry.info.last_exit = Some(-1); true }
                    },
                    None => true,
                };
                if exited {
                    entry.child = None;
                    proc_set_status(entry.info.proc_id, "stopped");
                    // Réinitialise le compteur si le service a tourné longtemps
                    // (>60s) : on ne pénalise que les crash-loops rapides.
                    if now_secs().saturating_sub(entry.info.started_at) > 60 {
                        entry.info.restarts = 0;
                    }
                    // Single-instance : si un verrou d'instance est déjà pris
                    // (processus vivant), on ne (re)spawn pas — un seul service
                    // de ce type doit tourner.
                    if instance_lock_taken(&entry.info.name) {
                        entry.info.status = "stopped".to_string();
                        log_to_file("SUPERVISOR", &format!("skip restart {} (instance déjà en cours)", entry.info.name));
                    } else if entry.info.restart && entry.info.restarts < 10 {
                        entry.info.restarts += 1;
                        entry.info.status = "restarting".to_string();
                        log_to_file("SUPERVISOR", &format!("restart {} (attempt {})", entry.info.name, entry.info.restarts));
                        spawn_service_child(entry);
                    } else {
                        entry.info.status = "stopped".to_string();
                    }
                }
            }
        }
        mirror_services_to_db();
        std::thread::sleep(Duration::from_millis(1000));
    });
}

/// Écrit un fichier résumé clair de l'interface des services (comment chaque
/// service est ouvert/lancé) dans ~/.modelweaver/services-summary.txt.
fn write_services_summary() {
    let reg = services_reg().lock().unwrap();
    let mut out = String::new();
    out.push_str("=== ModelWeaver — Interface des services ===\n\n");
    out.push_str("Chaque service est ouvert (lancé) par le superviseur Rust.\n");
    out.push_str("Services légers = threads Rust (pas d'enfant Python).\n");
    out.push_str("Services complexes = enfants Python supervisés (auto-redémarrage).\n\n");
    for s in reg.iter() {
        out.push_str(&format!("● {}  [mode: {}]\n", s.info.name, s.info.mode));
        out.push_str(&format!("    ouvert par : {} {}\n", s.info.command, s.info.args.join(" ")));
        out.push_str(&format!("    auto-redémarrage : {} | enfant managé : {} | cache watch : {}\n",
            s.info.restart, s.managed, s.watch));
        out.push('\n');
    }
    let path = mw_home().join("services-summary.txt");
    if let Ok(mut f) = OpenOptions::new().create(true).write(true).truncate(true).open(&path) {
        let _ = f.write_all(out.as_bytes());
    }
    log_to_file("INIT", &format!("services summary written: {}", path.display()));
}

fn read_watch_cache(name: &str) -> Option<String> {
    watch_cache().lock().unwrap().get(name).cloned()
}

#[cfg(unix)]
fn mirror_services_to_db() {
    use std::fmt::Write as _;
    let reg = services_reg().lock().unwrap();
    let mut sql = String::new();
    let _ = writeln!(sql, "CREATE TABLE IF NOT EXISTS services (\n\
        name TEXT PRIMARY KEY,\n\
        mode TEXT,\n\
        command TEXT,\n\
        args TEXT,\n\
        status TEXT,\n\
        pid INTEGER,\n\
        parent TEXT,\n\
        restart INTEGER,\n\
        restarts INTEGER DEFAULT 0,\n\
        last_exit INTEGER,\n\
        started_at INTEGER,\n\
        updated_at INTEGER DEFAULT (strftime('%s','now'))\n\
    );");
    for s in reg.iter() {
        let esc = |x: &str| x.replace('\'', "''");
        let args = s.info.args.join("\u{1}").replace('\'', "''");
        let pid = s.info.pid.map(|v| v as i64).unwrap_or(-1);
        let parent = s.info.parent.clone().unwrap_or_default().replace('\'', "''");
        let _ = writeln!(sql, "INSERT INTO services (name,mode,command,args,status,pid,parent,restart,restarts,last_exit,started_at) \
            VALUES ('{}','{}','{}','{}','{}',{},'{}',{},{},{},{}) \
            ON CONFLICT(name) DO UPDATE SET mode=excluded.mode,command=excluded.command,args=excluded.args,status=excluded.status,pid=excluded.pid,parent=excluded.parent,restart=excluded.restart,restarts=excluded.restarts,last_exit=excluded.last_exit,started_at=excluded.started_at,updated_at=strftime('%s','now');",
            esc(&s.info.name), esc(&s.info.mode), esc(&s.info.command), args, esc(&s.info.status), pid, parent, if s.info.restart {1} else {0}, s.info.restarts, s.info.last_exit.unwrap_or(-1), s.info.started_at as i64);
    }
    let home = mw_home().join("runtime.db");
    db_run(&home, &sql);
}
#[cfg(not(unix))]
fn mirror_services_to_db() {}

fn find_helper_path() -> PathBuf {
    let home = get_home_dir();
    let production = mw_home().join("gui_helper.py");
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

/// Racine du dépôt : on préfère MODELWEAVER_HOME (mw_home) si `services/` y est
/// présent, sinon on remonte depuis l'exécutable jusqu'à trouver `services/`,
/// repli sur le parent de gui_helper, puis le dossier courant.
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
    if let Ok(exe) = std::env::current_exe() {
        if let Some(p) = exe.parent().and_then(|p| p.parent()).and_then(|p| p.parent()).and_then(|p| p.parent()) {
            if p.join("services").is_dir() {
                return p.to_path_buf();
            }
        }
    }
    PathBuf::from(".")
}

/// Chemin de l'entrypoint d'un service : <repo>/services/<name>/service.py
fn service_entry(repo: &PathBuf, name: &str) -> PathBuf {
    repo.join("services").join(name).join("service.py")
}

/// Chemin du verrou d'instance unique : ~/.modelweaver/run/<name>.pid
fn lock_path(name: &str) -> PathBuf {
    mw_home().join("run").join(format!("{}.pid", name))
}

/// Vrai si un lock valide (PID vivant) existe déjà pour `name`.
fn instance_lock_taken(name: &str) -> bool {
    let p = lock_path(name);
    if let Ok(s) = std::fs::read_to_string(&p) {
        if let Ok(pid) = s.trim().parse::<i32>() {
            if pid > 0 && process_alive(pid) {
                return true;
            }
        }
    }
    false
}

/// Acquiert le verrou d'instance unique pour `name`. Retourne false si déjà pris.
/// Nettoie un éventuel lock périmé (PID mort).
fn acquire_instance_lock(name: &str) -> bool {
    let p = lock_path(name);
    if let Some(parent) = p.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if instance_lock_taken(name) {
        return false;
    }
    if p.exists() {
        let _ = std::fs::remove_file(&p);
    }
    if let Ok(mut f) = std::fs::File::create(&p) {
        let _ = f.write_all(std::process::id().to_string().as_bytes());
        return true;
    }
    false
}

/// Quitte le processus si un autre superviseur tourne déjà (single-instance).
fn ensure_single_supervisor() {
    if instance_lock_taken("supervisor") {
        eprintln!("ModelWeaver: un superviseur tourne déjà (supervisor.lock). Arrêt.");
        std::process::exit(2);
    }
    if !acquire_instance_lock("supervisor") {
        eprintln!("ModelWeaver: impossible d'acquérir supervisor.lock. Arrêt.");
        std::process::exit(2);
    }
}

/// Vrai si le PID existe (kill -0, portable Unix).
fn process_alive(pid: i32) -> bool {
    #[cfg(unix)]
    {
        Command::new("kill").arg("-0").arg(pid.to_string()).status().map(|s| s.success()).unwrap_or(false)
    }
    #[cfg(not(unix))]
    {
        let _ = pid;
        true
    }
}

fn run_python_helper(helper_path: &PathBuf, args: &[&str]) -> Result<serde_json::Value, String> {
    let name = args.first().copied().unwrap_or("python");
    let cmd_str = format!("python3 {} {}", helper_path.display(), args.join(" "));
    log_to_file("PYTHON", &cmd_str);
    let logp = {
        let id = { proc_reg().lock().unwrap().next_id };
        logs_dir().join(format!("proc-{}-{}.log", id, safe_name(name)))
    };
    let log_path = logp.to_string_lossy().to_string();
    let repo_root = find_repo_root();
    log_to_file("PYTHON", &format!("helper={} repo_root={} mw_home={}", helper_path.display(), repo_root.display(), mw_home().display()));
    let output = Command::new(python_bin())
        .arg(helper_path)
        .args(args)
        .env("PYTHONPATH", &repo_root)
        .env("MODELWEAVER_HOME", mw_home())
        .output()
        .map_err(|e| { log_to_file("ERROR", &format!("python helper error: {}", e)); format!("Erreur exécution helper: {}", e) })?;
    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    if let Ok(mut f) = OpenOptions::new().create(true).write(true).truncate(true).open(&logp) {
        let _ = writeln!(f, "=== STDOUT ===\n{}\n=== STDERR ===\n{}", stdout, stderr);
    }
    let status = if output.status.success() { "done" } else { "failed" };
    proc_register(name, None, None, &cmd_str, log_path, status);
    if output.status.success() {
        match serde_json::from_str::<serde_json::Value>(&stdout) {
            Ok(v) => Ok(v),
            Err(e) => {
                log_to_file("ERROR", &format!("helper '{}' stdout vide/invalide: {} | stdout=[{}] stderr=[{}]", name, e, stdout, stderr));
                // Ne pas faire échouer la GUI: renvoyer un objet sûr.
                Ok(serde_json::json!({"warning": format!("helper {} sans sortie JSON: {}", name, e), "tools": [], "count": 0}))
            }
        }
    } else {
        log_to_file("ERROR", &format!("python helper stderr: {}", stderr));
        Err(stderr)
    }
}

fn sql_esc(s: &str) -> String { s.replace('\'', "''") }

fn db_run(db: &PathBuf, sql: &str) {
    let _ = Command::new("sqlite3")
        .arg(db)
        .arg("-cmd").arg(".timeout 5000")
        .arg(sql)
        .status();
}

fn db_query_json(db: &PathBuf, sql: &str) -> Vec<serde_json::Value> {
    match Command::new("sqlite3")
        .arg("-json")
        .arg(db)
        .arg("-cmd").arg(".timeout 5000")
        .arg(sql)
        .output() {
        Ok(o) if o.status.success() => serde_json::from_slice(&o.stdout).unwrap_or_default(),
        _ => vec![],
    }
}

fn db_scalar_u64(db: &PathBuf, sql: &str) -> u64 {
    if let Some(obj) = db_query_json(db, sql).first().and_then(|v| v.as_object()) {
        for (_, v) in obj.iter() {
            if let Some(n) = v.as_u64() { return n; }
        }
    }
    0
}

fn ensure_install_jobs(db: &PathBuf) {
    db_run(db, "CREATE TABLE IF NOT EXISTS install_jobs (\n\
        id INTEGER PRIMARY KEY AUTOINCREMENT,\n\
        ref TEXT NOT NULL,\n\
        name TEXT,\n\
        job_type TEXT,\n\
        status TEXT,\n\
        log TEXT,\n\
        pid INTEGER,\n\
        created_at INTEGER,\n\
        updated_at INTEGER DEFAULT (strftime('%s','now'))\n\
    );");
}

// install_jobs est consommé par le worker Python (run_installer_service).
// Les commandes UI ci-dessous opèrent directement sur la table.

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
fn daemon_post(route: &str, body: &str) -> Result<serde_json::Value, String> {
    let token = std::fs::read_to_string(mw_home().join("api.token"))
        .map_err(|_| "daemon token not found".to_string())?;
    let port = std::fs::read_to_string(mw_home().join("api.port"))
        .unwrap_or_else(|_| "8770".to_string());
    let port = port.trim();
    let url = format!("http://127.0.0.1:{}/v1/{}", port, route);
    let output = Command::new("curl")
        .args(["-s", "-X", "POST", "-H", &format!("Authorization: Bearer {}", token.trim()), "-d", body, &url])
        .output()
        .map_err(|e| format!("curl failed: {}", e))?;
    let resp = String::from_utf8_lossy(&output.stdout).to_string();
    serde_json::from_str(&resp).map_err(|e| format!("parse error: {} — body: {}", e, resp))
}

#[tauri::command]
async fn install_all_dependencies(include_optional: bool) -> Result<String, String> {
    // Installe les dépendances requises de la cible via le script compilé
    // (manifeste + install-dependencies-<target>.sh). Délégation au daemon.
    // include_optional -> installe aussi les deps heavy/unsafe (litellm, docker).
    // async + spawn_blocking : l'appel curl bloquant part sur un thread du runtime
    // async pour ne PAS geler le thread principal du webview (sinon le spinner
    // ne s'affiche qu'après la fin de l'install).
    log_cmd(&format!("install_all_dependencies(include_optional={})", include_optional));
    let body = format!("{{\"include_optional\":{}}}", include_optional);
    let join = tauri::async_runtime::spawn_blocking(move || {
        daemon_post("deps/install_target", &body)
    }).await;
    let resp = match join {
        Ok(r) => r?,
        Err(e) => return Err(format!("runtime error: {}", e)),
    };
    // Le daemon renvoie {"ok": true, "result": {...}} ; le statut réel est
    // dans result.status (result.error en cas d'échec).
    let result = resp.get("result").cloned().unwrap_or(resp.clone());
    match result.get("status").and_then(|s| s.as_str()) {
        Some("ok") => Ok("dépendances installées".to_string()),
        _ => {
            let err = result.get("error").and_then(|e| e.as_str()).unwrap_or("unknown error");
            log_to_file("ERROR", &format!("deps install_target failed: {}", err));
            Err(err.to_string())
        }
    }
}

#[tauri::command]
fn check_dependencies_manifest() -> Result<serde_json::Value, String> {
    // Liste les deps du manifeste pour la cible courante (statut installé).
    log_cmd("check_dependencies_manifest");
    daemon_post("deps/check_manifest", "{}")
}

#[tauri::command]
async fn install_dependency(name: String) -> Result<String, String> {
    log_cmd(&format!("install_dependency({})", name));
    // Délégation au daemon API (backend unique, root en container, sudo/pkexec sinon).
    // Mappe le nom de dépendance logique vers le(s) paquet(s) apt.
    // async + spawn_blocking : évite de geler le webview pendant l'install.
    let pkg = match name.as_str() {
        "python3" | "python" => "python3 python3-pip",
        "sqlite3" | "sqlite" => "sqlite3",
        "git" => "git",
        other => other,
    };
    let body = format!("{{\"package\":\"{}\"}}", pkg);
    let join = tauri::async_runtime::spawn_blocking(move || {
        daemon_post("deps/install", &body)
    }).await;
    let resp = match join {
        Ok(r) => r?,
        Err(e) => return Err(format!("runtime error: {}", e)),
    };
    let result = resp.get("result").cloned().unwrap_or(resp.clone());
    match result.get("status").and_then(|s| s.as_str()) {
        Some("ok") => {
            log_to_file("INSTALL", &format!("{} installed OK via daemon", name));
            Ok(format!("{} installé", name))
        }
        _ => {
            let err = result.get("error").and_then(|e| e.as_str()).unwrap_or("unknown error");
            log_to_file("ERROR", &format!("{} install failed: {}", name, err));
            Err(err.to_string())
        }
    }
}

#[tauri::command]
fn run_python_script(script_path: String, args: Vec<String>) -> Result<PythonResponse, String> {
    log_cmd(&format!("run_python_script({})", script_path));
    let home = get_home_dir();
    let root = mw_home();
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
fn check_databases() -> Result<serde_json::Value, String> {
    log_cmd("check_databases");
    run_python_helper(&find_helper_path(), &["check_databases"])
}

#[tauri::command]
fn init_databases() -> Result<serde_json::Value, String> {
    log_cmd("init_databases");
    run_python_helper(&find_helper_path(), &["init_databases"])
}

#[tauri::command]
fn check_python_deps() -> Result<serde_json::Value, String> {
    log_cmd("check_python_deps");
    run_python_helper(&find_helper_path(), &["check_python_deps"])
}

#[tauri::command]
fn get_system_state() -> Result<serde_json::Value, String> {
    log_cmd("get_system_state");
    run_python_helper(&find_helper_path(), &["get_system_state"])
}

#[tauri::command]
fn seed_catalogue() -> Result<serde_json::Value, String> {
    log_cmd("seed_catalogue");
    run_python_helper(&find_helper_path(), &["seed_catalogue"])
}

#[tauri::command]
fn get_catalogue_tools() -> Result<serde_json::Value, String> {
    log_cmd("get_catalogue_tools");
    run_python_helper(&find_helper_path(), &["get_catalogue_tools"])
}

#[tauri::command]
fn get_installed_tools() -> Result<serde_json::Value, String> {
    log_cmd("get_installed_tools");
    run_python_helper(&find_helper_path(), &["get_installed_tools"])
}

#[tauri::command]
fn save_system_state() -> Result<serde_json::Value, String> {
    log_cmd("save_system_state");
    run_python_helper(&find_helper_path(), &["save_system_state"])
}

#[tauri::command]
fn sync_catalogue(url: String) -> Result<serde_json::Value, String> {
    log_cmd(&format!("sync_catalogue({})", url));
    run_python_helper(&find_helper_path(), &["sync_catalogue_remote", &url])
}

#[tauri::command]
fn install_tool(ref_: String) -> Result<serde_json::Value, String> {
    log_cmd(&format!("install_tool({})", ref_));
    run_python_helper(&find_helper_path(), &["install_tool", &ref_])
}

#[tauri::command]
fn uninstall_tool(ref_: String) -> Result<serde_json::Value, String> {
    log_cmd(&format!("uninstall_tool({})", ref_));
    run_python_helper(&find_helper_path(), &["uninstall_tool", &ref_])
}

#[tauri::command]
fn get_providers() -> Result<serde_json::Value, String> {
    log_cmd("get_providers");
    run_python_helper(&find_helper_path(), &["get_providers"])
}

#[tauri::command]
fn add_provider(data_json: String) -> Result<serde_json::Value, String> {
    log_cmd("add_provider");
    run_python_helper(&find_helper_path(), &["add_provider", &data_json])
}

fn install_db_path() -> PathBuf {
    mw_home().join("runtime.db")
}

#[tauri::command]
fn install_queue_add(ref_: String, name: String, job_type: String) -> Result<u64, String> {
    log_cmd(&format!("install_queue_add({}, {}, {})", ref_, name, job_type));
    let db = install_db_path();
    ensure_install_jobs(&db);
    let chk = format!("SELECT COUNT(*) FROM install_jobs WHERE ref='{}' AND status IN ('queued','running');", sql_esc(&ref_));
    if db_scalar_u64(&db, &chk) > 0 {
        log_to_file("QUEUE", &format!("add_job doublon ignoré: {}", ref_));
        return Ok(0);
    }
    let now = now_secs() as i64;
    db_run(&db, &format!("INSERT INTO install_jobs (ref,name,job_type,status,created_at,updated_at) VALUES ('{}','{}','{}','queued',{},{});", sql_esc(&ref_), sql_esc(&name), sql_esc(&job_type), now, now));
    let id = db_scalar_u64(&db, "SELECT last_insert_rowid();");
    log_to_file("QUEUE", &format!("add_job #{} {} ({})", id, ref_, job_type));
    Ok(id)
}

#[tauri::command]
fn install_queue_cancel(id: u64) -> Result<(), String> {
    log_cmd(&format!("install_queue_cancel({})", id));
    let db = install_db_path();
    log_to_file("QUEUE", &format!("cancel_job #{}", id));
    let rows = db_query_json(&db, &format!("SELECT pid FROM install_jobs WHERE id={} AND status='running';", id));
    if let Some(pid) = rows.first().and_then(|r| r.get("pid")).and_then(|x| x.as_u64()) {
        let pid = pid as u32;
        #[cfg(unix)]
        { let neg = format!("-{}", pid); let _ = Command::new("kill").args(["-9", &neg]).status(); }
        #[cfg(not(unix))]
        { let _ = Command::new("kill").arg(pid.to_string()).status(); }
    }
    db_run(&db, &format!("UPDATE install_jobs SET status='cancelled', updated_at={} WHERE id={} AND status IN ('queued','running');", now_secs(), id));
    Ok(())
}

#[tauri::command]
fn install_queue_status() -> Result<Vec<InstallJob>, String> {
    let db = install_db_path();
    let rows = db_query_json(&db, "SELECT id,ref,name,job_type,status,log FROM install_jobs ORDER BY id;");
    Ok(rows.iter().map(|r| InstallJob {
        id: r.get("id").and_then(|x| x.as_u64()).unwrap_or(0),
        ref_: r.get("ref").and_then(|x| x.as_str()).unwrap_or("").to_string(),
        name: r.get("name").and_then(|x| x.as_str()).unwrap_or("").to_string(),
        job_type: r.get("job_type").and_then(|x| x.as_str()).unwrap_or("").to_string(),
        status: r.get("status").and_then(|x| x.as_str()).unwrap_or("").to_string(),
        log: r.get("log").and_then(|x| x.as_str()).unwrap_or("").to_string(),
    }).collect())
}

#[tauri::command]
fn install_queue_clear() -> Result<(), String> {
    log_cmd("install_queue_clear");
    let db = install_db_path();
    log_to_file("QUEUE", "clear_completed");
    db_run(&db, "DELETE FROM install_jobs WHERE status IN ('installed','removed','failed','cancelled');");
    Ok(())
}

#[tauri::command]
fn install_all_tools() -> Result<serde_json::Value, String> {
    log_cmd("install_all_tools");
    daemon_post("tools/install/all", "{}")
}

#[tauri::command]
fn process_list() -> Result<Vec<ProcInfo>, String> {
    Ok(proc_snapshot())
}

#[tauri::command]
fn process_log(id: u64) -> Result<String, String> {
    Ok(proc_log_tail(id, 200))
}

#[tauri::command]
fn service_list() -> Result<Vec<ServiceInfo>, String> {
    let reg = services_reg().lock().unwrap();
    Ok(reg.iter().map(|s| s.info.clone()).collect())
}

#[tauri::command]
fn service_log(name: String, lines: usize) -> Result<String, String> {
    Ok(service_log_tail(&name, if lines == 0 { 200 } else { lines }))
}

#[tauri::command]
fn watch_get(name: String) -> Result<String, String> {
    // api_token vit dans le fichier ~/.modelweaver/api.token (écrit par le
    // daemon), pas dans le cache watch en mémoire. On le lit directement.
    if name == "api_token" {
        return std::fs::read_to_string(mw_home().join("api.token"))
            .map(|s| s.trim().to_string())
            .map_err(|_| "api token not found".to_string());
    }
    Ok(read_watch_cache(&name).unwrap_or_default())
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

#[tauri::command]
fn app_version() -> String {
    // Version COMPLÈTE de la release (ex: 0.6.0.36) telle que livrée dans le
    // tarball (~/.modelweaver/version.txt). Cargo limite à 3 segments, donc on
    // lit le fichier txt qui porte la source de vérité. Fallback = version Cargo.
    let p = mw_home().join("version.txt");
    if let Ok(s) = std::fs::read_to_string(&p) {
        let v = s.trim();
        if !v.is_empty() { return v.to_string(); }
    }
    env!("CARGO_PKG_VERSION").to_string()
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

/// Copie gui_helper.py depuis le dossier de ressources Tauri vers
/// ~/.modelweaver afin que le helper Python puisse importer modules.* .
fn ensure_bundled_resources(app: &tauri::App) {
    if let Ok(res_dir) = app.path().resource_dir() {
        let home = get_home_dir();
        let dest = mw_home();
        let _ = std::fs::create_dir_all(&dest);

        let src_helper = res_dir.join("gui_helper.py");
        if src_helper.exists() {
            let _ = std::fs::copy(&src_helper, dest.join("gui_helper.py"));
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

fn autotest_enabled() -> bool {
    // Désactivé par défaut. Activé si MODELWEAVER_ENABLE_AUTOTEST=1|true|yes.
    match std::env::var("MODELWEAVER_ENABLE_AUTOTEST") {
        Ok(v) => {
            let v = v.trim().to_ascii_lowercase();
            v == "1" || v == "true" || v == "yes" || v == "on"
        }
        Err(_) => false,
    }
}

#[tauri::command]
fn autotest_enabled_cmd() -> bool {
    autotest_enabled()
}

fn main() {
    let helper_path = find_helper_path();
    let db_path = mw_home().join("runtime.db");
    log_to_file("INIT", &format!("ModelWeaver main starting, helper={}", helper_path.display()));
    log_to_file("INIT", &format!("OS={}, ARCH={}", std::env::consts::OS, std::env::consts::ARCH));

    // Un seul superviseur à la fois (single-instance, y compris le superviseur).
    ensure_single_supervisor();

    ensure_install_jobs(&db_path);

    // Root of the process tree: the main calling process ("modelweaver-main").
    let root_pid = std::process::id();
    proc_register("modelweaver-main", None, Some(root_pid), "modelweaver", String::new(), "running");
    start_proc_monitor(db_path.clone());
    register_thread_service("proc-monitor");

    // Services légers codés en Rust (watch/cache).
    watch_installed_tools_rust(2.0);
    register_thread_service("watch:installed-tools");
    watch_sys_state_rust(helper_path.clone(), 2.0);
    register_thread_service("watch:sys-state");

    // Services complexes/distants : enfants Python supervisés (auto-restart).
    // Chaque service est un seul processus à la fois (verrou d'instance unique
    // posé côté Python via services._common.acquire_instance_lock ; le
    // superviseur vérifie aussi le lock avant de (re)spawn).
    let repo_root = helper_path.parent().map(|p| p.to_path_buf()).unwrap_or_else(|| find_repo_root());
    let cat_entry = service_entry(&repo_root, "catalogue");
    let cat_db = mw_home().join("catalogue.remote.db");
    define_service("catalogue", "loop", python_bin(),
        vec![cat_entry.display().to_string(), "--port".to_string(), "8765".to_string(), "--db".to_string(), cat_db.display().to_string()], None, true, false);
    define_service("installer", "loop", python_bin(),
        vec![service_entry(&repo_root, "installer_worker").display().to_string()], None, true, false);
    // Service `tester` : opt-in (MODELWEAVER_ENABLE_AUTOTEST). Désactivé par défaut.
    if autotest_enabled() {
        define_service("tester", "loop", python_bin(),
            vec![service_entry(&repo_root, "tester").display().to_string()], None, true, false);
    }
    // Daemon API (backend unique, consommé par toute interface).
    define_service("api", "loop", python_bin(),
        vec![repo_root.join("services").join("api").join("daemon.py").display().to_string(), "serve".to_string(), "--port".to_string(), "8770".to_string()], None, true, false);
    start_service_supervisor();
    write_services_summary();

    // Auto-install tous les outils dès que le daemon est prêt — opt-in uniquement
    // (MODELWEAVER_ENABLE_AUTOTEST). Désactivé par défaut : pas de déclenchement
    // automatique tant que l'autotest n'est pas en place.
    if autotest_enabled() {
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_secs(8));
        let home = get_home_dir();
        let token_path = mw_home().join("api.token");
        let port_path = mw_home().join("api.port");
        let token = match std::fs::read_to_string(&token_path) {
            Ok(t) => t,
            Err(e) => { log_to_file("AUTO_INSTALL", &format!("token not found: {}", e)); return; }
        };
        let port = std::fs::read_to_string(&port_path).unwrap_or_else(|_| "8770".into());
        let port = port.trim();
        let url = format!("http://127.0.0.1:{}/v1/tools/install/all", port);
        log_to_file("AUTO_INSTALL", &format!("calling POST {}", url));
        let output = match std::process::Command::new("curl")
            .args(["-s", "-X", "POST", "-H", &format!("Authorization: Bearer {}", token.trim()), "-d", "{}", &url])
            .output()
        {
            Ok(o) => o,
            Err(e) => { log_to_file("AUTO_INSTALL", &format!("curl failed: {}", e)); return; }
        };
        let body = String::from_utf8_lossy(&output.stdout).to_string();
        log_to_file("AUTO_INSTALL", &format!("result: {}", body));
    });
    }

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            ensure_bundled_resources(app);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            daemon_post,
            log_message,
            get_system_info,
            check_dependencies,
            check_dependencies_manifest,
            install_all_dependencies,
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
            get_providers,
            add_provider,
            install_queue_add,
            install_queue_cancel,
            install_queue_status,
            install_queue_clear,
            process_list,
            process_log,
            install_all_tools,
            service_list,
            service_log,
            watch_get,
            run_command,
            get_platform,
            check_dependencies_with_config,
            read_debug_logs,
            close_splashscreen,
            app_version,
            autotest_enabled_cmd,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
