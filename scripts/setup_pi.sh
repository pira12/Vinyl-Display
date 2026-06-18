#!/usr/bin/env bash
# One-time setup for Vinyl Display on Raspberry Pi OS.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

echo "==> Installing system packages"
sudo apt-get update
sudo apt-get install -y \
  python3 python3-venv python3-pip \
  libportaudio2 libsndfile1 \
  ffmpeg \
  chromium-browser curl \
  build-essential cmake   # needed to build olaf

echo "==> Installing Zig (needed to build Olaf)"
# Olaf builds with Zig (build.zig.zon requires >= 0.16.0), which is not in apt.
# Grab the official prebuilt for this machine's architecture.
ZIG_VERSION="0.16.0"
if ! command -v zig >/dev/null 2>&1; then
  case "$(uname -m)" in
    x86_64)  ZIG_ARCH="x86_64" ;;
    aarch64) ZIG_ARCH="aarch64" ;;
    armv7l)  ZIG_ARCH="arm" ;;
    *) echo "Unsupported architecture: $(uname -m)" >&2; exit 1 ;;
  esac
  ztmp="$(mktemp -d)"
  curl -fsSL "https://ziglang.org/download/${ZIG_VERSION}/zig-${ZIG_ARCH}-linux-${ZIG_VERSION}.tar.xz" \
    | tar -xJ -C "$ztmp"
  sudo rm -rf /opt/zig
  sudo mv "$ztmp/zig-${ZIG_ARCH}-linux-${ZIG_VERSION}" /opt/zig
  sudo ln -sf /opt/zig/zig /usr/local/bin/zig
  rm -rf "$ztmp"
else
  echo "    zig already installed, skipping."
fi

echo "==> Building Olaf (self-hosted fingerprinter)"
if ! command -v olaf >/dev/null 2>&1; then
  tmp="$(mktemp -d)"
  git clone --depth 1 https://github.com/JorenSix/Olaf "$tmp/Olaf"
  make -C "$tmp/Olaf"
  sudo make -C "$tmp/Olaf" install
  rm -rf "$tmp"
else
  echo "    olaf already installed, skipping."
fi

echo "==> Creating Python virtualenv"
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

echo "==> Seeding config"
[ -f config.yaml ] || cp config.example.yaml config.yaml

cat <<'EOF'

Setup complete.

Next:
  1. Edit config.yaml (set the audio device + your MusicBrainz User-Agent).
  2. Try it without hardware:  ./.venv/bin/python -m backend.main --simulate
     then open http://localhost:8080
  3. Enroll a record:
       ./.venv/bin/python -m backend.enroll record --out sideA.wav --minutes 25
       ./.venv/bin/python -m backend.enroll add sideA.wav --release <MBID> --side A
  4. Install services (optional, autostart on boot):
       sudo cp systemd/*.service /etc/systemd/system/
       sudo systemctl enable --now vinyl-display.service vinyl-kiosk.service
EOF
