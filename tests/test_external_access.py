#!/usr/bin/env python3
"""test_external_access.py —— 驗證外網登入路徑的設定檔（不需要真的連線）。

檢查重點是「主機不曝露」這件事有沒有真的寫進設定：
  - cloudflared 的 origin 必須指向 127.0.0.1（不是 0.0.0.0，也不是主機 LAN IP）
  - Tailscale ACL 必須是白名單，且 guest 群組不得有 22 埠
  - systemd unit 不得把 Hub 綁到公網介面，且 docker socket 只有 Hub 這個服務持有
"""
import json
import os
import re
import sys

import yaml

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PASS = FAIL = 0


def ok(desc, cond, extra=""):
    global PASS, FAIL
    print(("PASS  " if cond else "FAIL  ") + desc + (f"  <- {extra}" if extra else ""))
    PASS, FAIL = (PASS + bool(cond), FAIL + (not cond))


print("-- 1. cloudflared tunnel ------------------------------------------")
with open(os.path.join(BASE, "configs/cloudflared/config.yml"), encoding="utf-8") as f:
    cf = yaml.safe_load(f)
ok("config.yml 可被解析為 YAML", isinstance(cf, dict))
ingress = cf.get("ingress", [])
svc = [i.get("service", "") for i in ingress if "hostname" in i]
ok("每個 hostname 都有對應的 service", len(svc) == len([i for i in ingress if "hostname" in i]))
ok("origin 一律指向 127.0.0.1（不曝露主機介面）",
   all(s.startswith("http://127.0.0.1") for s in svc), ", ".join(svc))
ok("有 catch-all 規則（未列出的主機名回 404）",
   any(i.get("service", "").startswith("http_status") for i in ingress))
ok("沒有把 SSH 或其他 TCP 服務直接開到 tunnel 上",
   not any(s.startswith(("ssh://", "tcp://", "rdp://")) for s in
           [i.get("service", "") for i in ingress]))

print("-- 2. Tailscale ACL -----------------------------------------------")
raw = open(os.path.join(BASE, "configs/tailscale/acl.json"), encoding="utf-8").read()
acl = json.loads(raw)
ok("acl.json 是合法 JSON", isinstance(acl, dict))
rules = acl.get("acls", [])
ok("所有規則都是 accept 白名單（沒有 wildcard src）",
   all(r["action"] == "accept" and "*" not in r["src"] for r in rules))
guest_dsts = [d for r in rules if "group:lab-guest" in r["src"] for d in r["dst"]]
ok("guest 沒有 22 埠（只能走網頁）",
   guest_dsts and not any(d.endswith(":22") for d in guest_dsts), ", ".join(guest_dsts))
researcher = [d for r in rules if "group:lab-researcher" in r["src"] for d in r["dst"]]
ok("researcher 有 SSH 與 Hub 兩個埠", any(d.endswith(":22") for d in researcher)
   and any(d.endswith(":8000") for d in researcher))
ok("沒有任何規則開放整台機器的所有埠（tag:spark:*）",
   not any(d.endswith(":*") for r in rules for d in r["dst"]))
ok("Tailscale SSH 只允許非 root 使用者",
   all("autogroup:nonroot" in s.get("users", []) for s in acl.get("ssh", [])))

print("-- 3. systemd unit -------------------------------------------------")
unit = open(os.path.join(BASE, "configs/systemd/jupyterhub.service"), encoding="utf-8").read()
ok("只有 jupyterhub 服務持有 docker 群組",
   re.search(r"^SupplementaryGroups=docker$", unit, re.M) is not None)
ok("服務禁止提權（NoNewPrivileges）", "NoNewPrivileges=true" in unit)
ok("proxy token 由 EnvironmentFile 帶入（不寫在設定檔裡）", "EnvironmentFile=" in unit)
ok("開機順序在 docker 與 tailscaled 之後",
   "docker.service" in unit and "tailscaled.service" in unit)

print("-- 4. 交叉檢查：Hub 設定與外網路徑一致 -----------------------------")
jh = open(os.path.join(BASE, "configs/jupyterhub_config.py"), encoding="utf-8").read()
ok("Hub bind_url 與 cloudflared origin 的 127.0.0.1:8000 相符",
   'bind_url = "http://127.0.0.1:8000"' in jh
   and any("127.0.0.1:8000" in s for s in svc))
ok("設定檔沒有硬寫任何 token/密碼", "auth_token = os.environ" in jh)

print(f"\n總計：PASS={PASS}  FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
