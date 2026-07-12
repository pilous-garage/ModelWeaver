#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::Write;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

const VERSION: &str = env!("CARGO_PKG_VERSION");

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
    home_dir().join(".modelweaver")
}

fn home_dir() -> PathBuf {
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/root"))
}

fn python_bin() -> &'static str {
    if std::env::consts::OS == "windows" { "python" } else { "python3" }
}

fn find_helper_path() -> PathBuf {
    let production = mw_home().join("gui_helper.py");
    if production.exists() {
        return production;
    }
    production
}

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

fn service_entry(repo: &PathBuf, name: &str) -> PathBuf {
    repo.join("services").join(name).join("service.py")
}

fn lock_path(name: &str) -> PathBuf {
    mw_home().join("run").join(format!("{}.pid", name))
}

fn process_alive(pid: i32) -> bool {
    unsafe { libc::kill(pid, 0) == 0 }
}

#[cfg(unix)]
fn signal_group(pgid: i32, sig: i32) -> bool {
    // pid négatif = groupe de processus entier (superviseur + services enfants).
    unsafe { libc::kill(-pgid, sig) == 0 }
}

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

fn acquire_instance_lock(name: &str) -> bool {
    let p = lock_path(name);
    if let Some(parent) = p.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if instance_lock_taken(name) {
        // Kill-and-replace : on tue l'ancien superviseur (et son groupe de
        // processus = ses services enfants) via son process group, puis on
        // réutilise le verrou. La base est sur disque : aucune donnée perdue.
        if let Ok(s) = std::fs::read_to_string(&p) {
            if let Ok(pid) = s.trim().parse::<i32>() {
                if pid > 0 {
                    let _ = signal_group(pid, libc::SIGTERM);
                    for _ in 0..50 {
                        if !process_alive(pid) {
                            break;
                        }
                        std::thread::sleep(Duration::from_millis(100));
                    }
                    let _ = signal_group(pid, libc::SIGKILL);
                }
            }
        }
        let _ = std::fs::remove_file(&p);
    }
    if let Ok(mut f) = std::fs::File::create(&p) {
        let _ = f.write_all(std::process::id().to_string().as_bytes());
        return true;
    }
    false
}

fn release_instance_lock(name: &str) {
    let p = lock_path(name);
    let _ = std::fs::remove_file(&p);
}

fn kill_children(services: &Arc<Mutex<Vec<Service>>>) {
    let mut svcs = services.lock().unwrap();
    for s in svcs.iter_mut() {
        if let Some(c) = s.child.as_mut() {
            let _ = c.kill();
        }
        s.child = None;
    }
}

#[cfg(unix)]
fn become_group_leader() {
    // Le superviseur devient leader de son propre process group : ses enfants
    // héritent de ce pgid, donc `kill -TERM -<pid>` les tue aussi.
    unsafe {
        let _ = libc::setpgid(0, 0);
    }
}

struct Service {
    name: String,
    command: String,
    args: Vec<String>,
    restart: bool,
    child: Option<Child>,
    restarts: u32,
    started_at: u64,
}

fn logs_dir() -> PathBuf {
    let d = mw_home().join("logs");
    let _ = std::fs::create_dir_all(&d);
    d
}

fn spawn_service(s: &mut Service) {
    let logp = logs_dir().join(format!("service-{}.log", s.name.replace('/', "_")));
    let mut cmd = Command::new(&s.command);
    cmd.args(&s.args);
    // PYTHONPATH = racine du repo pour que `import services` / `import modules` fonctionne.
    let repo = find_repo_root();
    cmd.env("PYTHONPATH", &repo);
    cmd.current_dir(&repo);
    let f = std::fs::OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(true)
        .open(&logp)
        .ok();
    if let Some(f) = f {
        if let Ok(f2) = f.try_clone() {
            cmd.stdout(Stdio::from(f2));
            cmd.stderr(Stdio::from(f));
        }
    }
    match cmd.spawn() {
        Ok(child) => {
            s.child = Some(child);
            s.started_at = now_secs();
            eprintln!("[main-cli] ✅ service '{}' démarré", s.name);
        }
        Err(e) => {
            eprintln!("[main-cli] ❌ service '{}' échec spawn: {}", s.name, e);
        }
    }
}

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

fn supervisor_thread(services: Arc<Mutex<Vec<Service>>>) {
    std::thread::spawn(move || loop {
        {
            let mut svcs = services.lock().unwrap();
            for s in svcs.iter_mut() {
                let exited = match s.child.as_mut() {
                    Some(c) => match c.try_wait() {
                        Ok(Some(code)) => {
                            eprintln!(
                                "[main-cli] service '{}' terminé (code {:?})",
                                s.name,
                                code.code()
                            );
                            true
                        }
                        Ok(None) => false,
                        Err(_) => true,
                    },
                    None => true,
                };
                if exited {
                    s.child = None;
                    if now_secs().saturating_sub(s.started_at) > 60 {
                        s.restarts = 0;
                    }
                    if s.restart && s.restarts < 10 {
                        s.restarts += 1;
                        eprintln!(
                            "[main-cli] restart '{}' (attempt {})",
                            s.name, s.restarts
                        );
                        spawn_service(s);
                    }
                }
            }
        }
        std::thread::sleep(Duration::from_millis(1000));
    });
}

fn auto_install_thread() {
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_secs(8));
        let token_path = mw_home().join("api.token");
        let port_path = mw_home().join("api.port");
        let token = match std::fs::read_to_string(&token_path) {
            Ok(t) => t,
            Err(e) => {
                eprintln!("[main-cli] auto-install: token introuvable: {}", e);
                return;
            }
        };
        let port = std::fs::read_to_string(&port_path).unwrap_or_else(|_| "8770".to_string());
        let port = port.trim();
        let url = format!("http://127.0.0.1:{}/v1/tools/install/all", port);
        eprintln!("[main-cli] auto-install: POST {}", url);
        let _ = Command::new("curl")
            .args([
                "-s",
                "-X",
                "POST",
                "-H",
                &format!("Authorization: Bearer {}", token.trim()),
                "-d",
                "{}",
                &url,
            ])
            .output();
    });
}

fn cmd_version() {
    println!("modelweaver-cli {}", VERSION);
}

fn cmd_info() {
    let info = serde_json::json!({
        "product": "modelweaver-cli",
        "version": VERSION,
        "os": std::env::consts::OS,
        "arch": std::env::consts::ARCH,
        "mw_home": mw_home().to_string_lossy(),
        "helper": find_helper_path().to_string_lossy(),
        "repo_root": find_repo_root().to_string_lossy(),
    });
    println!("{}", serde_json::to_string_pretty(&info).unwrap());
}

fn cmd_start() {
    if !acquire_instance_lock("main-cli") {
        eprintln!("[main-cli] une instance tourne déjà (run/main-cli.pid). Arrêt.");
        std::process::exit(2);
    }
    // Devenir leader de groupe AVANT de spawner les services, pour que les
    // enfants héritent de ce pgid et soient tués avec le superviseur.
    #[cfg(unix)]
    become_group_leader();
    let helper = find_helper_path();
    let repo_root = helper.parent().map(|p| p.to_path_buf()).unwrap_or_else(find_repo_root);
    let cat_db = mw_home().join("catalogue.remote.db");

    let mut services = vec![
        service_def(
            "catalogue",
            python_bin(),
            vec![
                service_entry(&repo_root, "catalogue").display().to_string(),
                "--port".into(),
                "8765".into(),
                "--db".into(),
                cat_db.display().to_string(),
            ],
        ),
        service_def(
            "installer",
            python_bin(),
            vec![service_entry(&repo_root, "installer_worker").display().to_string()],
        ),
        service_def(
            "tester",
            python_bin(),
            vec![service_entry(&repo_root, "tester").display().to_string()],
        ),
        service_def(
            "api",
            python_bin(),
            vec![
                repo_root.join("services").join("api").join("daemon.py").display().to_string(),
                "serve".into(),
                "--port".into(),
                "8770".into(),
            ],
        ),
    ];

    for s in services.iter_mut() {
        spawn_service(s);
    }

    let services = Arc::new(Mutex::new(services));
    supervisor_thread(services.clone());
    auto_install_thread();

    eprintln!("[main-cli] services démarrés. Ctrl-C pour arrêter.");

    // Blocage jusqu'à SIGINT/SIGTERM.
    ctrlc_handler(services.clone());
    eprintln!("[main-cli] arrêt demandé, nettoyage...");
    kill_children(&services);
    release_instance_lock("main-cli");
    std::process::exit(0);
}

fn service_def(name: &str, command: &str, args: Vec<String>) -> Service {
    Service {
        name: name.to_string(),
        command: command.to_string(),
        args,
        restart: true,
        child: None,
        restarts: 0,
        started_at: 0,
    }
}

#[cfg(unix)]
fn ctrlc_handler(services: Arc<Mutex<Vec<Service>>>) {
    let (tx, rx) = std::sync::mpsc::channel::<()>();
    let tx = Arc::new(Mutex::new(Some(tx)));
    let services2 = services.clone();
    ctrlc::set_handler(move || {
        kill_children(&services2);
        if let Some(tx) = tx.lock().unwrap().take() {
            let _ = tx.send(());
        }
    })
    .ok();
    let _ = rx.recv();
}

#[cfg(not(unix))]
fn ctrlc_handler(services: Arc<Mutex<Vec<Service>>>) {
    let (tx, rx) = std::sync::mpsc::channel::<()>();
    let tx = Arc::new(Mutex::new(Some(tx)));
    ctrlc::set_handler(move || {
        kill_children(&services);
        if let Some(tx) = tx.lock().unwrap().take() {
            let _ = tx.send(());
        }
    })
    .ok();
    let _ = rx.recv();
}

fn cmd_stop() {
    let p = lock_path("main-cli");
    if let Ok(s) = std::fs::read_to_string(&p) {
        if let Ok(pid) = s.trim().parse::<i32>() {
            if pid > 0 {
                // Le superviseur est group leader : on envoie le signal à son
                // process group (-pid) pour tuer aussi tous les services enfants
                // d'un coup. SIGKILL en repli sur le groupe si toujours vivant.
                let _ = signal_group(pid, libc::SIGTERM);
                for _ in 0..30 {
                    if !process_alive(pid) {
                        break;
                    }
                    std::thread::sleep(Duration::from_millis(100));
                }
                if process_alive(pid) {
                    let _ = signal_group(pid, libc::SIGKILL);
                }
                eprintln!("[main-cli] arrêt de l'instance pid {}", pid);
            }
        }
    } else {
        eprintln!("[main-cli] aucune instance en cours.");
    }
    let _ = std::fs::remove_file(&p);
}

fn usage() {
    eprintln!("Usage: modelweaver-cli [--version|--info|start|stop]");
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let cmd = args.get(1).map(|s| s.as_str()).unwrap_or("start");
    match cmd {
        "--version" | "-v" | "version" => cmd_version(),
        "--info" | "-i" | "info" => cmd_info(),
        "start" => cmd_start(),
        "stop" => cmd_stop(),
        "--help" | "-h" | "help" => usage(),
        other => {
            eprintln!("[main-cli] commande inconnue: {}", other);
            usage();
            std::process::exit(1);
        }
    }
}
