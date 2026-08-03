"""Inventaire matériel complet et précis de la machine (check système).

Détection sans privilèges root dans la mesure du possible :
  - CPU : `lscpu` (parse) + /proc/cpuinfo (détail par cœur)
  - RAM  : psutil (total/used/libre) + /proc/meminfo (détail)
  - Carte mère / BIOS : dmidecode (dégradé si permission refusée)
  - GPU  : lspci (classe VGA/3D/Display) + nvidia-smi / rocm-smi si présents
  - NPU  : /sys/class/accel/* (accélérateurs type AMD XDNA) + lspci
  - Disques : `lsblk -J` (nom, type, taille, modèle, série, interface)
  - Réseau : /sys/class/net/* (nom, adresse MAC, vitesse, état)
  - USB : `lsusb` (vendors/dispositifs)
  - Températures : `sensors` si présent (lm-sensors)

Chaque section est autonome : une erreur sur une source ne casse pas le reste
(champs `error` par section, jamais de raise).
"""

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, Any, List, Optional


def _run(cmd: List[str], timeout: float = 5.0) -> Optional[str]:
    """Exécute une commande, renvoie stdout (str) ou None si échec."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout)
        if r.returncode != 0:
            return None
        return r.stdout
    except Exception:
        return None


def _has(cmd: str) -> bool:
    return shutil.which(cmd) is not None


# ── CPU ─────────────────────────────────────────────────────────────────

def _cpu_lscpu() -> Dict[str, Any]:
    """Détail CPU via lscpu (parse des lignes 'Key:' ou 'Key:' espacé)."""
    out = _run(["lscpu"])
    if not out:
        return {}
    info: Dict[str, Any] = {}
    for line in out.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower().replace(" ", "_")
        val = val.strip()
        if not val:
            continue
        info[key] = val
    return info


def _cpu_info() -> Dict[str, Any]:
    """Agrège lscpu + infos par cœur depuis /proc/cpuinfo."""
    lscpu = _cpu_lscpu()
    cpu: Dict[str, Any] = {}
    if lscpu:
        model = (lscpu.get("model_name") or lscpu.get("nom_du_modèle") or "")
        cpu = {
            "model": model,
            "vendor": lscpu.get("vendor_id") or lscpu.get("fournisseur") or "",
            "architecture": lscpu.get("architecture") or "",
            "sockets": lscpu.get("socket(s)") or lscpu.get("socket") or None,
            "cores_physiques": lscpu.get("core(s)_per_socket") or lscpu.get("cœur(s)_par_socket") or None,
            "threads_par_core": lscpu.get("thread(s)_per_core") or None,
            "threads_total": lscpu.get("cpu(s)") or None,
            "freq_max_mhz": lscpu.get("cpu_max_mhz"),
            "freq_min_mhz": lscpu.get("cpu_min_mhz"),
            "freq_actuelle_mhz": lscpu.get("cpu_mhz"),
            "virtualisation": lscpu.get("virtualization") or None,
            "flags": lscpu.get("flags") or lscpu.get("drapeaux") or "",
        }
    # Modèle + caches via /proc/cpuinfo
    try:
        lines = Path("/proc/cpuinfo").read_text(errors="replace").splitlines()
        model_from_proc = next((l.split(":", 1)[1].strip() for l in lines
                                if l.startswith("model name")), None)
        cache_l2 = next((l.split(":", 1)[1].strip() for l in lines
                         if l.startswith("cache size")), None)
        if model_from_proc and not cpu.get("model"):
            cpu["model"] = model_from_proc
        if cache_l2:
            cpu["cache_l2"] = cache_l2
        cpu["cores_logiques"] = sum(1 for l in lines if l.startswith("processor"))
    except Exception:
        pass
    return cpu


# ── RAM ─────────────────────────────────────────────────────────────────

def _memory_info() -> Dict[str, Any]:
    """RAM totale/utilisée/libre (psutil, non bloquant) + détail /proc/meminfo."""
    mem: Dict[str, Any] = {}
    try:
        import psutil
        vm = psutil.virtual_memory()
        mem.update({
            "ram_total_gb": round(vm.total / (1024 ** 3), 2),
            "ram_used_gb": round(vm.used / (1024 ** 3), 2),
            "ram_available_gb": round(vm.available / (1024 ** 3), 2),
            "ram_used_pct": vm.percent,
            "swap_total_gb": round(psutil.swap_memory().total / (1024 ** 3), 2) if psutil.swap_memory().total else 0,
        })
    except Exception:
        mem["ram_total_gb"] = None
    try:
        info = Path("/proc/meminfo").read_text(errors="replace")
        vals = {}
        for line in info.splitlines():
            k, _, v = line.partition(":")
            vals[k.strip()] = v.strip()
        mem["meminfo"] = vals
    except Exception:
        pass
    return mem


# ── Carte mère / BIOS ───────────────────────────────────────────────────

def _dmidecode_type(t: str) -> Optional[Dict[str, str]]:
    """Extrait un bloc dmidecode -t <type>. None si indisponible (root requis)."""
    out = _run(["dmidecode", "-t", t])
    if not out:
        return None
    d: Dict[str, str] = {}
    for line in out.splitlines():
        if ":" in line and not line.startswith(("#", "Handle")):
            k, _, v = line.partition(":")
            k = k.strip().lower().replace(" ", "_")
            v = v.strip()
            if v and k not in ("handle", "characteristics", "physical_memory_array", "mem_device_speed", "type"):
                d.setdefault(k, v)
    return d


def _motherboard_info() -> Dict[str, Any]:
    """Carte mère + BIOS (dmidecode ; dégradé si non root)."""
    mobo: Dict[str, Any] = {}
    baseboard = _dmidecode_type("baseboard")
    if baseboard:
        mobo["manufacturer"] = baseboard.get("manufacturer")
        mobo["product"] = baseboard.get("product_name")
        mobo["serial"] = baseboard.get("serial_number")
        mobo["version"] = baseboard.get("version")
    else:
        # Fallback sans root : /sys/class/dmi/id
        try:
            mobo["manufacturer"] = Path("/sys/class/dmi/id/board_vendor").read_text().strip()
            mobo["product"] = Path("/sys/class/dmi/id/board_name").read_text().strip()
            mobo["serial"] = Path("/sys/class/dmi/id/board_serial").read_text().strip()
            mobo["sysfs"] = True
        except Exception:
            mobo["error"] = "dmidecode requiert root — infos carte mère indisponibles"
    bios = _dmidecode_type("bios")
    if bios:
        mobo["bios"] = {
            "vendor": bios.get("vendor"),
            "version": bios.get("version"),
            "date": bios.get("release_date"),
        }
    else:
        try:
            mobo["bios"] = {
                "vendor": Path("/sys/class/dmi/id/bios_vendor").read_text().strip(),
                "version": Path("/sys/class/dmi/id/bios_version").read_text().strip(),
                "date": Path("/sys/class/dmi/id/bios_date").read_text().strip(),
            }
        except Exception:
            pass
    return mobo


# ── GPU ─────────────────────────────────────────────────────────────────

def _lspci_devices() -> List[Dict[str, str]]:
    """Liste des périphériques PCI (lspci -nn)."""
    out = _run(["lspci", "-nn"])
    if not out:
        return []
    devs = []
    for line in out.splitlines():
        parts = line.split(" ", 1)
        if len(parts) < 2:
            continue
        slot, desc = parts[0], parts[1]
        devs.append({"slot": slot, "description": desc})
    return devs


def _gpu_info() -> List[Dict[str, Any]]:
    """GPU : classes VGA/3D/Display de lspci + détails propriétaires (nvidia-smi)."""
    gpus: List[Dict[str, Any]] = []
    for d in _lspci_devices():
        low = d["description"].lower()
        if any(k in low for k in ("vga", "3d controller", "display controller")):
            gpus.append({
                "slot": d["slot"],
                "description": d["description"],
                "vendor": _infer_vendor(d["description"]),
            })
    # Détails runtime NVIDIA
    if _has("nvidia-smi"):
        out = _run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version,temperature.gpu",
                    "--format=csv,noheader,nounits"])
        if out:
            for i, line in enumerate(out.strip().splitlines()):
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 4 and i < len(gpus):
                    gpus[i].update({
                        "name": parts[0], "vram_total_mb": parts[1],
                        "driver": parts[2], "temp_c": parts[3],
                    })
    # Détails runtime AMD (rocm-smi)
    if _has("rocm-smi"):
        out = _run(["rocm-smi", "--showuse", "--showmeminfo", "vram", "--showtemp"])
        if out:
            gpus.append({"_rocm": out[:500]})
    return gpus


def _infer_vendor(desc: str) -> str:
    low = desc.lower()
    if "nvidia" in low:
        return "NVIDIA"
    if "amd" in low or "ati" in low or "radeon" in low:
        return "AMD"
    if "intel" in low:
        return "Intel"
    return "inconnu"


# ── NPU ─────────────────────────────────────────────────────────────────

def _npu_info() -> List[Dict[str, Any]]:
    """Accélérateurs neuronaux : /sys/class/accel/* (driver, PCI, device name)."""
    npus: List[Dict[str, Any]] = []
    try:
        accels = sorted(Path("/sys/class/accel").iterdir())
    except Exception:
        accels = []
    for acc in accels:
        dev = acc / "device"
        entry: Dict[str, Any] = {"id": acc.name}
        try:
            entry["driver"] = (dev / "uevent").read_text().split("DRIVER=", 1)[1].splitlines()[0].strip()
        except Exception:
            pass
        try:
            entry["pci_id"] = (dev / "uevent").read_text().split("PCI_ID=", 1)[1].splitlines()[0].strip()
        except Exception:
            pass
        try:
            entry["pci_slot"] = (dev / "uevent").read_text().split("PCI_SLOT_NAME=", 1)[1].splitlines()[0].strip()
        except Exception:
            pass
        try:
            name = (dev / "name").read_text().strip()
            if name:
                entry["name"] = name
        except Exception:
            pass
        try:
            entry["vendor"] = (dev / "vendor").read_text().strip()
        except Exception:
            pass
        npus.append(entry)
    # Fallback : chercher dans lspci les classes "accelerator"/"processing accelerators"
    if not npus:
        for d in _lspci_devices():
            low = d["description"].lower()
            if "accelerator" in low or "co-processor" in low:
                npus.append({"slot": d["slot"], "description": d["description"]})
    return npus


# ── Disques ─────────────────────────────────────────────────────────────

def _disk_info() -> List[Dict[str, Any]]:
    """Disques : lsblk -J -b (JSON) — type, taille, modèle, série, interface."""
    out = _run(["lsblk", "-J", "-b", "-o",
                "NAME,TYPE,SIZE,MODEL,SERIAL,TRAN,ROTA,FSTYPE,MOUNTPOINTS"])
    if not out:
        return []
    try:
        data = json.loads(out)
    except Exception:
        return []
    disks: List[Dict[str, Any]] = []

    def walk(node: Dict[str, Any], parent: Optional[str] = None):
        if node.get("type") == "disk":
            disks.append({
                "name": node.get("name"),
                "size_gb": round((node.get("size") or 0) / (1024 ** 3), 2),
                "model": node.get("model") or "",
                "serial": node.get("serial") or "",
                "interface": node.get("tran") or "",
                "rotational": node.get("rota"),
                "parent": parent,
            })
        for c in node.get("children") or []:
            walk(c, node.get("name") if node.get("type") == "disk" else parent)

    for d in data.get("blockdevices") or []:
        walk(d)
    return disks


# ── Réseau ──────────────────────────────────────────────────────────────

def _net_info() -> List[Dict[str, Any]]:
    """Interfaces réseau : /sys/class/net (MAC, vitesse, état) + type."""
    nets: List[Dict[str, Any]] = []
    try:
        ifaces = sorted(Path("/sys/class/net").iterdir())
    except Exception:
        return []
    for iface in ifaces:
        name = iface.name
        entry: Dict[str, Any] = {"name": name}
        try:
            entry["mac"] = (iface / "address").read_text().strip()
        except Exception:
            pass
        try:
            entry["speed_mbps"] = int((iface / "speed").read_text().strip())
        except Exception:
            entry["speed_mbps"] = None
        try:
            entry["state"] = (iface / "operstate").read_text().strip()
        except Exception:
            pass
        try:
            entry["type"] = _net_type(iface)
        except Exception:
            pass
        try:
            entry["mtu"] = int((iface / "mtu").read_text().strip())
        except Exception:
            pass
        nets.append(entry)
    return nets


def _net_type(iface: Path) -> str:
    name = iface.name
    try:
        dev_id = (iface / "device" / "uevent").read_text()
        if "DRIVER=wl" in dev_id:
            return "wifi"
    except Exception:
        pass
    if name.startswith("wlp") or name.startswith("wlan"):
        return "wifi"
    if name == "lo":
        return "loopback"
    if any(k in name for k in ("docker", "lxc", "veth", "br-", "virbr")):
        return "virtuel"
    return "ethernet"


# ── USB ─────────────────────────────────────────────────────────────────

def _usb_info() -> List[str]:
    """Périphériques USB (lsusb, lignes brutes)."""
    out = _run(["lsusb"])
    if not out:
        return []
    return [l.strip() for l in out.strip().splitlines() if l.strip()]


# ── Températures ────────────────────────────────────────────────────────

def _thermal_info() -> Dict[str, Any]:
    """Températures via lm-sensors (sensors -j) si présent."""
    if not _has("sensors"):
        return {}
    out = _run(["sensors", "-j"])
    if not out:
        return {}
    try:
        return json.loads(out)
    except Exception:
        return {"raw": out[:1000]}


# ── Point d'entrée public ───────────────────────────────────────────────

class HardwareInspector:
    """Inventaire complet et précis du matériel (check système).

    Chaque section est détectée indépendamment ; les sources absentes ou
    nécessitant root sont signalées via `error` au lieu d'échouer.
    """

    def inventory(self) -> Dict[str, Any]:
        return {
            "cpu": _cpu_info(),
            "memory": _memory_info(),
            "motherboard": _motherboard_info(),
            "gpus": _gpu_info(),
            "npus": _npu_info(),
            "disks": _disk_info(),
            "network": _net_info(),
            "usb_count": len(_usb_info()),
            "thermal": _thermal_info(),
        }


def full_inventory() -> Dict[str, Any]:
    """Raccourci : inventaire complet sans instanciation."""
    return HardwareInspector().inventory()


# ── Monitoring runtime (GPU/NPU + bande passante) ────────────────────────

def _read_int(path: Path) -> Optional[int]:
    try:
        return int(Path(path).read_text().strip())
    except Exception:
        return None


def _gpu_runtime() -> List[Dict[str, Any]]:
    """Charge et température des GPUs AMD (amdgpu expose gpu_busy_percent).

    Priorité : nvidia-smi (NVIDIA), puis /sys/class/drm/*/device (AMD/Intel).
    """
    out: List[Dict[str, Any]] = []
    if _has("nvidia-smi"):
        raw = _run(["nvidia-smi", "--query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total",
                    "--format=csv,noheader,nounits"])
        if raw:
            for i, line in enumerate(raw.strip().splitlines()):
                p = [x.strip() for x in line.split(",")]
                if len(p) >= 5:
                    out.append({
                        "name": p[0], "busy_percent": p[1], "temp_c": p[2],
                        "vram_used_mb": p[3], "vram_total_mb": p[4],
                    })
    for card in sorted(Path("/sys/class/drm").glob("card*")):
        dev = card / "device"
        if not dev.exists():
            continue
        busy = _read_int(dev / "gpu_busy_percent")
        if busy is None:
            continue
        entry: Dict[str, Any] = {"busy_percent": busy, "source": "amdgpu"}
        try:
            entry["name"] = (dev / "product_name").read_text().strip()
        except Exception:
            pass
        try:
            for hm in dev.glob("hwmon/hwmon*"):
                t = _read_int(hm / "temp1_input")
                if t is not None:
                    entry["temp_c"] = round(t / 1000, 1)
                    break
        except Exception:
            pass
        try:
            entry["pci_slot"] = (dev / "uevent").read_text().split(
                "PCI_SLOT_NAME=", 1)[1].splitlines()[0].strip()
        except Exception:
            pass
        out.append(entry)
    return out


def _npu_runtime() -> List[Dict[str, Any]]:
    """Présence/état des NPU : /sys/class/accel/* (fw_version, PCI)."""
    out: List[Dict[str, Any]] = []
    try:
        accels = sorted(Path("/sys/class/accel").iterdir())
    except Exception:
        accels = []
    for acc in accels:
        dev = acc / "device"
        entry: Dict[str, Any] = {"id": acc.name}
        try:
            entry["fw_version"] = (dev / "fw_version").read_text().strip()
        except Exception:
            pass
        try:
            entry["driver"] = (dev / "uevent").read_text().split("DRIVER=", 1)[1].splitlines()[0].strip()
        except Exception:
            pass
        try:
            entry["pci_id"] = (dev / "uevent").read_text().split("PCI_ID=", 1)[1].splitlines()[0].strip()
        except Exception:
            pass
        out.append(entry)
    return out


class BandwidthMonitor:
    """Mesure des taux RX/TX par interface réseau via psutil.

    Échantillons pris à chaque appel : taux instantané (depuis le dernier
    échantillon) + taux moyen (depuis le premier échantillon du processus).
    """

    def __init__(self):
        self._last_ts: Optional[float] = None
        self._last: Optional[Dict[str, Any]] = None
        self._first_ts: Optional[float] = None
        self._first: Optional[Dict[str, Any]] = None

    def sample(self) -> Dict[str, Any]:
        import psutil
        counters = psutil.net_io_counters(pernic=True)
        now = time.time()
        rates: List[Dict[str, Any]] = []
        for name, c in sorted(counters.items()):
            rx = c.bytes_recv
            tx = c.bytes_sent
            inst_rx = inst_tx = None
            avg_rx = avg_tx = None
            if self._last and self._last_ts and name in self._last:
                dt = max(now - self._last_ts, 1e-3)
                inst_rx = (rx - self._last[name][0]) / dt
                inst_tx = (tx - self._last[name][1]) / dt
            if self._first and self._first_ts and name in self._first:
                dt = max(now - self._first_ts, 1e-3)
                avg_rx = (rx - self._first[name][0]) / dt
                avg_tx = (tx - self._first[name][1]) / dt
            rates.append({
                "name": name, "rx_bytes": rx, "tx_bytes": tx,
                "rx_bps": round(inst_rx, 1) if inst_rx is not None else None,
                "tx_bps": round(inst_tx, 1) if inst_tx is not None else None,
                "rx_avg_bps": round(avg_rx, 1) if avg_rx is not None else None,
                "tx_avg_bps": round(avg_tx, 1) if avg_tx is not None else None,
            })
        self._last = {r["name"]: (r["rx_bytes"], r["tx_bytes"]) for r in rates}
        self._last_ts = now
        if self._first is None:
            self._first = dict(self._last)
            self._first_ts = now
        return {"interfaces": rates}


_bandwidth_monitor = BandwidthMonitor()


def runtime_sample() -> Dict[str, Any]:
    """Snapshot temps réel : GPU (charge/temp), NPU, bande passante réseau."""
    return {
        "gpus": _gpu_runtime(),
        "npus": _npu_runtime(),
        "network": _bandwidth_monitor.sample(),
        "ts": time.time(),
    }


if __name__ == "__main__":
    print(json.dumps(full_inventory(), indent=2, default=str))
