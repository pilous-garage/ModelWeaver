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

fn get_project_root() -> PathBuf {
    // src-tauri is 2 levels below project root
    let manifest_dir = env!("CARGO_MANIFEST_DIR");
    PathBuf::from(manifest_dir)
        .parent() // gui/installer/
        .unwrap()
        .parent() // gui/
        .unwrap()
        .parent() // project root
        .unwrap()
        .to_path_buf()
}

#[tauri::command]
fn run_python_script(script_path: String, args: Vec<String>) -> Result<PythonResponse, String> {
    let root = get_project_root();
    let full_script_path = root.join(&script_path);
    
    let output = Command::new("python3")
        .arg(&full_script_path)
        .args(&args)
        .current_dir(&root)
        .output()
        .map_err(|e| format!("Failed to execute python3: {}", e))?;

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
        .invoke_handler(tauri::generate_handler![run_python_script])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
