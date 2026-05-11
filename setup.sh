#!/bin/bash
# ============================================================
# TopUpFast VPS Setup Script (Debian 13 Trixie)
# Chạy: bash setup.sh
# ============================================================

set -e

BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="topupfast"
VENV_DIR="$BOT_DIR/.venv"

echo "=== TopUpFast VPS Setup ==="
echo "Dir: $BOT_DIR"

# 1. Cài Python nếu chưa có
if ! command -v python3 &>/dev/null; then
    echo "[1] Cài Python3..."
    apt-get update -qq && apt-get install -y python3 python3-pip python3-venv python3-full
else
    echo "[1] Python3 đã có: $(python3 --version)"
    # Đảm bảo có python3-venv (thường thiếu trên Debian minimal)
    apt-get install -y -qq python3-venv python3-full 2>/dev/null || true
fi

# 2. Tạo virtualenv
if [ ! -d "$VENV_DIR" ]; then
    echo "[2] Tạo virtualenv..."
    python3 -m venv "$VENV_DIR"
else
    echo "[2] Virtualenv đã có."
fi

# 3. Cài dependencies
echo "[3] Cài dependencies..."
"$VENV_DIR/bin/pip" install -q --upgrade pip
"$VENV_DIR/bin/pip" install -q -r "$BOT_DIR/requirements.txt"

# 4. Kiểm tra .env
if [ ! -f "$BOT_DIR/.env" ]; then
    echo ""
    echo "[4] CHƯA CÓ FILE .env !"
    echo "    Tạo file .env từ .env.example rồi điền token/key vào:"
    echo "    cp $BOT_DIR/.env.example $BOT_DIR/.env"
    echo "    nano $BOT_DIR/.env"
    echo ""
    echo "    Sau đó chạy lại: bash setup.sh"
    exit 1
else
    echo "[4] File .env đã có."
fi

# 5. Tạo systemd service (tự chạy khi boot, tự restart khi crash)
echo "[5] Tạo systemd service..."

cat > /etc/systemd/system/${SERVICE_NAME}.service << EOF
[Unit]
Description=TopUpFast Discord Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$BOT_DIR
ExecStart=$VENV_DIR/bin/python main.py
Restart=always
RestartSec=10
StandardOutput=append:/var/log/${SERVICE_NAME}.log
StandardError=append:/var/log/${SERVICE_NAME}.log
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable ${SERVICE_NAME}
systemctl restart ${SERVICE_NAME}

echo ""
echo "=== XONG ==="
echo "Xem log:     journalctl -u ${SERVICE_NAME} -f"
echo "           hoặc tail -f /var/log/${SERVICE_NAME}.log"
echo "Dừng bot:    systemctl stop ${SERVICE_NAME}"
echo "Khởi động:   systemctl start ${SERVICE_NAME}"
echo "Trạng thái:  systemctl status ${SERVICE_NAME}"
