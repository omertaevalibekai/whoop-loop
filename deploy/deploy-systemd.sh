#!/usr/bin/env bash
# Deploy Whoop Loop to a small VM (no Docker).
#
#   ./deploy/deploy-systemd.sh opc@1.2.3.4
#
# Written for a 1 GB Always Free instance, where two things bite:
#
#   1. Heavy steps (dnf, pip) push the box into swap hard enough that sshd
#      stops answering. A plain `ssh host "long command"` then dies on timeout
#      and `set -e` aborts the whole deploy — while the remote command keeps
#      running, so a retry collides with it and both starve. Every heavy step
#      here is therefore launched detached with setsid+nohup and polled from
#      outside, so a dropped connection costs nothing.
#   2. pip resolving the full requirements file at once is what actually
#      exhausts memory, not any single wheel. Packages go in one at a time.
set -euo pipefail

# Machine-specific overrides (SSH_KEY=..., REMOTE_DIR=...) live outside git.
LOCAL_ENV="$(dirname "${BASH_SOURCE[0]}")/local.env"
[ -f "$LOCAL_ENV" ] && . "$LOCAL_ENV"

TARGET="${1:?Usage: ./deploy/deploy-systemd.sh opc@host}"
REMOTE_DIR="${REMOTE_DIR:-/opt/whoop-loop}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/whoop-loop.key}"
LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SSH=(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=20)
SCP=(scp -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new -o BatchMode=yes)

# run_detached <marker-file> <log> <command...>
# Wait until the log contains the marker, tolerating the box going silent.
wait_for_marker() {
  local log="$1" marker="$2" label="$3" tries="${4:-60}"
  for ((i = 1; i <= tries; i++)); do
    if timeout 25 "${SSH[@]}" "$TARGET" "grep -q '$marker' $log 2>/dev/null"; then
      echo "    $label: готово"
      return 0
    fi
    sleep 25
  done
  echo "    $label: не завершилось, смотри $log на сервере" >&2
  return 1
}

echo "==> Связь"
"${SSH[@]}" "$TARGET" "echo \$(hostname), \$(free -m | awk '/Mem:/{print \$2}') МБ RAM"

echo "==> Swap"
"${SSH[@]}" "$TARGET" bash -s <<'REMOTE'
set -e
if ! swapon --show | grep -q extraswap; then
  sudo fallocate -l 2G /extraswap && sudo chmod 600 /extraswap
  sudo mkswap /extraswap >/dev/null && sudo swapon /extraswap
  grep -q '^/extraswap' /etc/fstab || echo '/extraswap none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  echo "    +2 ГБ swap"
else
  echo "    swap на месте"
fi
# Biggest idle consumer on a micro shape; nothing here needs it.
sudo systemctl disable --now oracle-cloud-agent oracle-cloud-agent-updater 2>/dev/null || true
REMOTE

echo "==> Python 3.11 (в образе 3.9, коду нужен 3.11)"
if ! "${SSH[@]}" "$TARGET" "python3.11 --version >/dev/null 2>&1"; then
  "${SSH[@]}" "$TARGET" "rm -f /tmp/py311.log; setsid nohup sudo dnf install -y --setopt=install_weak_deps=False python3.11 python3.11-pip > /tmp/py311.log 2>&1 < /dev/null & sleep 2"
  for ((i = 1; i <= 60; i++)); do
    if timeout 25 "${SSH[@]}" "$TARGET" "python3.11 --version >/dev/null 2>&1"; then break; fi
    sleep 25
  done
  "${SSH[@]}" "$TARGET" "python3.11 --version"
else
  echo "    уже установлен"
fi

echo "==> Каталог и код"
"${SSH[@]}" "$TARGET" "sudo mkdir -p $REMOTE_DIR && sudo chown -R \$(id -un):\$(id -gn) $REMOTE_DIR && mkdir -p $REMOTE_DIR/data $REMOTE_DIR/charts && rm -rf $REMOTE_DIR/app"
# tar over ssh: Git Bash on Windows ships no rsync.
tar -czf - -C "$LOCAL_DIR" --exclude='__pycache__' --exclude='*.pyc' \
  app deploy cli.py run_bot.py run_api.py run_mcp.py requirements.txt \
  | "${SSH[@]}" "$TARGET" "tar -xzf - -C $REMOTE_DIR"

echo "==> Конфигурация"
"${SCP[@]}" "$LOCAL_DIR/.env" "$TARGET:$REMOTE_DIR/.env" >/dev/null
"${SSH[@]}" "$TARGET" "chmod 600 $REMOTE_DIR/.env"

if "${SSH[@]}" "$TARGET" "test -f $REMOTE_DIR/data/whoop.db"; then
  echo "    база на сервере уже есть — не перезаписываю"
else
  echo "    первый деплой: переношу базу вместе с токеном Whoop"
  "${SCP[@]}" "$LOCAL_DIR/data/whoop.db" "$TARGET:$REMOTE_DIR/data/whoop.db" >/dev/null
fi

echo "==> Зависимости (по одному пакету, отсоединённо)"
"${SCP[@]}" "$LOCAL_DIR/deploy/install-deps.sh" "$TARGET:/tmp/install-deps.sh" >/dev/null
"${SSH[@]}" "$TARGET" "chmod +x /tmp/install-deps.sh; rm -f /tmp/pip.log; setsid nohup /tmp/install-deps.sh > /tmp/pip.log 2>&1 < /dev/null & sleep 2"
wait_for_marker /tmp/pip.log INSTALL_DONE "пакеты" 60

echo "==> systemd"
"${SSH[@]}" "$TARGET" bash -s <<REMOTE
set -e
sudo cp $REMOTE_DIR/deploy/whoop-loop.service /etc/systemd/system/whoop-loop.service
sudo systemctl daemon-reload
sudo systemctl enable whoop-loop >/dev/null 2>&1
sudo systemctl restart whoop-loop
REMOTE

sleep 12
echo "==> Статус"
"${SSH[@]}" "$TARGET" "systemctl is-active whoop-loop; sudo journalctl -u whoop-loop -n 8 --no-pager | tail -4"

echo
echo "Логи:  ssh -i $SSH_KEY $TARGET 'sudo journalctl -u whoop-loop -f'"
echo "ВАЖНО: локального бота остановить, иначе два процесса делят один токен Telegram."
