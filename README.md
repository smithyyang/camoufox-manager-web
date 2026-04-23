👻 SpecterForge WRB (Web Remote Browser)

SpecterForge WRB is a professional-grade, lightweight Web-based profile manager designed to forge stealthy, anti-detect browser environments. It empowers you with unlimited local profiles, advanced fingerprint protection, and a modern dashboard.

With its newly introduced Multi-Engine Architecture, SpecterForge seamlessly orchestrates both Firefox (Camoufox) and Chromium (Patchright) kernels, giving you the ultimate arsenal for privacy, multi-accounting, and bypassing advanced bot-detection systems.
✨ Key Features

    ✅ Unlimited Profiles: Purely local storage with zero account restrictions or subscription fees.

    ✅ Deep Fingerprinting Control:

        OS Spoofing: Mimic Windows, macOS, or Linux flawlessly.

        Font Whitelist: Automatically loads fonts.json to prevent leaking your real system fonts.

        Hardware Emulation: Customize Screen Resolution, CPU cores (hardware concurrency), and WebGL GPU vendor/renderer info.

    ✅ Multi-Engine Architecture (NEW):

        Dual-Core Support: Choose between Firefox (camoufox) or Chromium (patchright) kernels on a per-profile basis.

        Engine Adapter: Unified launch flow with engine-specific script builders dynamically handled in the backend.

        Isolated Repositories: Extensions are smartly split into extensions/xpi/ (Firefox) and extensions/crx/ (Chromium).

    ✅ Advanced Networking & Security:

        Proxy Binding: Independent HTTP/SOCKS4/SOCKS5 configurations per profile.

        Anti-DNS Leak: Automatically converts socks5 to socks5h to force remote DNS resolution, ensuring a clean 100% privacy score.

        Fallback DoH: If no proxy is set, both engines force DNS-over-HTTPS (TRR-only / Secure DNS) as a best-effort leak mitigation.

        Proxy Guardrails: Smart risk warnings for no-proxy or datacenter IP setups. (Strict mode available via ENV variables).

        Proxy Tester: Built-in curl-based latency and geolocation checks (City, Country, ISP).

    ✅ Integrated Extension Store:

        AMO Integration: Search and install Firefox Add-ons directly from the dashboard.

        Per-Profile Management: Toggle specific extensions on or off for each isolated environment.

    ✅ Smart Session Memory: Automatically remembers and restores your last open tabs upon the next launch.

    ✅ Stability & Process Management:

        Status Tracking: Real-time "Active/Offline" status for every profile.

        Async Logging: Redirects stdout/stderr to browser.log to prevent sudden browser crashes caused by OS pipe buffer overflows.

        Zombie Killer: One-click cleanup to terminate orphaned or untracked browser processes.

📂 Directory Structure
Plaintext

specterforge-manager/
├── app.py                # Flask Backend: Core routing, Engine Adapter & Profile management
├── requirements.txt      # Python dependencies
├── extensions/
│   ├── xpi/              # Firefox extensions (.xpi) downloaded via AMO
│   └── crx/              # Chromium extensions (.crx) for Patchright
├── templates/
│   └── index.html        # Frontend Dashboard UI (Dark Theme)
└── profiles_data/        # Auto-created; stores all environment data (Ignored by Git)
    └── <profile_id>/
        ├── meta.json     # Fingerprint config, tags, engine choice, and session history
        ├── browser.log   # Runtime logs to prevent system deadlocks
        └── userdata/     # Isolated browser profile (Cookies, Cache, LocalStorage)

🚀 Quick Start
1. Clone & Setup
Bash

git clone https://github.com/smithyyang/camoufox-manager-web.git specterforge-manager
cd specterforge-manager

# Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: .\venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

2. Fetch Browser Binaries (Crucial for first run)

SpecterForge requires the actual browser binaries to operate.

For Firefox (Camoufox) Engine:
Bash

python3 -m camoufox fetch

For Chromium (Patchright) Engine:
Bash

# Install the Patchright python wrapper
pip install patchright
# Download the Chromium binaries
patchright install chromium

3. Launch the Manager
Bash

python3 app.py

Visit http://127.0.0.1:7070 in your browser to access the SpecterForge dashboard.
⚠️ Important Notes

    Privacy & Access: The Web UI binds to 127.0.0.1 by default and is NOT exposed to your local network.

    Proxy Advice: It is highly recommended to use a different, high-quality residential proxy for each profile to avoid IP correlation.

    Security Strict Mode: You can enforce proxy usage by starting the app with environment variables: CM_REQUIRE_PROXY=1 or CM_BLOCK_DATACENTER_PROXY=1.

    Kernel Differences: While both engines provide excellent fingerprint spoofing, certain websites may have strict behavioral checks targeting specific engines. Switch between camoufox and patchright if you encounter captchas or blocks.

    Beta Status: As of 2026, SpecterForge relies on upstream anti-detect kernels that are actively evolving. Expect occasional updates and minor instabilities.

📄 License

This project is licensed under the MIT License.
