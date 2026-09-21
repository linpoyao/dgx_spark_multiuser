#!/usr/bin/env python3
"""validate_jupyterhub_config.py —— 不啟動服務就驗證 jupyterhub_config.py。

檢查三件事：
  1. 每個 c.<Class>.<trait> 都是該類別真實存在的 trait（抓錯字，例如 auth_token_file）。
  2. 設定值型別能被 traitlets 接受（實際以 config 建構 Authenticator / DockerSpawner）。
  3. pre_spawn_hook 真的會把政策掛載寫進 spawner，且違規時會中止 spawn（fail-closed）。
"""
import os
import sys
import types

from traitlets.config.loader import PyFileConfigLoader

CFG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "configs")
sys.path.insert(0, CFG_DIR)

PASS = FAIL = 0


def ok(desc, cond, extra=""):
    global PASS, FAIL
    print(("PASS  " if cond else "FAIL  ") + desc + (f"  <- {extra}" if extra else ""))
    PASS, FAIL = (PASS + bool(cond), FAIL + (not cond))


cfg = PyFileConfigLoader("jupyterhub_config.py", path=[CFG_DIR]).load_config()
ok("設定檔可被 traitlets 載入", bool(cfg))

import dockerspawner  # noqa: E402
from jupyterhub.app import JupyterHub  # noqa: E402
from jupyterhub.auth import Authenticator, PAMAuthenticator  # noqa: E402
from jupyterhub.proxy import ConfigurableHTTPProxy  # noqa: E402
from jupyterhub.spawner import Spawner  # noqa: E402

SECTIONS = {
    "JupyterHub": JupyterHub,
    "Authenticator": Authenticator,
    "PAMAuthenticator": PAMAuthenticator,
    "Spawner": Spawner,
    "DockerSpawner": dockerspawner.DockerSpawner,
    "ConfigurableHTTPProxy": ConfigurableHTTPProxy,
}

print("-- 1. trait 名稱存在性 --------------------------------------------")
unknown = []
for section, values in cfg.items():
    cls = SECTIONS.get(section)
    ok(f"設定區段 {section} 對應到已知類別", cls is not None)
    if cls is None:
        continue
    traits = set(cls.class_traits().keys())
    for key in values:
        if key not in traits:
            unknown.append(f"{section}.{key}")
ok("沒有不存在的 trait（錯字）", not unknown, ", ".join(unknown) or "0 個")

print("-- 2. 設定值型別可被接受 ------------------------------------------")


class FakeUser:  # Spawner 建構時會讀 user.name
    name = "alice"
    url = "/user/alice"
    id = 1
    server = None

    def get_auth_state(self):
        return {}


for name, cls in (("PAMAuthenticator", PAMAuthenticator),
                  ("DockerSpawner", dockerspawner.DockerSpawner)):
    try:
        obj = cls(config=cfg) if name == "PAMAuthenticator" else cls(config=cfg, user=FakeUser())
        ok(f"{name} 能以此設定建構", True)
        if name == "DockerSpawner":
            ok("image 讀到自建 arm64 tag", obj.image == "lab/pytorch-arm64:2026.09", obj.image)
            ok("mem_limit 解析為位元組", obj.mem_limit == 48 * 1024 ** 3, str(obj.mem_limit))
            ok("GPU 以 device_requests 傳入", "device_requests" in obj.extra_host_config)
            ok("已丟棄所有 capability 並禁止提權",
               obj.extra_host_config.get("cap_drop") == ["ALL"]
               and "no-new-privileges" in obj.extra_host_config.get("security_opt", []))
            ok("容器結束後自動移除", obj.remove is True)
    except Exception as exc:  # noqa: BLE001
        ok(f"{name} 能以此設定建構", False, repr(exc))

hub_bind = cfg.JupyterHub.get("bind_url", "")
ok("Hub 只綁 127.0.0.1（不直接對公網）", "127.0.0.1" in hub_bind, hub_bind)
ok("設定了 idle-culler 服務", any(s["name"] == "idle-culler" for s in cfg.JupyterHub.services))
ok("allowed_users 是白名單而非全開", bool(cfg.Authenticator.allowed_users))
ok("admin_users 是 allowed_users 的子集",
   set(cfg.Authenticator.admin_users) <= set(cfg.Authenticator.allowed_users))

print("-- 3. pre_spawn_hook 行為 -----------------------------------------")
hook = cfg.Spawner.pre_spawn_hook
ok("pre_spawn_hook 已設定且可呼叫", callable(hook))


class FakeLog:
    def info(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


def fake_spawner(username):
    return types.SimpleNamespace(
        user=types.SimpleNamespace(name=username), log=FakeLog(),
        volumes={}, extra_create_kwargs={}, extra_host_config={},
    )


try:
    sp = fake_spawner("alice")
    hook(sp)
    ok("hook 為 alice 掛入 lung 去識別資料（ro）",
       sp.volumes.get("/data/deid/lung", {}).get("mode") == "ro")
    ok("hook 沒有掛入任何 /data/raw",
       not any(h.startswith("/data/raw") for h in sp.volumes))
    ok("容器以使用者自己的 UID:GID 執行",
       sp.extra_create_kwargs.get("user", "").split(":")[0] not in ("", "0"),
       sp.extra_create_kwargs.get("user", ""))
    ok("附加群組（group_add）已帶入容器",
       bool(sp.extra_host_config.get("group_add")), str(sp.extra_host_config.get("group_add")))
except Exception as exc:  # noqa: BLE001
    ok("hook 對 alice 正常執行", False, repr(exc))

import mount_policy  # noqa: E402

orig = mount_policy.load_policy
mount_policy.load_policy = lambda *a, **k: {
    **orig(), "common": [{"host": "/data/raw", "container": "/data/raw", "mode": "ro"}]}
try:
    hook(fake_spawner("alice"))
    ok("政策被改成掛 /data/raw 時 hook 中止 spawn", False, "竟然沒有拋出例外")
except mount_policy.PolicyViolation as exc:
    ok("政策被改成掛 /data/raw 時 hook 中止 spawn", True, str(exc))
finally:
    mount_policy.load_policy = orig

print(f"\n總計：PASS={PASS}  FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
