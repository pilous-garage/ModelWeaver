use std::env;
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::PathBuf;
use std::process::Command;

const REPO: &str = "pilous-garage/ModelWeaver";

const INSTALL_ROOT: &str = "/opt/modelweaver";
const BIN_LINK: &str = "/usr/bin/modelweaver";
const CLI_LINK: &str = "/usr/bin/modelweaver-cli";

fn home_dir() -> PathBuf {
    PathBuf::from(env::var("HOME").unwrap_or_else(|_| "/root".to_string()))
}

fn is_root() -> bool {
    Command::new("id")
        .args(["-u"])
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim() == "0")
        .unwrap_or(false)
}

/// Exécute cmd avec args, en préfixant par sudo si on n'est pas root.
fn root_run(cmd: &str, args: &[&str]) -> Result<(), String> {
    let mut v: Vec<&str> = if is_root() {
        vec![cmd]
    } else {
        vec!["sudo", cmd]
    };
    v.extend_from_slice(args);
    let o = Command::new(v[0]).args(&v[1..]).output().map_err(|e| format!("{} error: {}", v[0], e))?;
    if o.status.success() {
        Ok(())
    } else {
        Err(format!(
            "{} exit {}: {}",
            v[0],
            o.status,
            String::from_utf8_lossy(&o.stderr)
        ))
    }
}

fn command_exists(name: &str) -> bool {
    Command::new("which")
        .arg(name)
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

fn detect_pkg_manager() -> Option<&'static str> {
    if command_exists("apt-get") {
        return Some("apt");
    }
    if command_exists("dnf") {
        return Some("dnf");
    }
    if command_exists("apk") {
        return Some("apk");
    }
    if command_exists("pacman") {
        return Some("pacman");
    }
    None
}

fn install_system_pkgs(pkgs: &[&str]) -> Result<(), String> {
    match detect_pkg_manager() {
        Some("apt") => {
            root_run("apt-get", &["update", "-qq"])?;
            let mut args = vec!["install", "-y"];
            for p in pkgs {
                args.push(p);
            }
            root_run("apt-get", &args)
        }
        Some("dnf") => {
            let mut args = vec!["install", "-y"];
            for p in pkgs {
                args.push(p);
            }
            root_run("dnf", &args)
        }
        Some("apk") => {
            root_run("apk", &["update"])?;
            let mut args = vec!["add"];
            for p in pkgs {
                args.push(p);
            }
            root_run("apk", &args)
        }
        Some("pacman") => {
            let mut args = vec!["-S", "--noconfirm"];
            for p in pkgs {
                args.push(p);
            }
            root_run("pacman", &args)
        }
        Some(_) => Err("Gestionnaire de paquets non supporté".to_string()),
        None => Err("Aucun gestionnaire de paquets supporté (apt/dnf/apk/pacman)".to_string()),
    }
}

/// Détecte et installe automatiquement les prérequis système (python3, pip, sqlite3).
fn ensure_prereqs() -> Result<(), String> {
    let mut missing = Vec::new();
    if !command_exists("python3") {
        missing.push("python3");
    }
    if command_exists("python3") {
        let has_pip = Command::new("python3")
            .args(["-m", "pip", "--version"])
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false);
        if !has_pip {
            missing.push("python3-pip");
        }
    } else {
        missing.push("python3-pip");
    }
    if !command_exists("sqlite3") {
        missing.push("sqlite3");
    }
    if !command_exists("curl") && !command_exists("wget") {
        missing.push("curl");
    }

    if missing.is_empty() {
        println!("[bootstrap-cli] ✅ prérequis présents (python3, pip, sqlite3, curl)");
        return Ok(());
    }
    println!(
        "[bootstrap-cli] prérequis manquants: {:?} — installation automatique...",
        missing
    );
    let pm = detect_pkg_manager().ok_or_else(|| "gestionnaire de paquets introuvable".to_string())?;
    let install_list: Vec<&str> = match pm {
        "apt" => vec!["python3", "python3-pip", "sqlite3", "curl"],
        "dnf" => vec!["python3", "python3-pip", "sqlite", "curl"],
        "apk" => vec!["python3", "py3-pip", "sqlite", "curl"],
        "pacman" => vec!["python3", "python-pip", "sqlite", "curl"],
        _ => return Err("gestionnaire de paquets inconnu".to_string()),
    };
    install_system_pkgs(&install_list)?;
    if command_exists("python3") && command_exists("sqlite3") {
        println!("[bootstrap-cli] ✅ prérequis installés");
        Ok(())
    } else {
        Err("échec de l'installation des prérequis".to_string())
    }
}

fn http_get(url: &str) -> Result<String, String> {
    if Command::new("which")
        .arg("curl")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
    {
        let o = Command::new("curl")
            .args([
                "-s",
                "-H",
                "Accept: application/json",
                "-H",
                "User-Agent: ModelWeaver",
                url,
            ])
            .output()
            .map_err(|e| format!("curl error: {}", e))?;
        if !o.status.success() {
            return Err(format!("curl failed for {}", url));
        }
        return Ok(String::from_utf8_lossy(&o.stdout).to_string());
    }
    if Command::new("which")
        .arg("wget")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
    {
        let o = Command::new("wget")
            .args(["-q", "-O-", "--header=Accept: application/json", url])
            .output()
            .map_err(|e| format!("wget error: {}", e))?;
        return Ok(String::from_utf8_lossy(&o.stdout).to_string());
    }
    if command_exists("python3") {
        let o = Command::new("python3")
            .args([
                "-c",
                "import sys,urllib.request;print(urllib.request.urlopen(sys.argv[1]).read().decode())",
                url,
            ])
            .output()
            .map_err(|e| format!("python3 error: {}", e))?;
        if o.status.success() {
            return Ok(String::from_utf8_lossy(&o.stdout).to_string());
        }
        return Err("echec download python3".to_string());
    }
    Err("ni curl ni wget ni python3 disponible".to_string())
}

fn download(url: &str, dest: &PathBuf) -> Result<(), String> {
    if Command::new("which")
        .arg("curl")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
    {
        let o = Command::new("curl")
            .args(["-L", "-o", dest.to_str().unwrap(), url])
            .output()
            .map_err(|e| format!("curl error: {}", e))?;
        if o.status.success() {
            return Ok(());
        }
        return Err("echec download curl".to_string());
    }
    if Command::new("which")
        .arg("wget")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
    {
        let o = Command::new("wget")
            .args(["-q", "-O", dest.to_str().unwrap(), url])
            .output()
            .map_err(|e| format!("wget error: {}", e))?;
        if o.status.success() {
            return Ok(());
        }
        return Err("echec download wget".to_string());
    }
    if command_exists("python3") {
        let o = Command::new("python3")
            .args([
                "-c",
                "import sys,urllib.request;urllib.request.urlretrieve(sys.argv[1],sys.argv[2])",
                url,
                dest.to_str().unwrap(),
            ])
            .output()
            .map_err(|e| format!("python3 error: {}", e))?;
        if o.status.success() {
            return Ok(());
        }
        return Err("echec download python3".to_string());
    }
    Err("curl/wget/python3 indisponibles".to_string())
}

fn run(cmd: &str, args: &[&str]) -> Result<(), String> {
    let o = Command::new(cmd)
        .args(args)
        .output()
        .map_err(|e| format!("{} error: {}", cmd, e))?;
    if o.status.success() {
        Ok(())
    } else {
        Err(format!(
            "{} exit {}: {}",
            cmd,
            o.status,
            String::from_utf8_lossy(&o.stderr)
        ))
    }
}

fn installed_version() -> Option<String> {
    // On interroge le binaire CLI headless (le GUI nécessite GTK et échoue en headless).
    let bin = PathBuf::from(CLI_LINK);
    if !bin.exists() {
        return None;
    }
    let o = Command::new(&bin)
        .arg("--version")
        .output()
        .ok()?;
    if !o.status.success() {
        return None;
    }
    let v = String::from_utf8_lossy(&o.stdout);
    Some(v.split_whitespace().last().unwrap_or("").to_string())
}

fn parse_version(v: &str) -> Vec<u32> {
    v.trim_start_matches('v')
        .split('.')
        .filter_map(|s| s.parse::<u32>().ok())
        .collect()
}

fn is_newer(latest: &str, current: &str) -> bool {
    let mut lv = parse_version(latest);
    let mut cv = parse_version(current);
    let max = lv.len().max(cv.len());
    lv.resize(max, 0);
    cv.resize(max, 0);
    lv > cv
}

fn install_systemwide(archive: &PathBuf) -> Result<(), String> {
    println!(
        "[bootstrap-cli] installation system-wide -> {} (binaires: {} + {})",
        INSTALL_ROOT, BIN_LINK, CLI_LINK
    );
    root_run("mkdir", &["-p", INSTALL_ROOT])?;
    // Extraction de l'archive (binaires + services/ + modules/ + gui_helper.py) dans INSTALL_ROOT
    root_run("tar", &["-xzf", archive.to_str().unwrap(), "-C", INSTALL_ROOT])?;
    // Liens globaux : GUI (modelweaver) + CLI headless (modelweaver-cli)
    root_run("ln", &["-sf", &format!("{}/modelweaver", INSTALL_ROOT), BIN_LINK])?;
    root_run("ln", &["-sf", &format!("{}/modelweaver-cli", INSTALL_ROOT), CLI_LINK])?;
    // Droits exec sur les binaires
    #[cfg(unix)]
    {
        let _ = fs::set_permissions(
            &PathBuf::from(INSTALL_ROOT).join("modelweaver"),
            fs::Permissions::from_mode(0o755),
        );
        let _ = fs::set_permissions(
            &PathBuf::from(INSTALL_ROOT).join("modelweaver-cli"),
            fs::Permissions::from_mode(0o755),
        );
    }
    println!(
        "[bootstrap-cli] ✅ installé. Commandes globales: modelweaver (GUI), modelweaver-cli (headless)"
    );
    // Dépendances Python du backend (psutil, requests, ...).
    let req = PathBuf::from(INSTALL_ROOT).join("requirements.txt");
    if req.exists() {
        println!("[bootstrap-cli] installation des dépendances Python du backend...");
        let mut pip = Command::new("python3");
        pip.args(["-m", "pip", "install", "--upgrade", "-r", req.to_str().unwrap()]);
        // Install system-wide : on autorise --break-system-packages (PEP 668, distros modernes).
        pip.arg("--break-system-packages");
        match pip.output() {
            Ok(o) if o.status.success() => {
                println!("[bootstrap-cli] ✅ dépendances Python installées");
            }
            Ok(o) => {
                let e = String::from_utf8_lossy(&o.stderr);
                println!("[bootstrap-cli] ⚠ installation deps Python: {}", e.lines().last().unwrap_or(""));
            }
            Err(e) => println!("[bootstrap-cli] ⚠ pip indisponible: {}", e),
        }
    }
    Ok(())
}

fn main() {
    let args: Vec<String> = env::args().collect();
    let do_launch = args.iter().any(|a| a == "--launch");
    let force = args.iter().any(|a| a == "--force");

    let os = env::consts::OS;
    let arch = env::consts::ARCH;
    println!("[bootstrap-cli] plateforme: {}/{}", os, arch);

    if let Err(e) = ensure_prereqs() {
        eprintln!("[bootstrap-cli] erreur prérequis: {}", e);
        std::process::exit(1);
    }

    let local = installed_version();
    println!(
        "[bootstrap-cli] version installée ({}): {}",
        BIN_LINK,
        local.clone().unwrap_or_else(|| "aucune".to_string())
    );

    println!("[bootstrap-cli] récupération de la release latest...");
    let body = match http_get(&format!(
        "https://api.github.com/repos/{}/releases/latest",
        REPO
    )) {
        Ok(b) => b,
        Err(e) => {
            eprintln!("[bootstrap-cli] erreur GitHub: {}", e);
            std::process::exit(1);
        }
    };
    let json: serde_json::Value = match serde_json::from_str(&body) {
        Ok(j) => j,
        Err(e) => {
            eprintln!("[bootstrap-cli] JSON invalide: {}", e);
            std::process::exit(1);
        }
    };
    let tag = json["tag_name"].as_str().unwrap_or("").to_string();
    println!("[bootstrap-cli] dernière release: {}", tag);

    if let Some(ref lv) = local {
        if !force && !is_newer(&tag, lv) {
            println!(
                "[bootstrap-cli] déjà à jour (installé {} >= latest {}). Utilisez --force pour réinstaller.",
                lv, tag
            );
            if do_launch {
                launch();
            }
            return;
        }
    }

    let assets = json["assets"].as_array().cloned().unwrap_or_default();
    let mut url = String::new();
    for a in &assets {
        let name = a["name"].as_str().unwrap_or("");
        if name.contains("modelweaver-release") && name.contains(os) && name.contains(arch) {
            url = a["browser_download_url"].as_str().unwrap_or("").to_string();
            break;
        }
    }
    if url.is_empty() {
        eprintln!(
            "[bootstrap-cli] asset modelweaver-release-{}-{} introuvable dans la release.",
            os, arch
        );
        std::process::exit(1);
    }
    println!("[bootstrap-cli] téléchargement: {}", url);

    let cache = home_dir().join(".modelweaver").join("cache");
    let _ = fs::create_dir_all(&cache);
    let archive = cache.join("modelweaver-release.tar.gz");
    if let Err(e) = download(&url, &archive) {
        eprintln!("[bootstrap-cli] échec download: {}", e);
        std::process::exit(1);
    }

    if let Err(e) = install_systemwide(&archive) {
        eprintln!("[bootstrap-cli] échec installation: {}", e);
        std::process::exit(1);
    }
    let _ = fs::remove_file(&archive);

    // Validation rapide du backend déployé
    let root = PathBuf::from(INSTALL_ROOT);
    let backend_ok = root.join("services").join("api").join("daemon.py").exists()
        && root.join("modules").join("sql").exists()
        && root.join("gui_helper.py").exists();
    if backend_ok {
        println!("[bootstrap-cli] ✅ backend Python déployé dans {}", INSTALL_ROOT);
    } else {
        println!(
            "[bootstrap-cli] ⚠ backend incomplet dans {} (services/modules/gui_helper manquants)",
            INSTALL_ROOT
        );
    }

    if do_launch {
        launch();
    }
}

fn launch() {
    // Le bootstrap lance le main CLI headless (démarre les services backend + daemon API).
    // La GUI (modelweaver) est lancée séparément par l'utilisateur sur poste graphique.
    let bin = PathBuf::from(CLI_LINK);
    if !bin.exists() {
        eprintln!("[bootstrap-cli] binaire {} introuvable, impossible de lancer.", CLI_LINK);
        return;
    }
    let log = PathBuf::from(INSTALL_ROOT).join("modelweaver-cli.log");
    let stdout = fs::OpenOptions::new().create(true).append(true).open(&log).ok();
    let stderr = fs::OpenOptions::new().create(true).append(true).open(&log).ok();
    let mut cmd = Command::new(&bin);
    cmd.arg("start");
    if let (Some(out), Some(err)) = (stdout, stderr) {
        cmd.stdout(out).stderr(err);
    }
    println!("[bootstrap-cli] lancement du main CLI headless (services + daemon API)... logs: {}", log.display());
    match cmd.spawn() {
        Ok(_) => println!("[bootstrap-cli] ✅ main-cli lancé en arrière-plan."),
        Err(e) => eprintln!("[bootstrap-cli] échec lancement: {}", e),
    }
}
