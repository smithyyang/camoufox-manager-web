# 👻 SpecterForge WRB  
**Web Remote Browser Manager for Stealth Browser Environments**

SpecterForge WRB is a lightweight, local-first web manager for creating and controlling isolated browser environments with advanced fingerprint customization.

It provides a unified interface to orchestrate multiple anti-detect browser engines.

---

## ✨ Features

### 🧩 Profile Management
- Unlimited local profiles (no accounts, no cloud sync)
- Fully isolated environments (cookies, cache, storage)
- Automatic session restore (reopen previous tabs)

### 🛡 Fingerprint Control
- OS spoofing (Windows / macOS / Linux)
- Custom hardware fingerprint:
  - Screen resolution
  - CPU cores (`hardwareConcurrency`)
  - WebGL vendor & renderer
- Font whitelist system (prevents leaking real system fonts)

### ⚙️ Multi-Engine Architecture
- Dual engine support:
  - Firefox (Camoufox)
  - Chromium (Patchright)
- Per-profile engine selection
- Engine-isolated extension system:
  - `extensions/xpi/` (Firefox)
  - `extensions/crx/` (Chromium)

### 🌐 Networking & Privacy
- Per-profile proxy support:
  - HTTP / SOCKS4 / SOCKS5
- Automatic DNS leak prevention:
  - Converts `socks5 → socks5h`
- DNS-over-HTTPS fallback
- Built-in proxy tester:
  - Latency
  - Geo location
  - ISP detection

### 🔌 Extension System
- Built-in Firefox Add-ons (AMO)
- Per-profile enable / disable

### 🧠 Stability & Process Control
- Real-time status tracking
- Async logging (`browser.log`)
- Zombie process cleanup

---

## 📂 Project Structure

```text
specterforge/
├── app.py
├── requirements.txt
├── templates/
│   └── index.html
├── extensions/
│   ├── xpi/
│   └── crx/
└── profiles_data/
🚀 Getting Started
1. Clone & Install
git clone https://github.com/smithyyang/specterforge.git
cd specterforge

python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
2. Install Browsers
python3 -m camoufox fetch
pip install patchright
patchright install chromium
3. Run
python3 app.py

Open: http://127.0.0.1:7070

⚠️ Notes
Local access only (127.0.0.1)
Use separate proxies per profile
Engines may behave differently across sites
📄 License

MIT License
