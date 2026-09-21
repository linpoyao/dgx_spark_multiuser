#!/usr/bin/env bash
# test_fail2ban.sh —— 驗證 fail2ban 設定可載入，且自訂的 JupyterHub filter 真的抓得到失敗登入
set -uo pipefail
PASS=0; FAIL=0
check() { local d="$1"; shift; if "$@" >/dev/null 2>&1; then echo "PASS  $d"; PASS=$((PASS+1)); else echo "FAIL  $d"; FAIL=$((FAIL+1)); fi; }
HERE="$(cd "$(dirname "$0")/.." && pwd)"

install -d /etc/fail2ban/filter.d /var/log/jupyterhub
cp "$HERE/configs/fail2ban/jail.local" /etc/fail2ban/jail.local
cp "$HERE/configs/fail2ban/filter.d/jupyterhub.conf" /etc/fail2ban/filter.d/jupyterhub.conf

# 取自 JupyterHub 實際輸出格式的樣本
cat > /tmp/jhub-sample.log <<'LOG'
[I 2026-09-13 01:20:30.001 JupyterHub log:186] 200 GET /hub/login (@100.64.0.9) 5.00ms
[W 2026-09-13 01:20:33.123 JupyterHub base:875] Failed login for alice
[W 2026-09-13 01:20:33.124 JupyterHub log:186] 403 POST /hub/login?next=%2Fhub%2F (@100.64.0.9) 120.00ms
[W 2026-09-13 01:20:36.500 JupyterHub log:186] 403 POST /hub/login?next=%2Fhub%2F (@100.64.0.9) 118.00ms
[W 2026-09-13 01:20:39.900 JupyterHub log:186] 403 POST /hub/login?next=%2Fhub%2F (@203.0.113.7) 121.00ms
[I 2026-09-13 01:21:02.000 JupyterHub log:186] 302 POST /hub/login?next=%2Fhub%2F (alice@100.64.0.9) 300.00ms
LOG

echo "── 1. 整體設定可載入 ──────────────────────────────────────────────"
check "fail2ban-client -d（設定語法）通過" fail2ban-client -d
check "jail 清單含 sshd 與 jupyterhub" bash -c 'fail2ban-client -d 2>/dev/null | grep -q "\[.add., .jupyterhub." && fail2ban-client -d 2>/dev/null | grep -q "\[.add., .sshd."'
check "ignoreip 含 127.0.0.1（不封鎖 cloudflared）" grep -q '127.0.0.1' /etc/fail2ban/jail.local

echo "── 2. JupyterHub filter 比對樣本記錄 ──────────────────────────────"
OUT=$(fail2ban-regex --print-all-matched /tmp/jhub-sample.log /etc/fail2ban/filter.d/jupyterhub.conf 2>&1)
MATCHED=$(sed -n "/Matched line/,/^\`-/p" <<<"$OUT")
echo "$OUT" | grep -E 'Failregex|matched|Lines' | sed 's/^/      /'
check "抓到 3 筆失敗登入" bash -c "grep -qE 'Failregex: *3 total' <<<\"\$0\"" "$OUT"
check "成功登入（302）不被誤判" bash -c "! grep -q '302 POST' <<<\"\$0\"" "$MATCHED"
check "抓到的 IP 含 100.64.0.9" bash -c "grep -q '100.64.0.9' <<<\"\$0\"" "$MATCHED"
check "抓到的 IP 含 203.0.113.7（不同來源分別計數）" bash -c "grep -q '203.0.113.7' <<<\"\$0\"" "$MATCHED"

echo "── 3. sshd filter 對硬化後的 LogLevel VERBOSE 仍有效 ──────────────"
cat > /tmp/sshd-sample.log <<'LOG'
Sep 13 01:30:01 spark sshd[1234]: Invalid user hacker from 100.64.0.44 port 51234
Sep 13 01:30:01 spark sshd[1234]: Failed publickey for invalid user hacker from 100.64.0.44 port 51234 ssh2: RSA SHA256:abcd
Sep 13 01:30:05 spark sshd[1235]: Connection closed by authenticating user alice 100.64.0.9 port 51240 [preauth]
LOG
OUT2=$(fail2ban-regex /tmp/sshd-sample.log /etc/fail2ban/filter.d/sshd.conf 2>&1)
check "sshd filter 抓到失敗嘗試" bash -c "! grep -qE 'Failregex: *0 total' <<<\"\$0\"" "$OUT2"

printf "\n總計：PASS=%d  FAIL=%d\n" "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
