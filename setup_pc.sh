#!/bin/bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  G MAX V1 Signal Bot — PC/VPS Setup (Linux/Mac)
#  One-line install:
#  curl -fsSL https://raw.githubusercontent.com/Adilfffffff/gmax-bot/main/setup_pc.sh | bash
#  Engineered by Paqu
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

REPO_RAW="https://raw.githubusercontent.com/Adilfffffff/gmax-bot/main"
BOT_DIR="$HOME/GMaxSignalBot"
BOT_FILE="GMaxSignalBot.py"
BOT_PATH="$BOT_DIR/$BOT_FILE"
# Global commands go here — most Linux/Mac setups already have this in PATH
BIN="$HOME/.local/bin"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🤖 G MAX V1 Signal Bot — PC/VPS Setup"
echo "  Engineered by Paqu"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

mkdir -p "$BOT_DIR"
mkdir -p "$BIN"
echo "  ✅ Bot folder: $BOT_DIR"

# Detect OS
OS="$(uname -s)"
echo "  🖥  OS: $OS"

# Install curl if needed (rare, but possible on minimal VPS images)
if ! command -v curl &>/dev/null; then
    echo "  Installing curl..."
    if [[ "$OS" == "Darwin" ]]; then
        brew install curl 2>/dev/null
    else
        sudo apt-get install -y curl 2>/dev/null || sudo yum install -y curl 2>/dev/null
    fi
fi

# Install tmux if needed
if ! command -v tmux &>/dev/null; then
    echo "  Installing tmux..."
    if [[ "$OS" == "Darwin" ]]; then
        brew install tmux 2>/dev/null || echo "  Install Homebrew first: https://brew.sh"
    else
        sudo apt-get install -y tmux 2>/dev/null || \
        sudo yum install -y tmux 2>/dev/null || \
        echo "  ⚠️  Install tmux manually: sudo apt install tmux"
    fi
fi
echo "  ✅ tmux: $(tmux -V 2>/dev/null || echo 'not found')"

# Detect Python
PYTHON=$(command -v python3 || command -v python)
if [ -z "$PYTHON" ]; then
    echo "  ❌ Python not found. Install Python 3.8+ first:"
    echo "     https://www.python.org/downloads/"
    exit 1
fi
echo "  ✅ Python: $($PYTHON --version)"

# Install packages
echo ""
echo "  📦 Installing packages..."
$PYTHON -m pip install --upgrade pip -q 2>/dev/null
if ! $PYTHON -m pip install flask requests -q 2>/dev/null; then
    # Newer Debian/Ubuntu systems block system-wide pip installs by default
    # (PEP 668 "externally managed environment") — fall back safely.
    $PYTHON -m pip install flask requests -q --break-system-packages
fi
if $PYTHON -c "import flask, requests" 2>/dev/null; then
    echo "  ✅ Packages installed"
else
    echo "  ❌ Failed to install flask/requests — install manually:"
    echo "     $PYTHON -m pip install flask requests --break-system-packages"
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
cat > "$BIN/startg" << STARTSCRIPT
#!/bin/bash
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
    echo "  📊 Dashboard : http://\$IP:5000  (or http://localhost:5000)"
    echo "  📱 Telegram  : send /menu to your bot"
    echo "  📜 Live logs : viewg"
    echo "  🛑 Stop      : stopg"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
else
    echo "  ❌ Failed to start."
    echo "  Run: \$PYTHON \$BOT_PATH"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
fi
STARTSCRIPT

# ── Write stopg ───────────────────────────────────────────
cat > "$BIN/stopg" << 'STOPSCRIPT'
#!/bin/bash
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
STOPSCRIPT

# ── Write viewg ───────────────────────────────────────────
cat > "$BIN/viewg" << 'VIEWSCRIPT'
#!/bin/bash
SESSION="gmaxv1"
if ! tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Bot is not running. Use: startg"
    exit 1
fi
echo "Attaching to bot logs... (Ctrl+B then D to detach)"
sleep 1
tmux attach-session -t "$SESSION"
VIEWSCRIPT

# ── Write gstatus ─────────────────────────────────────────
cat > "$BIN/gstatus" << STATUSSCRIPT
#!/bin/bash
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
STATUSSCRIPT

# ── Write updateg (re-download latest bot file) ────────────
cat > "$BIN/updateg" << UPDATESCRIPT
#!/bin/bash
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
UPDATESCRIPT

chmod +x "$BIN/startg" "$BIN/stopg" "$BIN/viewg" "$BIN/gstatus" "$BIN/updateg"

# Make sure $BIN is on PATH for future terminal sessions
SHELL_RC="$HOME/.bashrc"
[ -n "$ZSH_VERSION" ] && SHELL_RC="$HOME/.zshrc"
if ! echo "$PATH" | grep -q "$BIN"; then
    if ! grep -q "$BIN" "$SHELL_RC" 2>/dev/null; then
        echo "export PATH=\"$BIN:\$PATH\"" >> "$SHELL_RC"
    fi
    export PATH="$BIN:$PATH"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✅ PC Setup Complete!"
echo ""
echo "  Bot location : $BOT_PATH"
echo ""
echo "  COMMANDS (available from any folder):"
echo "  startg     — start the bot"
echo "  stopg      — stop the bot"
echo "  viewg      — see live logs"
echo "  gstatus    — check running status"
echo "  updateg    — download the latest version"
echo ""
echo "  ⚠️  If 'startg' says command not found, close and reopen"
echo "  your terminal once, then try again."
echo ""
echo "  Run 'startg' now to launch G MAX V1!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
