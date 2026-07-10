# ModelWeaver Bootstrap Installer (Windows)

$ErrorActionPreference = "Stop"
$DefaultInstallDir = "$env:USERPROFILE\.modelweaver"
$ProjectName = "ModelWeaver"

function Write-LogInfo($msg) { Write-Host "[INFO] $msg" -ForegroundColor Green }
function Write-LogWarn($msg) { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-LogErr($msg) { Write-Host "[ERROR] $msg" -ForegroundColor Red }

function Check-Python {
    try {
        python --version
        return $true
    } catch {
        return $false
    }
}

function Check-Sqlite {
    try {
        sqlite3 --version
        return $true
    } catch {
        return $false
    }
}

function Install-Dependencies {
    Write-LogInfo "Installation des dépendances système (Python, SQLite...)"
    
    # Utilisation de winget pour l'installation automatique
    try {
        Write-LogInfo "Installation de Python via winget..."
        winget install -e --id Python.Python.3 --silent
        
        Write-LogInfo "Installation de SQLite via winget..."
        winget install -e --id SQLite.SQLite --silent
        
        Write-LogInfo "Installation de Git via winget..."
        winget install -e --id Git.Git --silent
    } catch {
        Write-LogErr "Échec de l'installation via winget. Veuillez installer Python et SQLite manuellement."
        exit 1
    }

    Write-LogInfo "Installation des bibliothèques Python..."
    pip install -q pyyaml libsql-client python-dotenv psutil requests keyring
}

function Download-Project {
    param([string]$TargetDir)
    Write-LogInfo "Téléchargement du projet depuis GitHub..."
    if (!(Test-Path $TargetDir)) {
        New-Item -ItemType Directory -Path $TargetDir -Force
    }
    git clone --depth 1 https://github.com/pilous-garage/ModelWeaver.git $TargetDir
}

function Run-Interactive {
    Write-Host "===================================================="
    Write-Host "   Welcome to $ProjectName Installer (Interactive)"
    Write-Host "===================================================="

    $installPath = Read-Host "Où souhaitez-vous installer $ProjectName ? [$DefaultInstallDir]"
    if ([string]::IsNullOrWhiteSpace($installPath)) { $installPath = $DefaultInstallDir }

    Write-Host "`n--- Conditions Générales d'Utilisation ---"
    Write-Host "Le logiciel est fourni 'tel quel'. L'utilisateur est responsable de ses clés API."
    $acceptTOS = Read-Host "Acceptez-vous les CGU ? (y/n)"
    if ($acceptTOS -ne 'y') {
        Write-LogErr "Installation annulée. Vous devez accepter les CGU."
        exit 1
    }

    if (!(Check-Python) -or !(Check-Sqlite)) {
        $installDeps = Read-Host "Certaines dépendances sont manquantes. Les installer maintenant ? (y/n)"
        if ($installDeps -eq 'y') {
            Install-Dependencies
        } else {
            Write-LogErr "L'installation nécessite Python et SQLite. Arrêt."
            exit 1
        }
    }

    $doDownload = Read-Host "Télécharger le moteur ModelWeaver ? (y/n)"
    if ($doDownload -eq 'y') {
        Download-Project $installPath
    }

    Write-LogInfo "Installation terminée avec succès dans $installPath !"
}

function Run-AutoInstall {
    Write-LogInfo "Lancement de l'installation automatique..."
    Install-Dependencies
    Download-Project $DefaultInstallDir
    Write-LogInfo "Installation automatique terminée dans $DefaultInstallDir !"
}

# Entrée principale
if ($args[0] -eq "--autoinstall") {
    Run-AutoInstall
} else {
    Run-Interactive
}
