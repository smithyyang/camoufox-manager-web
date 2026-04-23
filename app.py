import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

import urllib.request
import urllib.parse

from flask import Flask, abort, jsonify, request, render_template

app = Flask(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).parent
PROFILES_DIR = BASE_DIR / "profiles_data"
EXT_ROOT_DIR = BASE_DIR / "extensions"
EXT_XPI_DIR  = EXT_ROOT_DIR / "xpi"
EXT_CRX_DIR  = EXT_ROOT_DIR / "crx"
ENGINE_DEFAULT = "camoufox"
SUPPORTED_ENGINES = {"camoufox", "patchright"}

PROFILES_DIR.mkdir(exist_ok=True)
EXT_ROOT_DIR.mkdir(exist_ok=True)
EXT_XPI_DIR.mkdir(parents=True, exist_ok=True)
EXT_CRX_DIR.mkdir(parents=True, exist_ok=True)

# { profile_id: pid }
running_processes: dict[str, int] = {}
_procs: dict[str, subprocess.Popen] = {}
# { profile_id: file handle } — kept open while browser runs, closed in watcher
_log_files: dict[str, object] = {}

AMO_API = "https://addons.mozilla.org/api/v5"
AMO_HEADERS = {
    "User-Agent": "SpecterForgeManager/1.0 (profile manager; contact@localhost)",
}

# ── Process watcher ───────────────────────────────────────────────────────────
def _watch_process(profile_id: str, proc: subprocess.Popen):
    """Wait for browser to exit, then save open tabs and clean up."""
    proc.wait()

    # Close the log file handle now that the process has exited
    lf = _log_files.pop(profile_id, None)
    try:
        if lf:
            lf.close()
    except Exception:
        pass

    # Read log file — our launch script prints open URLs before exiting
    try:
        log_path = _profile_path(profile_id) / "browser.log"
        if log_path.exists():
            raw = log_path.read_text(errors="replace")
            urls = []
            for line in raw.splitlines():
                line = line.strip()
                if line.startswith("OPEN_URL:"):
                    url = line[len("OPEN_URL:"):].strip()
                    if url and url not in ("about:blank", "about:newtab"):
                        urls.append(url)
            if urls:
                # Persist to meta
                meta_p = _meta_path(profile_id)
                if meta_p.exists():
                    with open(meta_p) as f:
                        meta = json.load(f)
                    meta["last_urls"] = urls
                    meta["updated_at"] = datetime.utcnow().isoformat()
                    with open(meta_p, "w") as f:
                        json.dump(meta, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

    running_processes.pop(profile_id, None)
    _procs.pop(profile_id, None)


# ── Profile helpers ───────────────────────────────────────────────────────────
def _profile_path(profile_id: str) -> Path:
    return PROFILES_DIR / profile_id

def _meta_path(profile_id: str) -> Path:
    return _profile_path(profile_id) / "meta.json"

def _load_meta(profile_id: str) -> dict:
    p = _meta_path(profile_id)
    if not p.exists():
        abort(404, description=f"Profile {profile_id} not found")
    with open(p) as f:
        return _normalize_profile_meta(json.load(f))

def _save_meta(profile_id: str, meta: dict):
    with open(_meta_path(profile_id), "w") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

def _list_profiles() -> list[dict]:
    profiles = []
    for d in PROFILES_DIR.iterdir():
        if d.is_dir():
            meta_file = d / "meta.json"
            if meta_file.exists():
                with open(meta_file) as f:
                    meta = _normalize_profile_meta(json.load(f))
                meta["is_running"] = meta["id"] in running_processes
                profiles.append(meta)
    profiles.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return profiles

def _is_running(profile_id: str) -> bool:
    if profile_id not in running_processes:
        return False
    pid = running_processes[profile_id]
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        del running_processes[profile_id]
        return False


def _normalize_engine(engine: str | None) -> str:
    val = (engine or ENGINE_DEFAULT).strip().lower()
    return val if val in SUPPORTED_ENGINES else ENGINE_DEFAULT


def _normalize_profile_meta(meta: dict) -> dict:
    meta["engine"] = _normalize_engine(meta.get("engine"))
    if "extensions" not in meta or not isinstance(meta.get("extensions"), list):
        meta["extensions"] = []
    return meta


def _engine_ext_suffix(engine: str) -> str:
    return ".crx" if _normalize_engine(engine) == "patchright" else ".xpi"


def _engine_ext_dir(engine: str) -> Path:
    return EXT_CRX_DIR if _normalize_engine(engine) == "patchright" else EXT_XPI_DIR


def _resolve_extension_file(engine: str, filename: str) -> Path | None:
    safe_name = _safe_filename(filename)
    expected_suffix = _engine_ext_suffix(engine)
    if not safe_name.endswith(expected_suffix):
        return None

    primary = _engine_ext_dir(engine) / safe_name
    if primary.exists():
        return primary

    # Backward compatibility: old camoufox installs stored *.xpi under extensions/
    if _normalize_engine(engine) == "camoufox":
        legacy = EXT_ROOT_DIR / safe_name
        if legacy.exists():
            return legacy
    return None


# ── Extension helpers ─────────────────────────────────────────────────────────
def _list_local_extensions(engine: str) -> list[dict]:
    engine = _normalize_engine(engine)
    result = []
    suffix = _engine_ext_suffix(engine)
    candidates: list[Path] = list(_engine_ext_dir(engine).glob(f"*{suffix}"))
    if engine == "camoufox":
        candidates.extend(EXT_ROOT_DIR.glob(f"*{suffix}"))

    # de-dup by filename while preferring the new engine-specific directories
    by_name: dict[str, Path] = {}
    for ext in sorted(candidates):
        by_name[ext.name] = ext

    for ext in sorted(by_name.values(), key=lambda p: p.name.lower()):
        stat = ext.stat()
        result.append({
            "filename": ext.name,
            "size":     stat.st_size,
            "size_kb":  round(stat.st_size / 1024, 1),
            "mtime":    datetime.utcfromtimestamp(stat.st_mtime).isoformat(),
        })
    return result

def _safe_filename(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_\-.]", "_", name)

def _amo_get(path: str, params: dict | None = None) -> dict:
    url = f"{AMO_API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=AMO_HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


_DATACENTER_HINTS = (
    "digitalocean", "amazon", "aws", "google cloud", "oracle cloud", "azure",
    "linode", "vultr", "hetzner", "ovh", "choopa", "m247", "scaleway",
    "contabo", "alibaba cloud", "tencent cloud", "cdn", "hosting", "data center",
)


def _normalize_proxy_url(proxy_url: str, engine: str) -> str:
    proxy = (proxy_url or "").strip()
    if not proxy:
        return ""
    eng = _normalize_engine(engine)
    if eng == "camoufox" and proxy.startswith("socks5://"):
        # camoufox/firefox path: force remote DNS for SOCKS5.
        return proxy.replace("socks5://", "socks5h://", 1)
    if eng == "patchright" and proxy.startswith("socks5h://"):
        # chromium path: playwright/chromium expects socks5://.
        return proxy.replace("socks5h://", "socks5://", 1)
    return proxy


def _proxy_for_curl(proxy_url: str) -> str:
    proxy = (proxy_url or "").strip()
    if proxy.startswith("socks5://"):
        return proxy.replace("socks5://", "socks5h://", 1)
    return proxy


def _is_datacenter_org(org: str) -> bool:
    val = (org or "").strip().lower()
    return any(hint in val for hint in _DATACENTER_HINTS)


def _fetch_ipinfo_via_proxy(proxy_url: str, timeout: int = 15) -> tuple[dict | None, str | None]:
    try:
        cmd = ["curl", "-s", "-m", str(timeout), "--max-time", str(timeout)]
        proxy = _proxy_for_curl(proxy_url)
        if proxy:
            cmd += ["-x", proxy]
        cmd.append("https://ipinfo.io/json")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
        if result.returncode != 0:
            return None, f"curl exit {result.returncode}: {result.stderr.strip()}"
        info = json.loads(result.stdout)
        return info, None
    except json.JSONDecodeError:
        return None, "Invalid response from ipinfo.io"
    except subprocess.TimeoutExpired:
        return None, "Connection timed out"
    except FileNotFoundError:
        return None, "curl not found"
    except Exception as e:
        return None, str(e)


def _proxy_preflight(proxy_url: str) -> tuple[bool, str, dict]:
    if not proxy_url:
        return True, "No proxy configured: launch will use direct network.", {}
    info, err = _fetch_ipinfo_via_proxy(proxy_url, timeout=15)
    if err:
        return True, f"Proxy preflight warning: {err}", {}
    if not info:
        return True, "Proxy preflight warning: empty response", {}

    org = info.get("org", "") or ""
    block_dc = os.environ.get("CM_BLOCK_DATACENTER_PROXY", "0") == "1"
    if block_dc and _is_datacenter_org(org):
        return False, (
            f"Datacenter proxy detected ({org}). "
            "Use residential/mobile proxy or set CM_BLOCK_DATACENTER_PROXY=0 to bypass."
        ), info
    if _is_datacenter_org(org):
        return True, (
            f"Datacenter proxy detected ({org}). "
            "This may increase risk of platform checks."
        ), info
    return True, "", info


# ── Timezone resolver ─────────────────────────────────────────────────────────
def _resolve_timezone_via_curl(proxy_url: str = "") -> str:
    """
    Query ipinfo.io via curl (honouring the profile proxy) to resolve the
    public IP's timezone.  Returns a tz string like "Asia/Tokyo", or "" on
    any failure.  Never raises.
    """
    try:
        cmd = ["curl", "-s", "-m", "10", "--max-time", "10"]
        proxy = _proxy_for_curl(proxy_url)
        if proxy:
            cmd += ["-x", proxy]
        cmd.append("https://ipinfo.io/json")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return ""
        info = json.loads(result.stdout)
        return info.get("timezone", "")
    except Exception:
        return ""

# ── Launch script builder ─────────────────────────────────────────────────────
def _build_camoufox_script(profile_id: str, meta: dict, goto_url: str = "") -> str:
    """
    Generate the Python launch script for this profile.

    goto_url: if non-empty, opens this URL instead of the configured launch_url
              or the remembered last_urls.  Used for "Add Extension → AMO" flow.
    """
    profile_dir = _profile_path(profile_id).resolve()

    os_val  = meta.get("os", "windows")
    locale  = meta.get("locale", "en-US")
    cpu_val      = (meta.get("fingerprint") or {}).get("cpu", "auto")
    timezone_val = (meta.get("fingerprint") or {}).get("timezone", "auto")

    # ── Decide what URLs to open ──────────────────────────────────────────────
    if goto_url:
        # Explicit override (e.g. AMO store URL)
        open_urls = [goto_url]
    else:
        configured = (meta.get("fingerprint") or {}).get("launch_url", "").strip()
        remembered = meta.get("last_urls", [])
        if remembered:
            open_urls = remembered          # restore last session
        elif configured:
            open_urls = [configured]
        else:
            open_urls = ["about:blank"]

    # ── Extensions ────────────────────────────────────────────────────────────
    enabled_exts: list[str] = meta.get("extensions", [])
    ext_paths = []
    for fname in enabled_exts:
        xpi = _resolve_extension_file("camoufox", fname)
        if xpi:
            ext_paths.append(str(xpi.resolve()))

    _OS_FONT_KEY = {"windows": "win", "macos": "mac", "linux": "lin"}
    font_os_key = _OS_FONT_KEY.get(os_val, "win")

    proxy_val = _normalize_proxy_url(meta.get("proxy", ""), "camoufox")

    firefox_prefs = {
        "focusmanager.testmode": False,
        "intl.ime.hack.on_ime_unaware_apps.firefox": False,
        "widget.wayland.text-input.enabled": True,
        "widget.wayland.text-input-v3.enabled": True,
        "network.dns.disablePrefetch": True,
        "network.prefetch-next": False,
        "network.predictor.enabled": False,
        "browser.urlbar.speculativeConnect.enabled": False,
        "network.http.speculative-parallel-limit": 0,
        "network.connectivity-service.enabled": False,
        "network.captive-portal-service.enabled": False,
        "captivedetect.canonicalURL": "",
    }
    if proxy_val:
        firefox_prefs["network.proxy.socks_remote_dns"] = True
        firefox_prefs["network.trr.mode"] = 5
    else:
        # 无代理时启用 TRR-only，避免回落到系统 DNS 造成泄露。
        firefox_prefs["network.trr.mode"] = 3
        firefox_prefs["network.trr.uri"] = "https://mozilla.cloudflare-dns.com/dns-query"
        firefox_prefs["network.trr.custom_uri"] = "https://mozilla.cloudflare-dns.com/dns-query"
        firefox_prefs["network.trr.bootstrapAddr"] = "1.1.1.1"
        firefox_prefs["network.trr.request_timeout_ms"] = 15000

    # ── Build arg_lines ───────────────────────────────────────────────────────
    arg_lines = [
        f'        os="{os_val}"',
        '        humanize=False',
        '        i_know_what_im_doing=True',
        f'        locale=["{locale}"]',
        '        persistent_context=True',
        f'        user_data_dir="{profile_dir}/userdata"',
        '        block_webrtc=True',
        '        fonts=_os_fonts',
        '        custom_fonts_only=True',
        '        env=_browser_env',
    ]
    arg_lines.append(f'        firefox_user_prefs={repr(firefox_prefs)}')

    # geoip=True 会调用内部 public_ip()，走系统直连网络；
    # 在代理环境下极易 InvalidIP 崩溃。改为后端用 curl+代理主动查时区。
    resolved_tz = ""
    if timezone_val == "auto":
        proxy_for_tz = proxy_val
        resolved_tz = _resolve_timezone_via_curl(proxy_for_tz)
        # 不再传 geoip=True，时区通过 config["timezone"] 硬编码

    if ext_paths:
        paths_repr = "[" + ", ".join(f'"{p}"' for p in ext_paths) + "]"
        arg_lines.append(f'        extensions={paths_repr}')

    config_inner = [
        '            "showcursor": False',
        '            "disableTheming": True',
        '            "window.outerWidth": 1920',
        '            "window.outerHeight": 1080',
        '            "window.innerWidth": 1920',
        '            "window.innerHeight": 1032',
        '            "window.screenX": 0',
        '            "window.screenY": 0',
        '            "screen.width": 1920',
        '            "screen.height": 1080',
        '            "screen.availWidth": 1920',
        '            "screen.availHeight": 1032',
    ]
    if cpu_val and cpu_val != "auto":
        config_inner.append(f'            "navigator.hardwareConcurrency": {int(cpu_val)}')
    # 时区优先级：手动选择 > curl 自动解析 > 不传（SpecterForge 默认）
    _tz_to_set = timezone_val if (timezone_val and timezone_val != "auto") else resolved_tz
    if _tz_to_set:
        config_inner.append(f'            "timezone": "{_tz_to_set}"')

    config_block = "        config={\n" + ",\n".join(config_inner) + ",\n        }"
    arg_lines.append(config_block)

    if proxy_val:
        arg_lines.append(f'        proxy="{proxy_val}"')
    arg_lines.append('        headless=False')

    args_str = ",\n".join(arg_lines)

    # ── Build open-tabs code ──────────────────────────────────────────────────
    open_lines = []
    open_lines.append(f'        await page.goto({repr(open_urls[0])})')
    for extra_url in open_urls[1:]:
        open_lines.append(f'        extra = await context.new_page()')
        open_lines.append(f'        await extra.goto({repr(extra_url)})')
    open_tabs_code = "\n".join(open_lines)

    script = f'''#!/usr/bin/env python3
import asyncio
import json
import os
import sys

from camoufox.async_api import AsyncSpecterForge

# ── 跨平台输入法支持 ──────────────────────────────────────────────────────────
_browser_env = dict(os.environ)

if sys.platform.startswith("linux"):
    import shutil as _shutil, subprocess as _sp

    # ── Step 1: 确保 D-Bus / XDG 运行时目录存在（fcitx5 通信必须）────────────
    # fcitx5 通过 D-Bus 与客户端通信。若 Flask 是从没有完整桌面会话的环境启动
    # （如 SSH、某些启动器），这两个变量可能缺失，导致 fcitx5 根本无法建立连接。
    _uid = os.getuid()
    if "XDG_RUNTIME_DIR" not in _browser_env:
        _xdg = "/run/user/" + str(_uid)
        if os.path.exists(_xdg):
            _browser_env["XDG_RUNTIME_DIR"] = _xdg
    if "DBUS_SESSION_BUS_ADDRESS" not in _browser_env:
        _bus = "/run/user/" + str(_uid) + "/bus"
        if os.path.exists(_bus):
            _browser_env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=" + _bus

    # ── Step 2: 探测当前运行的输入法框架 ─────────────────────────────────────
    def _detect_im_name():
        try:
            _running = set(_sp.check_output(
                ["ps", "-e", "-o", "comm="], stderr=_sp.DEVNULL
            ).decode().split())
            if "fcitx5" in _running or "fcitx" in _running: return "fcitx"
            if "ibus-daemon" in _running: return "ibus"
        except Exception:
            pass
        if _shutil.which("fcitx5") or _shutil.which("fcitx"): return "fcitx"
        if _shutil.which("ibus"): return "ibus"
        return None

    def _ime_log(msg: str):
        print("IME_DIAG " + msg, flush=True)

    def _run_cmd_raw(cmd: list[str], timeout: float = 3):
        try:
            res = _sp.run(cmd, capture_output=True, text=True, timeout=timeout)
            out = (res.stdout or "").strip().replace("\\n", "\\\\n")
            err = (res.stderr or "").strip().replace("\\n", "\\\\n")
            return res.returncode, out, err
        except FileNotFoundError:
            return None, "", "missing"
        except Exception as e:
            return None, "", f"error={{type(e).__name__}}:{{e}}"

    def _run_cmd(cmd: list[str], timeout: float = 3) -> str:
        rc, out, err = _run_cmd_raw(cmd, timeout=timeout)
        try:
            if len(out) > 220:
                out = out[:220] + "...(truncated)"
            if len(err) > 220:
                err = err[:220] + "...(truncated)"
        except Exception:
            pass
        return f"rc={{rc}} out={{out}} err={{err}}"

    _im = _detect_im_name() or "fcitx"
    _desktop = str(_browser_env.get("XDG_CURRENT_DESKTOP", "")).lower()
    _is_hyprland = ("HYPRLAND_INSTANCE_SIGNATURE" in _browser_env) or ("hyprland" in _desktop)
    _ime_mode = str(_browser_env.get("CAMOUFOX_IME_MODE", "auto")).strip().lower()
    if _browser_env.get("CAMOUFOX_FORCE_X11_IME", "0") == "1":
        _ime_mode = "x11"
    if _ime_mode not in ("auto", "wayland", "x11"):
        _ime_mode = "auto"
    _has_wayland = "WAYLAND_DISPLAY" in _browser_env
    if _ime_mode == "auto":
        _selected_mode = "x11" if (_is_hyprland and _has_wayland) else ("wayland" if _has_wayland else "x11")
    else:
        _selected_mode = _ime_mode
    _x11_gtk_im = str(_browser_env.get("CAMOUFOX_X11_GTK_IM_MODULE", "fcitx")).strip() or "fcitx"
    _autorestore = _browser_env.get("CAMOUFOX_IME_AUTORESTORE", "1") == "1"

    _fcitx_state_rc, _fcitx_state_out, _ = _run_cmd_raw(["fcitx5-remote"])
    _fcitx_name_rc, _fcitx_name_out, _ = _run_cmd_raw(["fcitx5-remote", "-n"])
    _preferred_im_name = _fcitx_name_out if (_fcitx_name_rc == 0 and _fcitx_name_out) else ""
    _preferred_state = _fcitx_state_out if (_fcitx_state_rc == 0 and _fcitx_state_out) else ""

    _ime_log(
        f"BASE pid={{os.getpid()}} ppid={{os.getppid()}} "
        f"desktop={{_desktop}} session={{_browser_env.get('XDG_SESSION_TYPE')}} "
        f"display={{_browser_env.get('DISPLAY')}} wayland={{_browser_env.get('WAYLAND_DISPLAY')}}"
    )
    _ime_log(
        f"TOOLS fcitx5={{_shutil.which('fcitx5')}} "
        f"fcitx5-remote={{_shutil.which('fcitx5-remote')}} ibus={{_shutil.which('ibus')}}"
    )
    _ime_log(f"CMD fcitx5-remote => {{_run_cmd(['fcitx5-remote'])}}")
    _ime_log(f"CMD fcitx5-remote -n => {{_run_cmd(['fcitx5-remote', '-n'])}}")
    _ime_log(f"CMD fcitx5-remote -q => {{_run_cmd(['fcitx5-remote', '-q'])}}")
    _ime_log(f"CMD fcitx5-remote -a => {{_run_cmd(['fcitx5-remote', '-a'])}}")
    _ime_log(f"CMD fcitx5-remote -m {{_im}} => {{_run_cmd(['fcitx5-remote', '-m', _im])}}")
    _ime_log(f"CMD ibus engine => {{_run_cmd(['ibus', 'engine'])}}")

    _uid_rt = str(os.getuid())
    _ime_log(
        f"PATH /run/user/{{_uid_rt}} exists={{os.path.exists('/run/user/' + _uid_rt)}} "
        f"bus={{os.path.exists('/run/user/' + _uid_rt + '/bus')}} "
        f"x11={{os.path.exists('/tmp/.X11-unix/X0')}} "
        f"wayland_sock={{os.path.exists('/run/user/' + _uid_rt + '/' + str(_browser_env.get('WAYLAND_DISPLAY', '')))}}"
    )
    _ime_log(
        f"MODE requested={{_ime_mode}} selected={{_selected_mode}} "
        f"hyprland={{_is_hyprland}} im={{_im}} x11_gtk_im={{_x11_gtk_im}} "
        f"preferred_name={{_preferred_im_name}} preferred_state={{_preferred_state}} "
        f"autorestore={{_autorestore}}"
    )

    # ── Step 3: 统一设置所有 IM 环境变量 ─────────────────────────────────────
    if _selected_mode == "x11":
        _browser_env.pop("WAYLAND_DISPLAY", None)
        _browser_env["GDK_BACKEND"] = "x11"
        _browser_env["MOZ_ENABLE_WAYLAND"] = "0"
        _browser_env["GTK_IM_MODULE"] = _x11_gtk_im
        _browser_env["XMODIFIERS"] = "@im=" + _im
        _browser_env["QT_IM_MODULE"] = _im
    else:
        _browser_env["MOZ_ENABLE_WAYLAND"] = "1"
        _browser_env.pop("GTK_IM_MODULE", None)
        _browser_env.setdefault("XMODIFIERS", "@im=" + _im)
        _browser_env.setdefault("QT_IM_MODULE", _im)
# ── 输入法支持结束 ────────────────────────────────────────────────────────────

print(
    "IME_DEBUG "
    "MODE=%s DISPLAY=%s WAYLAND_DISPLAY=%s MOZ_ENABLE_WAYLAND=%s GDK_BACKEND=%s "
    "GTK_IM_MODULE=%s QT_IM_MODULE=%s XMODIFIERS=%s DBUS_SESSION_BUS_ADDRESS=%s XDG_RUNTIME_DIR=%s "
    "HYPRLAND=%s PREFERRED_IM=%s PREFERRED_STATE=%s AUTORESTORE=%s"
    % (
        _selected_mode if sys.platform.startswith("linux") else "native",
        _browser_env.get("DISPLAY"),
        _browser_env.get("WAYLAND_DISPLAY"),
        _browser_env.get("MOZ_ENABLE_WAYLAND"),
        _browser_env.get("GDK_BACKEND"),
        _browser_env.get("GTK_IM_MODULE"),
        _browser_env.get("QT_IM_MODULE"),
        _browser_env.get("XMODIFIERS"),
        _browser_env.get("DBUS_SESSION_BUS_ADDRESS"),
        _browser_env.get("XDG_RUNTIME_DIR"),
        _is_hyprland if sys.platform.startswith("linux") else False,
        _preferred_im_name if sys.platform.startswith("linux") else "",
        _preferred_state if sys.platform.startswith("linux") else "",
        _autorestore if sys.platform.startswith("linux") else False,
    ),
    flush=True,
)

# 字体防泄露：从 camoufox 自带的 fonts.json 读取目标 OS 的标准字体列表，
# 配合 custom_fonts_only=True 确保真实系统字体不会泄露给网站。
_camoufox_fonts_path = os.path.join(os.path.dirname(__import__("camoufox").__file__), "fonts.json")
with open(_camoufox_fonts_path, "rb") as _f:
    _os_fonts = json.loads(_f.read()).get("{font_os_key}", [])

async def main():
    async with AsyncSpecterForge(
{args_str},
    ) as context:
        pages = context.pages
        page = pages[0] if pages else await context.new_page()
{open_tabs_code}
        try:
            print(f"IME_DIAG RUNTIME pages={{len(context.pages)}} current_url={{page.url}}", flush=True)
        except Exception as e:
            print(f"IME_DIAG RUNTIME page_state_error={{type(e).__name__}}:{{e}}", flush=True)

        if sys.platform.startswith("linux"):
            try:
                import subprocess as _sp2

                def _rt_cmd(cmd):
                    try:
                        r = _sp2.run(cmd, capture_output=True, text=True, timeout=3)
                        return r.returncode, (r.stdout or "").strip().replace("\\n", "\\\\n"), (r.stderr or "").strip().replace("\\n", "\\\\n")
                    except FileNotFoundError:
                        return None, "", "missing"
                    except Exception as ex:
                        return None, "", f"error={{type(ex).__name__}}:{{ex}}"

                def _rt_log(prefix):
                    s_rc, s_out, s_err = _rt_cmd(["fcitx5-remote"])
                    n_rc, n_out, n_err = _rt_cmd(["fcitx5-remote", "-n"])
                    q_rc, q_out, q_err = _rt_cmd(["fcitx5-remote", "-q"])
                    print(
                        "IME_DIAG RUNTIME %s state(rc=%s,out=%s,err=%s) name(rc=%s,out=%s,err=%s) group(rc=%s,out=%s,err=%s)"
                        % (prefix, s_rc, s_out, s_err, n_rc, n_out, n_err, q_rc, q_out, q_err),
                        flush=True,
                    )
                    return s_rc, s_out, n_rc, n_out

                _state_rc, _state_out, _name_rc, _name_out = _rt_log("t+0s")

                if _autorestore and _preferred_im_name:
                    _state_now = _state_out if _state_rc == 0 else ""
                    _name_now = _name_out if _name_rc == 0 else ""
                    if (_state_now != "2") or (_name_now != _preferred_im_name):
                        _sw_rc, _sw_out, _sw_err = _rt_cmd(["fcitx5-remote", "-s", _preferred_im_name])
                        _on_rc, _on_out, _on_err = _rt_cmd(["fcitx5-remote", "-o"])
                        print(
                            "IME_DIAG RUNTIME autorestore switch(rc=%s,out=%s,err=%s) activate(rc=%s,out=%s,err=%s)"
                            % (_sw_rc, _sw_out, _sw_err, _on_rc, _on_out, _on_err),
                            flush=True,
                        )
                        _rt_log("after-autorestore")

                async def _ime_watchdog():
                    _elapsed = 0
                    for _delay in (1, 1, 1, 2, 3, 5):
                        await asyncio.sleep(_delay)
                        _elapsed += _delay
                        _rt_log(f"t+{{_elapsed}}s")

                asyncio.create_task(_ime_watchdog())
            except Exception as e:
                print(f"IME_DIAG RUNTIME fcitx5_remote_error={{type(e).__name__}}:{{e}}", flush=True)

        print("CAMOUFOX_READY", flush=True)

        async def _on_close():
            for p in context.pages:
                try:
                    print(f"OPEN_URL:{{p.url}}", flush=True)
                except Exception:
                    pass
        context.on("close", lambda: asyncio.ensure_future(_on_close()))

        # timeout=0 = 无限等待，不传则默认 30 秒后抛 TimeoutError
        # 导致 async with 块退出 → context manager 关闭浏览器 → 窗口闪退
        await context.wait_for_event("close", timeout=0)

asyncio.run(main())
'''
    return script


def _parse_screen_to_viewport(screen_val: str) -> tuple[int, int]:
    m = re.match(r"^\s*(\d{3,5})x(\d{3,5})\s*$", str(screen_val or ""))
    if not m:
        return 1920, 1080
    return int(m.group(1)), int(m.group(2))


def _build_patchright_script(profile_id: str, meta: dict, goto_url: str = "") -> str:
    profile_dir = _profile_path(profile_id).resolve()
    locale = meta.get("locale", "en-US")
    fp = meta.get("fingerprint") or {}
    screen = fp.get("screen", "1920x1080")
    timezone_val = fp.get("timezone", "auto")
    width, height = _parse_screen_to_viewport(screen)

    if goto_url:
        open_urls = [goto_url]
    else:
        configured = (fp.get("launch_url") or "").strip()
        remembered = meta.get("last_urls", [])
        if remembered:
            open_urls = remembered
        elif configured:
            open_urls = [configured]
        else:
            open_urls = ["about:blank"]

    ext_paths = []
    for fname in meta.get("extensions", []):
        crx = _resolve_extension_file("patchright", fname)
        if crx:
            ext_paths.append(str(crx.resolve()))

    chromium_args: list[str] = [
        "--disable-background-networking",
        "--disable-domain-reliability",
        "--disable-quic",
        "--dns-prefetch-disable",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    ]
    if ext_paths:
        joined = ",".join(ext_paths)
        chromium_args.extend([
            f"--disable-extensions-except={joined}",
            f"--load-extension={joined}",
        ])

    proxy_val = _normalize_proxy_url(meta.get("proxy", ""), "patchright")
    dns_mode = "proxy" if proxy_val else "doh"
    if proxy_val:
        chromium_args.append("--disable-features=AsyncDns,DnsOverHttps,UseDnsHttpsSvcb,WebRtcHideLocalIpsWithMdns")
        parsed_proxy = urllib.parse.urlparse(proxy_val)
        proxy_host = (parsed_proxy.hostname or "").strip()
        resolver_rule = "MAP * ~NOTFOUND,EXCLUDE localhost"
        if proxy_host:
            resolver_rule += f",EXCLUDE {proxy_host}"
        chromium_args.extend([
            "--proxy-bypass-list=<-loopback>",
            f"--host-resolver-rules={resolver_rule}",
        ])
    else:
        chromium_args.append("--disable-features=WebRtcHideLocalIpsWithMdns")
        chromium_args.extend([
            "--enable-features=AsyncDns,DnsOverHttps,UseDnsHttpsSvcb",
            "--dns-over-https-mode=secure",
            "--dns-over-https-templates=https://chrome.cloudflare-dns.com/dns-query{?dns}",
        ])

    resolved_tz = ""
    if timezone_val == "auto":
        resolved_tz = _resolve_timezone_via_curl(proxy_val)
    timezone_to_set = timezone_val if (timezone_val and timezone_val != "auto") else resolved_tz

    script = f'''#!/usr/bin/env python3
import asyncio
import os
import subprocess
import sys

try:
    from patchright.async_api import async_playwright
except Exception as e:
    print(f"PATCHRIGHT_IMPORT_ERROR: {{e}}", flush=True)
    raise

_browser_env = dict(os.environ)
_preferred_im_name = ""
_preferred_state = ""
_autorestore = _browser_env.get("CAMOUFOX_IME_AUTORESTORE", "1") == "1"

if sys.platform.startswith("linux"):
    if "XDG_RUNTIME_DIR" not in _browser_env:
        _xdg = "/run/user/" + str(os.getuid())
        if os.path.exists(_xdg):
            _browser_env["XDG_RUNTIME_DIR"] = _xdg
    if "DBUS_SESSION_BUS_ADDRESS" not in _browser_env:
        _bus = "/run/user/" + str(os.getuid()) + "/bus"
        if os.path.exists(_bus):
            _browser_env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=" + _bus

    def _detect_im_name():
        try:
            _running = set(subprocess.check_output(
                ["ps", "-e", "-o", "comm="], stderr=subprocess.DEVNULL
            ).decode().split())
            if "fcitx5" in _running or "fcitx" in _running:
                return "fcitx"
            if "ibus-daemon" in _running:
                return "ibus"
        except Exception:
            pass
        return "fcitx"

    _im = _detect_im_name()
    _browser_env.setdefault("XMODIFIERS", "@im=" + _im)
    _browser_env.setdefault("QT_IM_MODULE", _im)
    if "WAYLAND_DISPLAY" in _browser_env:
        _browser_env.setdefault("MOZ_ENABLE_WAYLAND", "1")
    else:
        _browser_env.setdefault("GTK_IM_MODULE", "fcitx")

    def _fcitx(cmd):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
        except Exception as ex:
            return None, "", f"{{type(ex).__name__}}:{{ex}}"

    _s_rc, _s_out, _ = _fcitx(["fcitx5-remote"])
    _n_rc, _n_out, _ = _fcitx(["fcitx5-remote", "-n"])
    if _s_rc == 0:
        _preferred_state = _s_out
    if _n_rc == 0:
        _preferred_im_name = _n_out

    print(
        "IME_DEBUG MODE=patchright DISPLAY=%s WAYLAND_DISPLAY=%s GTK_IM_MODULE=%s QT_IM_MODULE=%s XMODIFIERS=%s PREFERRED_IM=%s PREFERRED_STATE=%s"
        % (
            _browser_env.get("DISPLAY"),
            _browser_env.get("WAYLAND_DISPLAY"),
            _browser_env.get("GTK_IM_MODULE"),
            _browser_env.get("QT_IM_MODULE"),
            _browser_env.get("XMODIFIERS"),
            _preferred_im_name,
            _preferred_state,
        ),
        flush=True,
    )


async def main():
    _chromium_args = {repr(chromium_args)}
    _proxy_server = {repr(proxy_val)}
    _dns_mode = {repr(dns_mode)}
    _timezone_id = {repr(timezone_to_set)}

    async with async_playwright() as p:
        ctx_kwargs = dict(
            user_data_dir={repr(str(profile_dir / "userdata"))},
            headless=False,
            locale={repr(locale)},
            viewport={{"width": {width}, "height": {height}}},
            env=_browser_env,
            args=_chromium_args,
        )
        if _proxy_server:
            ctx_kwargs["proxy"] = {{"server": _proxy_server}}
        if _timezone_id:
            ctx_kwargs["timezone_id"] = _timezone_id

        print(
            f"DNS_DEBUG mode={{_dns_mode}} proxy={{bool(_proxy_server)}} args={{_chromium_args}}",
            flush=True,
        )
        context = await p.chromium.launch_persistent_context(**ctx_kwargs)

        pages = context.pages
        page = pages[0] if pages else await context.new_page()
        await page.goto({repr(open_urls[0])})
        for extra_url in {repr(open_urls[1:])}:
            extra = await context.new_page()
            await extra.goto(extra_url)

        if sys.platform.startswith("linux") and _autorestore and _preferred_im_name:
            try:
                def _fcitx(cmd):
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
                    return r.returncode, (r.stdout or "").strip()
                _state_rc, _state_out = _fcitx(["fcitx5-remote"])
                _name_rc, _name_out = _fcitx(["fcitx5-remote", "-n"])
                if (_state_rc == 0 and _state_out != "2") or (_name_rc == 0 and _name_out != _preferred_im_name):
                    subprocess.run(["fcitx5-remote", "-s", _preferred_im_name], timeout=3)
                    subprocess.run(["fcitx5-remote", "-o"], timeout=3)
                    print("IME_DIAG RUNTIME autorestore done", flush=True)
            except Exception as e:
                print(f"IME_DIAG RUNTIME autorestore_error={{e}}", flush=True)

        print("PATCHRIGHT_READY", flush=True)

        async def _on_close():
            for p in context.pages:
                try:
                    print(f"OPEN_URL:{{p.url}}", flush=True)
                except Exception:
                    pass
        context.on("close", lambda: asyncio.ensure_future(_on_close()))
        await context.wait_for_event("close", timeout=0)

asyncio.run(main())
'''
    return script


_ENGINE_SCRIPT_BUILDERS: dict[str, Callable[[str, dict, str], str]] = {
    "camoufox": _build_camoufox_script,
    "patchright": _build_patchright_script,
}


def _build_launch_script(profile_id: str, meta: dict, goto_url: str = "") -> str:
    engine = _normalize_engine(meta.get("engine"))
    builder = _ENGINE_SCRIPT_BUILDERS.get(engine, _build_camoufox_script)
    return builder(profile_id, meta, goto_url=goto_url)


# ── Routes: UI ────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


# ── Routes: Profiles API ──────────────────────────────────────────────────────
@app.route("/api/profiles", methods=["GET"])
def list_profiles():
    profiles = _list_profiles()
    for p in profiles:
        p["is_running"] = _is_running(p["id"])
    return jsonify(profiles)


@app.route("/api/profiles", methods=["POST"])
def create_profile():
    data = request.get_json(force=True)
    profile_id = str(uuid.uuid4())[:8]

    profile_dir = _profile_path(profile_id)
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "userdata").mkdir(exist_ok=True)

    meta = {
        "id":         profile_id,
        "name":       data.get("name", f"Profile {profile_id}"),
        "engine":     _normalize_engine(data.get("engine")),
        "os":         data.get("os", "windows"),
        "locale":     data.get("locale", "en-US"),
        "proxy":      data.get("proxy", ""),
        "tags":       data.get("tags", []),
        "notes":      data.get("notes", ""),
        "extensions": [],
        "last_urls":  [],          # session memory
        "fingerprint": {
            "screen": data.get("screen", "1920x1080"),
        },
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }

    _save_meta(profile_id, meta)
    return jsonify(meta), 201


@app.route("/api/profiles/<profile_id>", methods=["GET"])
def get_profile(profile_id):
    meta = _load_meta(profile_id)
    meta["is_running"] = _is_running(profile_id)
    return jsonify(meta)


@app.route("/api/profiles/<profile_id>", methods=["PUT"])
def update_profile(profile_id):
    meta = _load_meta(profile_id)
    data = request.get_json(force=True)

    updatable = [
        "name", "engine", "os", "locale", "proxy", "tags", "notes",
        "fingerprint", "screen", "gpu", "cpu", "timezone", "launch_url",
        "extensions", "last_urls",
    ]
    for key in updatable:
        if key in data:
            if key == "engine":
                meta["engine"] = _normalize_engine(data[key])
                continue
            if key in ("screen", "gpu", "cpu", "timezone", "launch_url"):
                meta.setdefault("fingerprint", {})[key] = data[key]
            else:
                meta[key] = data[key]

    # Ensure extensions are compatible with current engine
    engine = _normalize_engine(meta.get("engine"))
    cleaned_exts = []
    for name in meta.get("extensions", []):
        safe = _safe_filename(name)
        if _resolve_extension_file(engine, safe):
            cleaned_exts.append(safe)
    meta["extensions"] = cleaned_exts

    meta["updated_at"] = datetime.utcnow().isoformat()
    _save_meta(profile_id, meta)
    return jsonify(meta)


@app.route("/api/profiles/<profile_id>", methods=["DELETE"])
def delete_profile(profile_id):
    if _is_running(profile_id):
        return jsonify({"error": "Profile is running. Stop it first."}), 400

    profile_dir = _profile_path(profile_id)
    if profile_dir.exists():
        shutil.rmtree(profile_dir)

    return jsonify({"ok": True})


# ── Routes: Extensions ────────────────────────────────────────────────────────
@app.route("/api/extensions", methods=["GET"])
def list_extensions():
    engine = _normalize_engine(request.args.get("engine"))
    return jsonify(_list_local_extensions(engine))


@app.route("/api/extensions/search", methods=["POST"])
def search_extensions():
    data  = request.get_json(force=True)
    query = (data.get("q") or "").strip()
    page  = int(data.get("page", 1))
    engine = _normalize_engine(data.get("engine"))

    if engine != "camoufox":
        return jsonify({"error": "Store search is only available for camoufox (.xpi)."}), 400

    if not query:
        return jsonify({"error": "q is required"}), 400

    try:
        resp = _amo_get("/addons/search/", {
            "q":         query,
            "type":      "extension",
            "sort":      "users",
            "page":      page,
            "page_size": 12,
            "lang":      "en-US",
            "app":       "firefox",
        })
    except Exception as e:
        return jsonify({"error": f"AMO API error: {e}"}), 502

    addons = []
    for item in resp.get("results", []):
        current_version = item.get("current_version") or {}
        files = current_version.get("files") or []
        download_url = next(
            (f.get("url") for f in files if (f.get("url") or "").endswith(".xpi")),
            None
        )
        if not download_url:
            continue

        slug     = item.get("slug", "")
        version  = current_version.get("version", "")
        filename = _safe_filename(f"{slug}-{version}.xpi")
        already  = (_engine_ext_dir("camoufox") / filename).exists() or (EXT_ROOT_DIR / filename).exists()

        icon_url = next(
            (item.get("icons", {}).get(s) for s in ("64", "32", "16") if item.get("icons", {}).get(s)),
            None
        )

        addons.append({
            "addon_id":      item.get("guid") or item.get("id"),
            "slug":          slug,
            "name":          (item.get("name") or {}).get("en-US", slug),
            "summary":       (item.get("summary") or {}).get("en-US", ""),
            "icon":          icon_url,
            "version":       version,
            "users":         item.get("average_daily_users", 0),
            "rating":        item.get("ratings", {}).get("average", 0),
            "download_url":  download_url,
            "filename":      filename,
            "already_local": already,
        })

    return jsonify({"count": resp.get("count", 0), "page": page, "results": addons})


@app.route("/api/extensions/install", methods=["POST"])
def install_extension():
    data         = request.get_json(force=True)
    download_url = (data.get("download_url") or "").strip()
    filename     = (data.get("filename") or "").strip()
    engine       = _normalize_engine(data.get("engine"))

    if not download_url or not filename:
        return jsonify({"error": "download_url and filename are required"}), 400

    if engine != "camoufox":
        return jsonify({"error": "Store install is only available for camoufox (.xpi)."}), 400

    filename = _safe_filename(filename)
    if not filename.endswith(".xpi"):
        filename += ".xpi"

    dest = EXT_XPI_DIR / filename
    if dest.exists():
        return jsonify({"ok": True, "filename": filename, "cached": True})

    parsed = urllib.parse.urlparse(download_url)
    allowed_hosts = {
        "addons.mozilla.org",
        "addons.cdn.mozilla.net",
        "addons-amo-cdn.prod.webservices.mozgcp.net",
    }
    if parsed.hostname not in allowed_hosts:
        return jsonify({"error": "URL not from an allowed AMO host"}), 400

    try:
        req = urllib.request.Request(download_url, headers=AMO_HEADERS)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data_bytes = resp.read()
        if data_bytes[:2] != b"PK":
            return jsonify({"error": "Downloaded file is not a valid .xpi"}), 502
        dest.write_bytes(data_bytes)
    except Exception as e:
        return jsonify({"error": f"Download failed: {e}"}), 502

    return jsonify({"ok": True, "filename": filename, "cached": False, "size": dest.stat().st_size})


@app.route("/api/extensions/<filename>", methods=["DELETE"])
def delete_extension(filename: str):
    engine = _normalize_engine(request.args.get("engine"))
    filename = _safe_filename(filename)
    dest = _resolve_extension_file(engine, filename)
    if not dest:
        suffix = _engine_ext_suffix(engine)
        if not filename.endswith(suffix):
            filename += suffix
        dest = _resolve_extension_file(engine, filename)
    if not dest or not dest.exists():
        return jsonify({"error": "File not found"}), 404

    dest.unlink()

    for d in PROFILES_DIR.iterdir():
        meta_file = d / "meta.json"
        if meta_file.exists():
            with open(meta_file) as f:
                meta = _normalize_profile_meta(json.load(f))
            if _normalize_engine(meta.get("engine")) == engine and filename in meta.get("extensions", []):
                meta["extensions"] = [e for e in meta["extensions"] if e != filename]
                meta["updated_at"] = datetime.utcnow().isoformat()
                with open(meta_file, "w") as f:
                    json.dump(meta, f, indent=2, ensure_ascii=False)

    return jsonify({"ok": True})


@app.route("/api/extensions/<filename>/profiles", methods=["GET"])
def extension_profiles(filename: str):
    engine = _normalize_engine(request.args.get("engine"))
    filename = _safe_filename(filename)
    using = []
    for d in PROFILES_DIR.iterdir():
        meta_file = d / "meta.json"
        if meta_file.exists():
            with open(meta_file) as f:
                meta = _normalize_profile_meta(json.load(f))
            if _normalize_engine(meta.get("engine")) == engine and filename in meta.get("extensions", []):
                using.append({"id": meta["id"], "name": meta.get("name", meta["id"])})
    return jsonify(using)


@app.route("/api/profiles/<profile_id>/extensions", methods=["PUT"])
def update_profile_extensions(profile_id: str):
    meta  = _load_meta(profile_id)
    data  = request.get_json(force=True)
    names = data.get("extensions", [])
    engine = _normalize_engine(data.get("engine") or meta.get("engine"))

    valid = []
    for n in names:
        safe = _safe_filename(n)
        if _resolve_extension_file(engine, safe):
            valid.append(safe)
    meta["extensions"] = valid
    meta["updated_at"] = datetime.utcnow().isoformat()
    _save_meta(profile_id, meta)
    return jsonify({"ok": True, "extensions": valid})


# ── Routes: Session memory ────────────────────────────────────────────────────
@app.route("/api/profiles/<profile_id>/last-urls", methods=["DELETE"])
def clear_last_urls(profile_id: str):
    """Clear the remembered session URLs for this profile."""
    meta = _load_meta(profile_id)
    meta["last_urls"] = []
    meta["updated_at"] = datetime.utcnow().isoformat()
    _save_meta(profile_id, meta)
    return jsonify({"ok": True})


# ── Routes: Proxy test ────────────────────────────────────────────────────────
@app.route("/api/proxy-test", methods=["POST"])
def proxy_test():
    data      = request.get_json(force=True)
    proxy_url = (data.get("proxy") or "").strip()
    engine    = _normalize_engine(data.get("engine"))
    proxy_url = _normalize_proxy_url(proxy_url, engine)

    try:
        t0 = time.time()
        info, err = _fetch_ipinfo_via_proxy(proxy_url, timeout=15)
        if err:
            return jsonify({"ok": False, "error": err})
        if not info:
            return jsonify({"ok": False, "error": "Empty response from IP detection service"})
        latency_ms = int((time.time() - t0) * 1000)
        org = info.get("org", "") or ""

        return jsonify({
            "ok":      True,
            "ip":      info.get("ip",     "?"),
            "city":    info.get("city",   "?"),
            "region":  info.get("region", "?"),
            "country": info.get("country","?"),
            "org":     org,
            "latency": latency_ms,
            "proxy": bool(proxy_url),
            "proxy_quality": "datacenter" if _is_datacenter_org(org) else "residential_like",
            "datacenter_detected": _is_datacenter_org(org),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ── Routes: Launch / Stop ─────────────────────────────────────────────────────
@app.route("/api/profiles/<profile_id>/launch", methods=["POST"])
def launch_profile(profile_id):
    if _is_running(profile_id):
        return jsonify({"error": "Already running"}), 400

    data     = request.get_json(force=True) if request.content_length else {}
    # Optional: caller may pass { "goto_url": "https://..." } to override open URL
    goto_url = (data.get("goto_url") or "").strip() if data else ""

    meta = _load_meta(profile_id)
    engine = _normalize_engine(meta.get("engine"))
    proxy_url = _normalize_proxy_url(meta.get("proxy", ""), engine)
    require_proxy = os.environ.get("CM_REQUIRE_PROXY", "0") == "1"
    proxy_warning = ""
    proxy_org = ""

    if require_proxy and not proxy_url:
        return jsonify({
            "error": (
                "Proxy is required for anti-detect launch. "
                "Configure a proxy in profile settings, or set CM_REQUIRE_PROXY=0 to bypass."
            )
        }), 400

    if proxy_url:
        ok, err, info = _proxy_preflight(proxy_url)
        if not ok:
            return jsonify({"error": err, "proxy_org": (info or {}).get("org", "")}), 400
        proxy_warning = err
        proxy_org = (info or {}).get("org", "")
    else:
        proxy_warning = "No proxy configured: launch is using direct network."

    meta_launch = dict(meta)
    meta_launch["proxy"] = proxy_url
    script_content = _build_launch_script(profile_id, meta_launch, goto_url=goto_url)

    script_path = _profile_path(profile_id) / "launch.py"
    with open(script_path, "w") as f:
        f.write(script_content)

    # ── FIX: redirect stdout+stderr to a log file instead of PIPE ────────────
    # Using subprocess.PIPE causes the 64 KB OS pipe buffer to fill up from
    # Firefox/camoufox log output. Since _watch_process only reads the pipe
    # *after* the process exits, the buffer eventually blocks the child's
    # write() calls, freezing the asyncio event loop inside launch.py, which
    # causes camoufox to kill itself — manifesting as a sudden window crash.
    # Writing to a file has no buffer limit and never blocks.
    log_path = _profile_path(profile_id) / "browser.log"

    # ── 构建子进程环境 ─────────────────────────────────────────────────────────
    launch_env = os.environ.copy()

    if os.name != "nt":
        if "DISPLAY" not in launch_env and "WAYLAND_DISPLAY" not in launch_env:
            launch_env["DISPLAY"] = ":0"   # 兜底，避免无头模式被意外触发

        # fcitx5 / ibus 需要 XDG_RUNTIME_DIR 和 DBUS_SESSION_BUS_ADDRESS 才能通信。
        # 若 Flask 从不完整的桌面会话启动（如部分启动器），这两个变量可能缺失；
        # 按 systemd/logind 标准路径主动补全。
        uid = os.getuid()
        if "XDG_RUNTIME_DIR" not in launch_env:
            xdg = f"/run/user/{uid}"
            if os.path.exists(xdg):
                launch_env["XDG_RUNTIME_DIR"] = xdg
        if "DBUS_SESSION_BUS_ADDRESS" not in launch_env:
            bus = f"/run/user/{uid}/bus"
            if os.path.exists(bus):
                launch_env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"
    # ── 环境变量构建结束 ──────────────────────────────────────────────────────

    try:
        log_file = open(log_path, "w")

        proc = subprocess.Popen(
            ["python3", str(script_path)],
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
            env=launch_env,
        )

        time.sleep(1.5)
        if proc.poll() is not None:
            log_file.close()
            # Read whatever was logged to get the error detail
            detail = "Unknown error"
            try:
                detail = log_path.read_text(errors="replace").strip() or detail
            except Exception:
                pass
            return jsonify({"error": f"Browser failed to start: {detail}"}), 500

        running_processes[profile_id] = proc.pid
        _procs[profile_id]            = proc
        _log_files[profile_id]        = log_file  # watcher will close it on exit

        threading.Thread(
            target=_watch_process, args=(profile_id, proc), daemon=True
        ).start()
        payload = {"ok": True, "pid": proc.pid}
        if proxy_warning:
            payload["warning"] = proxy_warning
        if proxy_org:
            payload["proxy_org"] = proxy_org
        return jsonify(payload)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/profiles/<profile_id>/stop", methods=["POST"])
def stop_profile(profile_id):
    if not _is_running(profile_id):
        return jsonify({"error": "Not running"}), 400

    pid = running_processes[profile_id]
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except Exception:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    del running_processes[profile_id]
    _procs.pop(profile_id, None)
    return jsonify({"ok": True})


@app.route("/api/profiles/<profile_id>/status", methods=["GET"])
def profile_status(profile_id):
    return jsonify({"is_running": _is_running(profile_id)})


@app.route("/api/running", methods=["GET"])
def all_running():
    alive = {pid: _is_running(pid) for pid in list(running_processes)}
    return jsonify({k: v for k, v in alive.items() if v})


# ── Routes: Zombie killer ─────────────────────────────────────────────────────
@app.route("/api/zombie-kill", methods=["POST"])
def zombie_kill():
    """Kill untracked camoufox/firefox processes."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "camoufox"],
            capture_output=True, text=True
        )
        tracked_pids = set(running_processes.values())
        killed = []
        for line in result.stdout.splitlines():
            try:
                pid = int(line.strip())
                if pid not in tracked_pids:
                    os.kill(pid, signal.SIGTERM)
                    killed.append(pid)
            except Exception:
                pass
        return jsonify({"ok": True, "killed": killed})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    print("👻 SpecterForge Manager running at http://localhost:7070")
    app.run(host="127.0.0.1", port=7070, debug=False)
