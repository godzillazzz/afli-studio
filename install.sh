#!/usr/bin/env bash
# Afli Studio — ติดตั้งด้วยคำสั่งเดียว:  sudo bash install.sh
set -e
cd "$(dirname "$0")"

say() { echo -e "\n\033[1;33m▶ $*\033[0m"; }

if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then exec sudo bash "$0" "$@"; fi
  echo "กรุณาล็อกอินเป็น root (พิมพ์ su -) แล้วรัน bash install.sh อีกครั้ง"; exit 1
fi

# Docker needs extra options inside a Proxmox LXC container
if command -v systemd-detect-virt >/dev/null 2>&1 && [ "$(systemd-detect-virt 2>/dev/null)" = "lxc" ]; then
  echo "หมายเหตุ: เครื่องนี้เป็น LXC container — ถ้า Docker เปิดไม่ขึ้น ให้เปิด nesting และ keyctl ใน Proxmox (Options > Features)"
fi

if ! command -v docker >/dev/null 2>&1; then
  say "กำลังติดตั้ง Docker (ครั้งแรกครั้งเดียว ใช้เวลา 2-5 นาที)"
  apt-get update -y && apt-get install -y curl ca-certificates
  curl -fsSL https://get.docker.com | sh
fi
if ! docker compose version >/dev/null 2>&1; then
  apt-get install -y docker-compose-plugin
fi

if [ ! -f .env ]; then
  say "ตั้งรหัสผ่านสำหรับเข้าเว็บ (กด Enter เพื่อสุ่มให้)"
  read -r -p "รหัสผ่าน: " PW || true
  [ -z "$PW" ] && PW=$(tr -dc 'a-zA-Z0-9' </dev/urandom | head -c 10)
  read -r -p "พอร์ต (กด Enter = 8088): " PORT || true
  PORT=${PORT:-8088}
  printf "APP_PASSWORD=%s\nPORT=%s\n" "$PW" "$PORT" > .env
  chmod 600 .env
fi
. ./.env
mkdir -p data/music

say "กำลังสร้างและเปิดระบบ (ครั้งแรกใช้เวลา 3-8 นาที)"
docker compose up -d --build

IP=$(hostname -I | awk '{print $1}')
echo ""
echo "=============================================="
echo " ✅ ติดตั้งเสร็จแล้ว!"
echo " เปิดบนมือถือ (ต่อ Wi-Fi เดียวกัน):  http://$IP:$PORT"
echo " รหัสผ่าน: $APP_PASSWORD"
echo " เปลี่ยนรหัส: แก้ไฟล์ .env แล้วรัน bash install.sh อีกครั้ง"
echo "=============================================="
