#!/usr/bin/env python3
"""e2e_vm.py —— 在「實際跑起來的」JupyterHub + DockerSpawner 上做端到端驗證。

和 validate_jupyterhub_config.py（靜態驗證）不同，這支會：
  真的用 HTTP 登入 → 真的 spawn 容器 → 進到使用者容器裡檢查看得到什麼、能做什麼。
需要 root（要設測試密碼、docker exec、讀主機檔案）。

用法：python3 tests/e2e_vm.py [--hub http://127.0.0.1:8000] [--phase all|auth|spawn|failclosed|cull|cleanup]
驗收完一定要跑：python3 tests/e2e_vm.py --phase cleanup
"""
from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import time

import requests

PASS = FAIL = 0
# 測試密碼每次隨機產生，存在只有 root 可讀的檔案裡，供後續各 phase 沿用。
# 不可寫死在程式碼裡：這支腳本會進 git，而且會在真機的真實帳號上設定密碼。
# 驗收完務必執行 `--phase cleanup`：重新鎖住密碼、刪除測試帳號 mallory、刪除密碼檔。
PW_FILE = "/root/.e2e_vm_passwords.json"
E2E_USERS = ("alice", "bob", "guest1", "poyao", "mallory")


def _load_passwords():
    import secrets
    if os.path.exists(PW_FILE):
        with open(PW_FILE, encoding="utf-8") as f:
            return json.load(f)
    pw = {u: secrets.token_urlsafe(18) for u in E2E_USERS}
    fd = os.open(PW_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(pw, f)
    return pw


PW = _load_passwords() if os.geteuid() == 0 else {}
POLICY = "/srv/jupyterhub/mount_policy.yaml"
HUBLOG = "/var/log/jupyterhub/jupyterhub.log"


def ok(desc, cond, extra=""):
    global PASS, FAIL
    print(("PASS  " if cond else "FAIL  ") + desc + (f"  <- {extra}" if extra else ""), flush=True)
    PASS, FAIL = (PASS + bool(cond), FAIL + (not cond))


def sh(cmd, check=False):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"{cmd}\n{r.stderr}")
    return r


def dexec(user, cmd):
    """在使用者容器內以容器設定的 USER（= 該使用者 UID）執行"""
    return sh(f"docker exec jupyter-{user} sh -c {json.dumps(cmd)}")


def login(hub, user, password):
    s = requests.Session()
    s.get(f"{hub}/hub/login")
    xsrf = next((c.value for c in s.cookies if c.name == "_xsrf" and c.path.startswith("/hub")), "")
    r = s.post(f"{hub}/hub/login?next=%2Fhub%2F",
               data={"username": user, "password": password, "_xsrf": xsrf},
               allow_redirects=False)
    return s, r.status_code


def user_xsrf(s, user):
    return next((c.value for c in s.cookies if c.name == "_xsrf" and f"/user/{user}" in c.path), "")


def spawn(hub, s, user, timeout=120):
    """觸發 spawn 並等到 JupyterLab 頁面可開；回傳 (成功與否, 最後狀態)。
    注意：JupyterHub ≥4.1 的 single-user server 對「cookie 驗證的 API 請求」一律要求 XSRF token
    （連 GET 也要），所以不能直接打 /api/status 當就緒探針——要先開 /lab 頁面拿到 _xsrf。"""
    s.get(f"{hub}/hub/spawn/{user}", allow_redirects=True)
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout:
        r = s.get(f"{hub}/user/{user}/lab", allow_redirects=True, headers={"Sec-Fetch-Mode": "navigate"})
        last = f"{r.status_code} {r.url}"
        if r.status_code == 200 and f"/user/{user}/lab" in r.url:
            return True, last
        if "spawn-pending" not in r.url and "/user/" not in r.url and time.time() - t0 > 20:
            return False, last
        time.sleep(3)
    return False, last


def api(s, hub, user, method, path, **kw):
    headers = kw.pop("headers", {})
    headers["X-XSRFToken"] = user_xsrf(s, user)
    return s.request(method, f"{hub}/user/{user}/api/{path}", headers=headers, **kw)


_ADMIN = {}


def stop_server(user, hub="http://127.0.0.1:8000"):
    """經 Hub 的 admin API 停止（不可直接 docker rm，否則 Hub 仍以為伺服器在跑 → 之後回 424）"""
    if "s" not in _ADMIN:
        _ADMIN["s"], _ = login(hub, "poyao", PW["poyao"])
    s = _ADMIN["s"]
    xsrf = next((c.value for c in s.cookies if c.name == "_xsrf" and c.path.startswith("/hub")), "")
    s.delete(f"{hub}/hub/api/users/{user}/server", headers={"X-XSRFToken": xsrf})
    for _ in range(30):
        r = s.get(f"{hub}/hub/api/users/{user}", headers={"X-XSRFToken": xsrf})
        if r.ok and not r.json().get("servers"):
            break
        time.sleep(2)
    sh(f"docker rm -f jupyter-{user}")   # 保險：清掉殘留


# ────────────────────────────────────────────────────────────────────────────
def phase_auth(hub):
    print("\n== A. 身分驗證（PAM + 白名單） ==========================================")
    # A1：01_groups_users.sh 預設 passwd -l；鎖住的帳號應無法以密碼登入 Hub（runbook §12.1 的說法）
    sh("passwd -l alice")
    _, code = login(hub, "alice", PW["alice"])
    ok("密碼被鎖（passwd -l）的帳號無法登入 Hub", code != 302, f"HTTP {code}")

    # 設定測試密碼（等同 runbook §12.1 方式 A）
    for u in ("alice", "bob", "guest1", "poyao"):
        sh(f"passwd -u {u}")
        sh(f"echo '{u}:{PW[u]}' | chpasswd", check=True)
    _, code = login(hub, "alice", PW["alice"])
    ok("解鎖並設密碼後 alice 可登入", code == 302, f"HTTP {code}")

    # A2：錯誤密碼 → 403，且真實 log 要能被 fail2ban filter 抓到
    before = open(HUBLOG, encoding="utf-8").read().count("Failed login for")
    for _ in range(3):
        _, code = login(hub, "alice", "wrong-password")
    ok("錯誤密碼回 403", code == 403, f"HTTP {code}")
    after = open(HUBLOG, encoding="utf-8").read().count("Failed login for")
    ok("Hub log 記錄了失敗登入", after - before >= 3, f"+{after - before}")
    tail = "/tmp/jhub-real-tail.log"
    sh(f"tail -n 400 {HUBLOG} > {tail}")
    r = sh(f"fail2ban-regex {tail} /etc/fail2ban/filter.d/jupyterhub.conf")
    m = re.search(r"Failregex:\s*(\d+) total", r.stdout)
    n = int(m.group(1)) if m else 0
    ok("fail2ban filter 能抓到「真實」Hub log 的失敗登入", n >= 3, f"{n} 筆")
    ok("真實 log 的來源 IP 是 127.0.0.1（印證 ignoreip 的必要性）",
       "(@127.0.0.1)" in open(tail, encoding="utf-8").read())

    # A3：系統上有帳號、密碼也正確，但不在 allowed_users → 仍拒絕
    if sh("id mallory").returncode:
        sh("useradd -m -s /bin/bash mallory")
    sh(f"echo 'mallory:{PW['mallory']}' | chpasswd", check=True)
    _, code = login(hub, "mallory", PW["mallory"])
    ok("不在 allowed_users 的系統帳號（密碼正確）仍被拒絕", code != 302, f"HTTP {code}")


def phase_spawn(hub):
    print("\n== B. 實際 spawn：進入各使用者容器檢查 =================================")
    sessions = {}
    for u in ("alice", "bob", "guest1", "poyao"):
        stop_server(u)
        s, code = login(hub, u, PW[u])
        good, last = spawn(hub, s, u)
        ok(f"{u} 登入並成功 spawn 容器", code == 302 and good, last)
        sessions[u] = s

    # 容器層級設定（docker inspect）
    for u in ("alice", "bob", "guest1", "poyao"):
        info = json.loads(sh(f"docker inspect jupyter-{u}").stdout or "[{}]")[0]
        pw_ = pwd.getpwnam(u)
        ok(f"{u} 容器以自己的 UID:GID 執行",
           info.get("Config", {}).get("User") == f"{pw_.pw_uid}:{pw_.pw_gid}",
           info.get("Config", {}).get("User"))
        hc = info.get("HostConfig", {})
        ok(f"{u} 容器 cap_drop=ALL 且 no-new-privileges",
           hc.get("CapDrop") == ["ALL"] and "no-new-privileges" in (hc.get("SecurityOpt") or []))
        mounts = {m["Source"]: m for m in info.get("Mounts", [])}
        ok(f"{u} 容器的掛載來源中沒有 /data/raw",
           not any(src.startswith("/data/raw") for src in mounts), ", ".join(sorted(mounts)))
        ok(f"{u} 容器沒有掛 docker socket", not any("docker.sock" in s for s in mounts))

    print("-- 容器內部行為（docker exec，身分 = 容器的 USER） --")
    r = dexec("alice", "id -u"); ok("alice 容器內 uid 非 0", r.stdout.strip() not in ("", "0"), r.stdout.strip())
    r = dexec("alice", "grep CapEff /proc/self/status"); ok("alice 容器內有效 capability 為 0",
                                                            r.stdout.split()[-1] == "0000000000000000", r.stdout.strip())
    r = dexec("alice", "ls /data/raw"); ok("alice 容器內看不到 /data/raw", r.returncode != 0, r.stderr.strip()[:60])
    r = dexec("alice", "cat /data/deid/lung/case001.npz"); ok("alice 可讀 lung 去識別資料", r.returncode == 0)
    r = dexec("alice", "touch /data/deid/lung/x"); ok("alice 無法寫入 lung 去識別資料（ro 掛載）",
                                                     r.returncode != 0, r.stderr.strip()[:60])
    r = dexec("alice", "ls /data/deid/prostate"); ok("alice 容器內看不到 prostate 資料", r.returncode != 0)
    r = dexec("alice", "ls /var/run/docker.sock"); ok("alice 容器內沒有 docker socket", r.returncode != 0)
    r = dexec("alice", "echo hi > /workspace/e2e_alice.txt"); ok("alice 可寫自己的 /workspace", r.returncode == 0)
    st = os.stat("/workspace/alice/e2e_alice.txt") if os.path.exists("/workspace/alice/e2e_alice.txt") else None
    ok("容器內寫的檔案在主機上屬於 alice（不是 root）",
       st is not None and st.st_uid == pwd.getpwnam("alice").pw_uid)
    lung_gid = str(__import__("grp").getgrnam("lung-research").gr_gid)
    r = dexec("alice", "touch /shared/models/e2e_weights.pt && stat -c %g /shared/models/e2e_weights.pt")
    ok("alice 在 /shared/models 寫檔，群組繼承 lung-research（group_add + setgid）",
       r.returncode == 0 and r.stdout.strip() == lung_gid, (r.stdout or r.stderr).strip())

    r = dexec("bob", "ls /data/deid/lung"); ok("bob 容器內看不到 lung 資料", r.returncode != 0)
    r = dexec("bob", "cat /data/deid/prostate/case001.npz"); ok("bob 可讀 prostate 資料", r.returncode == 0)
    r = dexec("bob", "ls /workspace"); ok("bob 的 /workspace 是自己的（看不到 alice 的檔案）",
                                          "e2e_alice.txt" not in r.stdout)

    r = dexec("guest1", "ls /data/deid/lung"); ok("guest1 看得到的 lung 資料只有 cohort2018",
                                                  r.returncode == 0 and r.stdout.split() == ["cohort2018"], r.stdout.strip())
    r = dexec("guest1", "cat /data/deid/lung/cohort2018/a.npz"); ok("guest1 可讀 cohort2018", r.returncode == 0)
    r = dexec("guest1", "touch /data/deid/lung/cohort2018/x"); ok("guest1 無法寫入 cohort2018", r.returncode != 0)

    r = dexec("poyao", "ls /data/raw"); ok("admin 的容器同樣看不到 /data/raw（去識別只在主機做）", r.returncode != 0)

    print("-- 經由 proxy 的 Jupyter API（使用者真正會走的路徑） --")
    s = sessions["alice"]
    r = api(s, hub, "alice", "GET", "contents")
    names = [c["name"] for c in r.json().get("content", [])] if r.ok else []
    ok("alice 經瀏覽器路徑可列出自己的檔案", r.ok and "e2e_alice.txt" in names, str(names)[:80])
    r = api(s, hub, "alice", "PUT", "contents/from_browser.ipynb",
            json={"type": "notebook", "content": {"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}})
    ok("alice 經 Jupyter API 建立 notebook", r.status_code in (200, 201), f"HTTP {r.status_code}")
    ok("該 notebook 落在主機 /workspace/alice 且屬於 alice",
       os.path.exists("/workspace/alice/from_browser.ipynb")
       and os.stat("/workspace/alice/from_browser.ipynb").st_uid == pwd.getpwnam("alice").pw_uid)
    r = s.get(f"{hub}/user/bob/api/contents", allow_redirects=True,
              headers={"X-XSRFToken": user_xsrf(s, "alice")})
    ok("alice 的登入 session 無法存取 bob 的伺服器", not (r.ok and "/user/bob/api/contents" in r.url),
       f"HTTP {r.status_code} {r.url[-40:]}")
    return sessions


def phase_failclosed(hub):
    print("\n== C. fail-closed：政策被改成掛 /data/raw 時，容器必須起不來 =============")
    stop_server("bob")
    shutil.copy(POLICY, POLICY + ".bak")
    try:
        txt = open(POLICY, encoding="utf-8").read().replace(
            "common:\n", 'common:\n  - {host: "/data/raw", container: "/data/raw", mode: ro}\n', 1)
        open(POLICY, "w", encoding="utf-8").write(txt)
        s, _ = login(hub, "bob", PW["bob"])
        good, last = spawn(hub, s, "bob", timeout=30)
        ok("被竄改的政策下 bob 的容器起不來", not good, last)
        ok("主機上沒有 jupyter-bob 容器", sh("docker inspect jupyter-bob").returncode != 0)
        ok("Hub log 出現 PolicyViolation", "PolicyViolation" in open(HUBLOG, encoding="utf-8").read()
           or "禁止掛載 /data/raw" in open(HUBLOG, encoding="utf-8").read())
    finally:
        shutil.move(POLICY + ".bak", POLICY)
    s, _ = login(hub, "bob", PW["bob"])
    good, last = spawn(hub, s, "bob")
    ok("政策還原後 bob 可正常 spawn（不需重啟 Hub）", good, last)


def phase_cull(hub, wait=200):
    print(f"\n== D. 閒置回收（沙盒 timeout=90s，最多等 {wait}s） =======================")
    running = [u for u in ("alice", "bob", "guest1", "poyao") if sh(f"docker inspect jupyter-{u}").returncode == 0]
    ok("回收前有容器在跑", bool(running), ", ".join(running))
    t0 = time.time()
    while time.time() - t0 < wait:
        left = [u for u in running if sh(f"docker inspect jupyter-{u}").returncode == 0]
        if not left:
            break
        time.sleep(10)
    ok("閒置容器全部被 idle-culler 回收（DockerSpawner remove=True → 容器刪除）", not left,
       f"{int(time.time() - t0)}s，剩餘 {left}")


def phase_cleanup():
    print("\n== 清理：鎖回密碼、移除測試帳號與密碼檔 ================================")
    for u in ("alice", "bob", "guest1", "poyao"):
        stop_server(u)
        sh(f"passwd -l {u}")
        ok(f"{u} 的密碼已重新鎖住", sh(f"passwd -S {u}").stdout.split()[1:2] == ["L"])
    sh("userdel -r mallory")
    ok("測試帳號 mallory 已刪除", sh("id mallory").returncode != 0)
    if os.path.exists(PW_FILE):
        os.remove(PW_FILE)
    ok("隨機密碼檔已刪除", not os.path.exists(PW_FILE))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hub", default="http://127.0.0.1:8000")
    ap.add_argument("--phase", default="all")
    a = ap.parse_args()
    if a.phase in ("all", "auth"):
        phase_auth(a.hub)
    if a.phase in ("all", "spawn"):
        phase_spawn(a.hub)
    if a.phase in ("all", "failclosed"):
        phase_failclosed(a.hub)
    if a.phase in ("all", "cull"):
        phase_cull(a.hub)
    if a.phase == "cleanup":
        phase_cleanup()
    print(f"\n總計：PASS={PASS}  FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
