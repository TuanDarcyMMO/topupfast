# TopUpFast - Hướng dẫn cài đặt VPS (Debian 13)

> Chạy tất cả lệnh với quyền **root** (`sudo su -` trước)

---

## Lần đầu cài đặt

### Bước 1: Switch sang root

```bash
sudo su -
```

### Bước 2: Cài git + clone repo (thay `<TOKEN>` bằng GitHub PAT của bạn)

```bash
apt-get update && apt-get install -y git && git clone https://TuanDarcy:<TOKEN>@github.com/TuanDarcy/topupfast.git /opt/topupfast
```

### Bước 3: Tạo file .env

```bash
cat > /opt/topupfast/.env << 'EOF'
DISCORD_TOKEN=
DISCORD_GUILD_ID=1498363612115243100
EXTRA_SYNC_GUILD_IDS=1223243994482610297
WELCOME_CHANNEL_ID=1498363613206020217
RULES_CHANNEL_ID=1498368083499159694
VERIFY_CHANNEL_ID=1498363613206020223
GENERAL_CHANNEL_ID=1498363614292082740
SEPAY_API_TOKEN=
SEPAY_BANK_CODE=BIDV
SEPAY_ACCOUNT_NUMBER=
SEPAY_ACCOUNT_NAME=
NOWPAYMENTS_API_KEY=
NOWPAYMENTS_IPN_SECRET=
WEBHOOK_HOST=0.0.0.0
WEBHOOK_PORT=8080
WEBHOOK_BASE_URL=http://<IP_VPS>:8080
SUPABASE_URL=
SUPABASE_KEY=
EXCHANGE_RATE=26000
MIN_DEPOSIT_VND=10000
MIN_DEPOSIT_USD=1.0
PAYMENT_EXPIRY_MINUTES=30
EOF
```

> Sau đó edit điền token thật: `nano /opt/topupfast/.env`

### Bước 4: Chạy setup (cài Python, tạo venv, tạo systemd service, start bot)

```bash
cd /opt/topupfast && bash setup.sh
```

### Bước 5: Kiểm tra bot chạy

```bash
systemctl status topupfast
```

---

## Cập nhật bản mới

```bash
cd /opt/topupfast && git pull origin main && systemctl restart topupfast && systemctl status topupfast
```

---

## Các lệnh hữu ích

| Lệnh                             | Mô tả                     |
| -------------------------------- | ------------------------- |
| `systemctl status topupfast`     | Xem trạng thái bot        |
| `systemctl restart topupfast`    | Restart bot               |
| `systemctl stop topupfast`       | Dừng bot                  |
| `systemctl start topupfast`      | Start bot                 |
| `journalctl -u topupfast -f`     | Xem log realtime          |
| `journalctl -u topupfast -n 100` | Xem 100 dòng log gần nhất |

---

## Truy cập Dashboard

```
http://<IP_VPS>:8080/dashboard
```
