#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::process::{Command, Stdio};
use std::path::PathBuf;
use serde::{Serialize, Deserialize};
use tauri::Emitter;

#[derive(Serialize, Deserialize)]
struct PythonResponse {
    status: String,
    data: serde_json::Value,
    error: Option<String>,
}

fn get_project_root() -> PathBuf {
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

#[tauri::command]
async fn install_tool(app_handle: tauri::AppHandle, tool_ref: String) -> Result<PythonResponse, String> {
    run_streaming_script(app_handle, "gui/installer/scripts/install.py", &tool_ref).await
}

#[tauri::command]
async fn install_tools_batch(app_handle: tauri::AppHandle, tool_refs: Vec<String>, timeout: Option<i32>) -> Result<PythonResponse, String> {
    let root = get_project_root();
    let script_path = root.join("gui/installer/scripts/batch_install.py");

    let timeout_val = timeout.unwrap_or(300);
    let mut cmd_args: Vec<String> = vec![format!("--timeout={}", timeout_val)];
    cmd_args.extend(tool_refs.clone());

    let mut child = Command::new("python3")
        .arg(&script_path)
        .args(&cmd_args)
        .current_dir(&root)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("Failed to spawn python3: {}", e))?;

    let stdout = child.stdout.take()
        .ok_or_else(|| "No stdout from child".to_string())?;
    let reader = BufReader::new(stdout);

    let mut last_result = PythonResponse {
        status: "error".to_string(),
        data: serde_json::Value::Null,
        error: Some("No output".to_string()),
    };

    for line in reader.lines() {
        let line = line.map_err(|e| format!("Failed to read line: {}", e))?;
        if line.trim().is_empty() { continue; }
        if let Ok(val) = serde_json::from_str::<serde_json::Value>(&line) {
            match val.get("type").and_then(|t| t.as_str()) {
                Some("progress") => {
                    let _ = app_handle.emit("install-progress", &val);
                }
                Some("result") => {
                    let status = val.get("status").and_then(|s| s.as_str()).unwrap_or("error").to_string();
                    let error = val.get("error").and_then(|e| e.as_str()).map(|s| s.to_string());
                    last_result = PythonResponse {
                        status, data: val, error,
                    };
                }
                _ => {}
            }
        }
    }

    let status_code = child.wait().map_err(|e| format!("Wait error: {}", e))?;
    if !status_code.success() {
        let stderr = child.stderr.take()
            .map(|s| {
                let mut buf = String::new();
                let _ = std::io::Read::read_to_string(&mut std::io::BufReader::new(s), &mut buf);
                buf
            })
            .unwrap_or_default();
        if last_result.error.is_none() {
            last_result.error = Some(if stderr.is_empty() { "Process failed".into() } else { stderr });
            last_result.status = "error".to_string();
        }
    }

    Ok(last_result)
}

#[tauri::command]
async fn uninstall_tool(app_handle: tauri::AppHandle, tool_ref: String) -> Result<PythonResponse, String> {
    run_streaming_script(app_handle, "gui/installer/scripts/uninstall.py", &tool_ref).await
}

#[tauri::command]
async fn ollama_pull(app_handle: tauri::AppHandle, model_name: String) -> Result<PythonResponse, String> {
    let root = get_project_root();
    let script_path = root.join("gui/installer/scripts/ollama_models.py");

    let mut child = Command::new("python3")
        .arg(&script_path)
        .arg("pull")
        .arg(&model_name)
        .arg("--stream")
        .current_dir(&root)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("Failed to spawn python3: {}", e))?;

    let stdout = child.stdout.take()
        .ok_or_else(|| "No stdout from child".to_string())?;
    let reader = BufReader::new(stdout);

    let mut last_result = PythonResponse {
        status: "error".to_string(),
        data: serde_json::Value::Null,
        error: Some("No output".to_string()),
    };

    for line in reader.lines() {
        let line = line.map_err(|e| format!("Failed to read line: {}", e))?;
        if line.trim().is_empty() { continue; }
        if let Ok(val) = serde_json::from_str::<serde_json::Value>(&line) {
            match val.get("type").and_then(|t| t.as_str()) {
                Some("progress") => {
                    let _ = app_handle.emit("install-progress", &val);
                }
                Some("result") => {
                    let status = val.get("status").and_then(|s| s.as_str()).unwrap_or("error").to_string();
                    let error = val.get("error").and_then(|e| e.as_str()).map(|s| s.to_string());
                    last_result = PythonResponse {
                        status, data: val, error,
                    };
                }
                _ => {}
            }
        }
    }

    let status_code = child.wait().map_err(|e| format!("Wait error: {}", e))?;
    if !status_code.success() {
        let stderr = child.stderr.take()
            .map(|s| {
                let mut buf = String::new();
                let _ = std::io::Read::read_to_string(&mut std::io::BufReader::new(s), &mut buf);
                buf
            })
            .unwrap_or_default();
        if last_result.error.is_none() {
            last_result.error = Some(if stderr.is_empty() { "Process failed".into() } else { stderr });
            last_result.status = "error".to_string();
        }
    }

    Ok(last_result)
}

async fn run_streaming_script(app_handle: tauri::AppHandle, script_rel: &str, tool_ref: &str) -> Result<PythonResponse, String> {
    let root = get_project_root();
    let script_path = root.join(script_rel);

    let mut child = Command::new("python3")
        .arg(&script_path)
        .arg(tool_ref)
        .current_dir(&root)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("Failed to spawn python3: {}", e))?;

    let stdout = child.stdout.take()
        .ok_or_else(|| "No stdout from child".to_string())?;
    let reader = BufReader::new(stdout);

    let mut last_result = PythonResponse {
        status: "error".to_string(),
        data: serde_json::Value::Null,
        error: Some("No output".to_string()),
    };

    for line in reader.lines() {
        let line = line.map_err(|e| format!("Failed to read line: {}", e))?;
        if line.trim().is_empty() {
            continue;
        }
        if let Ok(val) = serde_json::from_str::<serde_json::Value>(&line) {
            match val.get("type").and_then(|t| t.as_str()) {
                Some("progress") => {
                    let _ = app_handle.emit("install-progress", &val);
                }
                Some("result") => {
                    let status = val.get("status").and_then(|s| s.as_str()).unwrap_or("error").to_string();
                    let error = val.get("error").and_then(|e| e.as_str()).map(|s| s.to_string());
                    last_result = PythonResponse {
                        status,
                        data: val,
                        error,
                    };
                }
                _ => {}
            }
        }
    }

    let status_code = child.wait().map_err(|e| format!("Wait error: {}", e))?;
    if !status_code.success() {
        let stderr = child.stderr.take()
            .map(|s| {
                let mut buf = String::new();
                let _ = std::io::Read::read_to_string(&mut std::io::BufReader::new(s), &mut buf);
                buf
            })
            .unwrap_or_default();
        if last_result.error.is_none() {
            last_result.error = Some(if stderr.is_empty() { "Process failed".into() } else { stderr });
            last_result.status = "error".to_string();
        }
    }

    Ok(last_result)
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![run_python_script, install_tool, install_tools_batch, uninstall_tool, ollama_pull])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
