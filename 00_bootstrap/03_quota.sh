#!/usr/bin/env bash
# 03_quota.sh —— 對 /workspace 所在檔案系統啟用使用者 quota，避免一個實驗把 4 TB 塞滿
# 注意：DGX OS 7 預設 ext4。若 /workspace 在根檔案系統上，mount option 要加在 / 這一行。
set -euo pipefail
FS_MOUNT="${1:-/}"          # /workspace 所在的掛載點
SOFT="${2:-400G}"; HARD="${3:-500G}"

grep -q "usrquota" /etc/fstab || echo "請先在 /etc/fstab 的 $FS_MOUNT 那一行加入 usrquota,grpquota 並重新掛載" >&2
mount -o remount "$FS_MOUNT"
quotacheck -cugm "$FS_MOUNT" || true
quotaon -v "$FS_MOUNT" || true

for u in alice bob guest1; do
  id "$u" >/dev/null 2>&1 || continue
  setquota -u "$u" "$SOFT" "$HARD" 0 0 "$FS_MOUNT"
done
repquota -s "$FS_MOUNT" | head -20
echo "OK: quota 設定完成（soft=$SOFT hard=$HARD）"
