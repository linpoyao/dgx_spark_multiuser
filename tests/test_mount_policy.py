#!/usr/bin/env python3
"""test_mount_policy.py —— 掛載政策單元測試（正向 + 攻擊向量負向測試）。

執行：python3 tests/test_mount_policy.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "configs"))
from mount_policy import (  # noqa: E402
    PolicyViolation,
    build_volumes,
    load_policy,
    validate_volumes,
    volumes_for,
)

POLICY = load_policy()
PASS = FAIL = 0


def ok(desc, cond):
    global PASS, FAIL
    print(("PASS  " if cond else "FAIL  ") + desc)
    PASS, FAIL = (PASS + bool(cond), FAIL + (not cond))


def raises(desc, volumes):
    """負向測試：validate_volumes 必須擋下來"""
    global PASS, FAIL
    try:
        validate_volumes(volumes, POLICY)
    except PolicyViolation as e:
        print(f"PASS  {desc}  ← {e}")
        PASS += 1
        return
    print(f"FAIL  {desc}（政策沒擋下來！）")
    FAIL += 1


print("── A. 正向：各角色的掛載清單 ──────────────────────────────────────")
alice = build_volumes("alice", groups=["alice", "lung-research"], policy=POLICY)
ok("alice 掛到 lung 去識別資料", "/data/deid/lung" in alice)
ok("alice 的 lung 資料為 ro", alice["/data/deid/lung"]["mode"] == "ro")
ok("alice 看不到 prostate 資料", "/data/deid/prostate" not in alice)
ok("alice 的 /workspace 為 rw 且對應自己的目錄",
   alice["/workspace/alice"] == {"bind": "/workspace", "mode": "rw"})
ok("alice 掛載清單中沒有 /data/raw", not any(h.startswith("/data/raw") for h in alice))

bob = build_volumes("bob", groups=["bob", "prostate-research"], policy=POLICY)
ok("bob 掛到 prostate 資料且看不到 lung",
   "/data/deid/prostate" in bob and "/data/deid/lung" not in bob)

guest = build_volumes("guest1", groups=["guest1", "lung-research"], policy=POLICY)
ok("guest1 只掛到指定 cohort（覆寫群組掛載）",
   "/data/deid/lung/cohort2018" in guest and "/data/deid/lung" not in guest)

poyao = build_volumes("poyao", groups=["poyao", "phi-raw", "lung-research", "prostate-research"], policy=POLICY)
ok("admin 的容器同樣掛不到 /data/raw（去識別只在主機做）",
   not any(h.startswith("/data/raw") for h in poyao))
ok("三個帳號的 volumes 都通過驗證",
   all(validate_volumes(v, POLICY) is None for v in (alice, bob, guest, poyao)))

print("── B. 負向：常見誤設與攻擊向量 ────────────────────────────────────")
raises("直接掛 /data/raw", {"/data/raw": {"bind": "/data/raw", "mode": "ro"}})
raises("掛 /data/raw 的子目錄", {"/data/raw/2018": {"bind": "/in", "mode": "ro"}})
raises("用 .. 迴避（/data/deid/../raw）", {"/data/deid/../raw": {"bind": "/in", "mode": "ro"}})
raises("掛整台主機根目錄（-v /:/host）", {"/": {"bind": "/host", "mode": "rw"}})
raises("掛 docker socket（等同 root）",
       {"/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"}})
raises("去識別資料被改成 rw", {"/data/deid/lung": {"bind": "/data/deid/lung", "mode": "rw"}})
raises("公開資料被改成 rw", {"/data/public/luna16": {"bind": "/x", "mode": "rw"}})
raises("掛 /etc（可植入 passwd/sudoers）", {"/etc": {"bind": "/etc-host", "mode": "ro"}})
raises("模式打錯字（r0）", {"/shared/models": {"bind": "/shared/models", "mode": "r0"}})
raises("容器內掛載點覆蓋 /etc", {"/shared/models": {"bind": "/etc", "mode": "rw"}})

# symlink 迂迴：/tmp/sneaky -> /data/raw
tmpd = tempfile.mkdtemp()
link = os.path.join(tmpd, "sneaky")
if os.path.isdir("/data/raw"):
    os.symlink("/data/raw", link)
    raises("以 symlink 指向 /data/raw", {link: {"bind": "/in", "mode": "ro"}})
else:
    print("SKIP  symlink 測試（此環境沒有 /data/raw）")

print("── C. volumes_for() 端到端（讀真實系統群組） ──────────────────────")
try:
    v = volumes_for("alice")
    ok("volumes_for('alice') 依系統群組產出且通過驗證", "/data/deid/lung" in v)
except Exception as e:  # 沙盒外可能沒有這些帳號
    print(f"SKIP  volumes_for 端到端（{e}）")

print(f"\n總計：PASS={PASS}  FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
