#!/data/data/com.termux/files/usr/bin/bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  G MAX V1 Signal Bot — Mobile Setup (Termux)
#  One-line install (paste this exact line into Termux):
#  pkg install curl -y && curl -fsSL https://raw.githubusercontent.com/Adilfffffff/gmax-bot/main/setup_mobile.sh | bash
#  Engineered by Paqu
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

REPO_RAW="https://raw.githubusercontent.com/Adilfffffff/gmax-bot/main"
BOT_DIR="$HOME/storage/shared/GMAX-Signal"
BOT_FILE="GMaxSignalBot.py"
BOT_PATH="$BOT_DIR/$BOT_FILE"
BIN="$PREFIX/bin"
SESSION="gmaxv1"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🤖 G MAX V1 Signal Bot — Mobile Setup"
echo "  Engineered by Paqu"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Storage permission (needed before ~/storage/shared exists)
echo ""
echo "  📁 Requesting storage permission..."
echo "  (a popup will appear — tap Allow)"
termux-setup-storage 2>/dev/null || true
sleep 3

# Fall back to Termux's own home if shared storage isn't available
# (e.g. permission denied, or termux-setup-storage not supported)
if [ ! -d "$HOME/storage/shared" ]; then
    BOT_DIR="$HOME/GMAX-Signal"
    BOT_PATH="$BOT_DIR/$BOT_FILE"
    echo "  ⚠️  Shared storage unavailable — using $BOT_DIR instead"
fi

mkdir -p "$BOT_DIR"
echo "  ✅ Bot folder: $BOT_DIR"

# Update packages
echo ""
echo "  📦 Updating packages..."
pkg update -y -q 2>/dev/null | tail -2
pkg upgrade -y -q 2>/dev/null | tail -2

# Install curl (needed to download the bot + future updates)
if ! command -v curl &>/dev/null; then
    echo "  Installing curl..."
    pkg install curl -y -q
fi

# Install tmux
if ! command -v tmux &>/dev/null; then
    echo "  Installing tmux..."
    pkg install tmux -y -q
fi
echo "  ✅ tmux ready"

# Install Python
if ! command -v python3 &>/dev/null && ! command -v python &>/dev/null; then
    echo "  Installing Python..."
    pkg install python -y -q
fi
PYTHON=$(command -v python3 || command -v python)
echo "  ✅ Python: $PYTHON"

# Install pip packages — only flask and requests, no Rust needed
echo ""
echo "  📦 Installing packages..."
$PYTHON -m pip install --upgrade pip -q 2>/dev/null
if ! $PYTHON -m pip install flask requests -q 2>/dev/null; then
    $PYTHON -m pip install flask requests -q --break-system-packages 2>/dev/null
fi
if $PYTHON -c "import flask, requests" 2>/dev/null; then
    echo "  ✅ Packages installed"
else
    echo "  ❌ Failed to install flask/requests — install manually:"
    echo "     $PYTHON -m pip install flask requests"
    exit 1
fi

# ── Download the bot itself ─────────────────────────────────
echo ""
echo "  ⬇️  Downloading G MAX V1..."
if curl -fsSL "$REPO_RAW/$BOT_FILE" -o "$BOT_PATH"; then
    echo "  ✅ $BOT_FILE downloaded"
else
    echo "  ❌ Download failed — check your internet connection and try again"
    exit 1
fi

# ── Write startg ──────────────────────────────────────────
cat > "$BIN/startg" << ENDSUB
#!/data/data/com.termux/files/usr/bin/bash
SESSION="gmaxv1"
BOT_PATH="$BOT_PATH"
PYTHON="$PYTHON"

if tmux has-session -t "\$SESSION" 2>/dev/null; then
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  ⚠️  G Max V1 already running!"
    echo "  viewg   — see live logs"
    echo "  stopg   — stop bot"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    exit 1
fi

if [ ! -f "\$BOT_PATH" ]; then
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  ❌ GMaxSignalBot.py not found!"
    echo "  Expected: \$BOT_PATH"
    echo "  Try: updateg"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    exit 1
fi

BOT_DIR="\$(dirname "\$BOT_PATH")"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🤖 Starting G Max V1..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
tmux new-session -d -s "\$SESSION" "cd \"\$BOT_DIR\" && \$PYTHON \$BOT_PATH"
sleep 3

if tmux has-session -t "\$SESSION" 2>/dev/null; then
    IP=\$(\$PYTHON -c "import socket; s=socket.socket(); s.connect(('8.8.8.8',80)); print(s.getsockname()[0]); s.close()" 2>/dev/null || echo "localhost")
    echo "  ✅ G Max V1 is RUNNING!"
    echo ""
    echo "  📊 Dashboard : http://\$IP:5000"
    echo "  📱 Telegram  : send /menu to your bot"
    echo "  📜 Live logs : viewg"
    echo "  🛑 Stop      : stopg"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
else
    echo "  ❌ Failed to start."
    echo "  Run: \$PYTHON \$BOT_PATH"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
fi
ENDSUB

# Capital alias
cat > "$BIN/Startg" << 'ENDSUB'
#!/data/data/com.termux/files/usr/bin/bash
startg
ENDSUB

# ── Write stopg ───────────────────────────────────────────
cat > "$BIN/stopg" << 'ENDSUB'
#!/data/data/com.termux/files/usr/bin/bash
SESSION="gmaxv1"
if ! tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  G Max V1 is not running."
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    exit 0
fi
tmux kill-session -t "$SESSION"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✅ G Max V1 stopped."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
ENDSUB

cat > "$BIN/Stopg" << 'ENDSUB'
#!/data/data/com.termux/files/usr/bin/bash
stopg
ENDSUB

# ── Write viewg ───────────────────────────────────────────
cat > "$BIN/viewg" << 'ENDSUB'
#!/data/data/com.termux/files/usr/bin/bash
SESSION="gmaxv1"
if ! tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Bot is not running. Use: startg"
    exit 1
fi
echo "Attaching to bot logs... (Ctrl+B then D to detach)"
sleep 1
tmux attach-session -t "$SESSION"
ENDSUB

# ── Write gstatus ─────────────────────────────────────────
cat > "$BIN/gstatus" << ENDSUB
#!/data/data/com.termux/files/usr/bin/bash
SESSION="gmaxv1"
if tmux has-session -t "\$SESSION" 2>/dev/null; then
    IP=\$($PYTHON -c "import socket; s=socket.socket(); s.connect(('8.8.8.8',80)); print(s.getsockname()[0]); s.close()" 2>/dev/null || echo "localhost")
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  ✅ G Max V1 is RUNNING"
    echo "  📊 Dashboard: http://\$IP:5000"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
else
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  ❌ G Max V1 is STOPPED"
    echo "  Use: startg"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
fi
ENDSUB

# ── Write updateg (re-download latest bot file) ────────────
cat > "$BIN/updateg" << ENDSUB
#!/data/data/com.termux/files/usr/bin/bash
SESSION="gmaxv1"
BOT_PATH="$BOT_PATH"
REPO_RAW="$REPO_RAW"

WAS_RUNNING=0
if tmux has-session -t "\$SESSION" 2>/dev/null; then
    WAS_RUNNING=1
    tmux kill-session -t "\$SESSION"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ⬇️  Downloading latest G MAX V1..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if curl -fsSL "\$REPO_RAW/GMaxSignalBot.py" -o "\$BOT_PATH"; then
    echo "  ✅ Updated successfully"
else
    echo "  ❌ Update failed — check your internet connection"
    exit 1
fi

if [ "\$WAS_RUNNING" = "1" ]; then
    echo "  Restarting bot..."
    startg
else
    echo "  Bot was stopped — run startg when ready"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
ENDSUB

chmod +x "$BIN/startg" "$BIN/Startg" "$BIN/stopg" "$BIN/Stopg" "$BIN/viewg" "$BIN/gstatus" "$BIN/updateg"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✅ Mobile Setup Complete!"
echo ""
echo "  Bot location : $BOT_PATH"
echo ""
echo "  COMMANDS:"
echo "  startg     — start the bot"
echo "  stopg      — stop the bot"
echo "  viewg      — see live logs"
echo "  gstatus    — check running status"
echo "  updateg    — download the latest version"
echo ""
echo "  Run 'startg' now to launch G MAX V1!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
