👻 SpecterForge WRB (Web Remote Browser)

SpecterForge WRB is a professional-grade, lightweight Web-based profile manager for SpecterForge. It allows for unlimited local browser environments with advanced fingerprint protection and a modern dashboard.
✨ Key Features

    ✅ Unlimited Profiles: Purely local storage with no account or subscription limits.

    ✅ Data Isolation: Each profile has its own independent directory (Cookies, localStorage, and Cache are completely isolated).

    ✅ Deep Fingerprinting Control:

        OS Spoofing: Mimic Windows, macOS, or Linux.

        Font Whitelist: Automatically loads fonts.json to prevent leaking your real system fonts.

        Hardware Emulation: Customize Screen Resolution, CPU cores, and WebGL GPU info.

    ✅ Advanced Networking:

        Proxy Binding: Independent HTTP/SOCKS4/SOCKS5 configuration per profile.

        Anti-DNS Leak: Automatically converts socks5 to socks5h to force remote DNS resolution, ensuring a clean privacy score.

        Proxy Tester: Built-in latency and geolocation check (City, Country, ISP).

    ✅ Multi-Engine Architecture:

        Engine Switch: Per-profile kernel selection (`camoufox` / `patchright`).

        Engine Adapter: Unified launch flow with engine-specific script builders.

        Ext Repository Split: `extensions/xpi/` for Firefox, `extensions/crx/` for Chromium.

    ✅ Integrated Extension Store:

        AMO Integration: Search and install Firefox Add-ons directly from the dashboard.

        Per-Profile Management: Toggle specific extensions on or off for each environment.

    ✅ Session Memory: Automatically remembers and restores your last open tabs.

    ✅ Stability & Tools:

        Status Tracking: Real-time "Active/Offline" status for every profile.

        Async Logging: Redirects logs to browser.log to prevent browser crashes caused by pipe buffer overflows.

        Zombie Killer: One-click cleanup for orphaned browser processes.

📂 Directory Structure
Plaintext

camoufox-manager-web/
├── app.py                # Flask Backend: Core logic & Profile management
├── requirements.txt      # Python dependencies
├── extensions/
│   ├── xpi/              # Firefox extensions (.xpi)
│   └── crx/              # Chromium extensions (.crx)
├── templates/
│   └── index.html        # Frontend Dashboard UI
└── profiles_data/        # Auto-created; stores all environment data (Ignored by Git)
    └── <profile_id>/
        ├── meta.json     # Fingerprint config, tags, and session history
        ├── browser.log   # Runtime logs to prevent system deadlocks
        └── userdata/     # Isolated browser profile (Cookies, Cache, etc.)

🚀 Quick Start

    Clone & Setup:
    Bash

    git clone https://github.com/smithyyang/camoufox-manager-web.git
    cd camoufox-manager-web
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt

    Fetch Binaries (Crucial for first run):
    Bash

    python3 -m camoufox fetch

    (Optional) Enable Chromium engine support with Patchright:
    Bash

    pip install patchright

    Launch:
    Bash

    python3 app.py

    Visit http://localhost:7070 in your browser.

⚠️ Important Notes

    Privacy: The interface only listens on 127.0.0.1 and is not exposed to the network by default.

    Proxy Advice: It is highly recommended to use a different proxy for each profile to avoid IP correlation.

    Proxy Guardrails (default allow): launch does not hard-block no-proxy/datacenter-proxy by default, but returns risk warnings. Enable strict mode with `CM_REQUIRE_PROXY=1` and/or `CM_BLOCK_DATACENTER_PROXY=1` if needed.

    DNS Note: with no proxy, both engines now force DoH (TRR-only / Secure DNS) as a best-effort DNS leak mitigation; using a real proxy is still the most reliable approach.

    Firefox Kernel: Since it is based on the Firefox engine, it cannot simulate Chrome-specific internal behaviors.

    Beta Status: As of 2026, SpecterForge is still in beta; expect occasional updates and minor instabilities.

📄 License

This project is licensed under the MIT License.
