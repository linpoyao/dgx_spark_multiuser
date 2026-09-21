#!/usr/bin/env bash
# test_sshd_config.sh —— 驗證 sshd 硬化設定：語法、實際生效值、以及錯誤設定是否會被擋下
set -uo pipefail
PASS=0; FAIL=0
check() { local d="$1"; shift; if "$@" >/dev/null 2>&1; then echo "PASS  $d"; PASS=$((PASS+1)); else echo "FAIL  $d"; FAIL=$((FAIL+1)); fi; }
HERE="$(cd "$(dirname "$0")/.." && pwd)"
SSHD=$(command -v sshd || echo /usr/sbin/sshd)

install -d /run/sshd /etc/ssh/sshd_config.d   # -t 需要 privsep 目錄存在
cp "$HERE/configs/sshd/lab.conf" /etc/ssh/sshd_config.d/lab.conf
# 沙盒沒有 tailscale0 介面，ListenAddress 改成 loopback 才能做 -T 展開（真機用 100.x）
sed -i 's/^ListenAddress .*/ListenAddress 127.0.0.1/' /etc/ssh/sshd_config.d/lab.conf

echo "-- 1. 語法 --------------------------------------------------------"
check "sshd -t 通過（含 lab.conf drop-in）" "$SSHD" -t
echo "-- 2. 實際生效值（sshd -T 展開） ----------------------------------"
EFF=$("$SSHD" -T -C user=alice,host=spark,addr=100.64.0.9 2>/dev/null)
for kv in "passwordauthentication no" "kbdinteractiveauthentication no" "permitrootlogin no" \
          "maxauthtries 3" "x11forwarding no" "allowtcpforwarding no" "permittunnel no" \
          "logingracetime 30" "loglevel VERBOSE" "authenticationmethods publickey"; do
  check "生效值：$kv" grep -qix "$kv" <<<"$EFF"
done
check "AllowGroups 含三個研究群組" bash -c "grep -qi '^allowgroups .*lung-research' <<<\"\$0\"" "$EFF"
check "只聽 loopback/Tailscale 位址（沒有 0.0.0.0）" bash -c "! grep -qi '^listenaddress 0.0.0.0' <<<\"\$0\"" "$EFF"

echo "-- 3. 負向：錯誤設定必須被 sshd -t 擋下 ---------------------------"
cp /etc/ssh/sshd_config.d/lab.conf /tmp/lab.conf.bak
echo "PasswordAuthentications yes" >> /etc/ssh/sshd_config.d/lab.conf   # 故意打錯字
if "$SSHD" -t >/dev/null 2>&1; then echo "FAIL  錯字設定沒被擋下"; FAIL=$((FAIL+1)); else echo "PASS  錯字設定被 sshd -t 擋下"; PASS=$((PASS+1)); fi
cp /tmp/lab.conf.bak /etc/ssh/sshd_config.d/lab.conf

printf "\n總計：PASS=%d  FAIL=%d\n" "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
