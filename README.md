👻 SpecterForge WRB

Web Remote Browser Manager for Stealth Browser Environments

SpecterForge WRB is a lightweight, local-first web manager for creating and controlling isolated browser environments with advanced fingerprint customization.

It provides a unified interface to orchestrate multiple anti-detect browser engines, enabling flexible workflows for privacy, testing, and multi-session management.

✨ Features
🧩 Profile Management
Unlimited local profiles (no accounts, no cloud sync)
Fully isolated environments (cookies, cache, storage)
Automatic session restore (reopen previous tabs)
🛡 Fingerprint Control
OS spoofing (Windows / macOS / Linux)
Custom hardware fingerprint:
Screen resolution
CPU cores (hardwareConcurrency)
WebGL vendor & renderer
Font whitelist system (prevents leaking real system fonts)
⚙️ Multi-Engine Architecture
Dual engine support:
Firefox (Camoufox)
Chromium (Patchright)
Per-profile engine selection
Unified backend adapter for different engines
Engine-isolated extension system:
extensions/xpi/ (Firefox)
extensions/crx/ (Chromium)
🌐 Networking & Privacy
Per-profile proxy support:
HTTP / SOCKS4 / SOCKS5
Automatic DNS leak prevention:
Converts socks5 → socks5h
DNS-over-HTTPS fallback (when no proxy is set)
Built-in proxy tester:
Latency
Geo location
ISP detection
Optional strict mode:
Enforce proxy usage
Block datacenter IPs
🔌 Extension System
Built-in Firefox Add-ons (AMO) integration
Install extensions directly from dashboard
Per-profile enable / disable control
🧠 Stability & Process Control
Real-time profile status tracking
Async logging (browser.log) to prevent crashes
Zombie process cleanup (one-click)
📂 Project Structure
specterforge/
├── app.py                # Flask backend (routing + engine adapter)
├── requirements.txt
├── templates/
│   └── index.html        # Web dashboard UI
├── extensions/
│   ├── xpi/              # Firefox extensions
│   └── crx/              # Chromium extensions
└── profiles_data/        # Runtime data (auto-generated)
    └── <profile_id>/
        ├── meta.json
        ├── browser.log
        └── userdata/
🚀 Getting Started
1. Clone & Install
git clone https://github.com/smithyyang/specterforge.git
cd specterforge

python3 -m venv venv
source venv/bin/activate   # Windows: .\venv\Scripts\activate

pip install -r requirements.txt
2. Install Browser Engines
Firefox (Camoufox)
python3 -m camoufox fetch
Chromium (Patchright)
pip install patchright
patchright install chromium
3. Run
python3 app.py

Open in browser:

http://127.0.0.1:7070
⚠️ Notes
Local Access Only

The web dashboard binds to 127.0.0.1 by default and is not exposed externally.

Proxy Recommendation

Use a different high-quality proxy per profile to avoid IP correlation.

Strict Mode (Optional)
CM_REQUIRE_PROXY=1
CM_BLOCK_DATACENTER_PROXY=1
Engine Compatibility

Different websites may behave differently across engines.
Switch between Firefox and Chromium if you encounter blocks or captchas.

Project Status

SpecterForge relies on actively evolving browser kernels.
Expect occasional updates or minor instability.

📄 License

MIT License

🧠 Design Philosophy

SpecterForge follows a local-first and engine-agnostic design:

No accounts, no remote control layer
No forced cloud infrastructure
Unified abstraction over multiple browser engines
Focus on practical, controllable environments instead of SaaS workflows

⭐ Contributing

Contributions, issues, and feature requests are welcome.
