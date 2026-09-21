# jupyterhub_config.py —— DGX Spark 多帳號入口（arm64 / DGX OS 7）
#
# 三個不可妥協的設計點：
#   1. 只有 Hub 服務帳號持有 docker socket；使用者沒有 docker 指令權（見 §3.3）。
#   2. Hub 只聽 127.0.0.1，外網一律經 Cloudflare Tunnel + Access 進來；防火牆 0 個 inbound port。
#   3. 掛載清單由 mount_policy.yaml 產生並在 spawn 前驗證，違規則拒絕啟動（fail-closed）。
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mount_policy import PolicyViolation, user_groups, volumes_for  # noqa: E402

# ── 身分驗證：直接用系統帳號（UID/權限/ACL 只有一套） ─────────────────────────
c.JupyterHub.authenticator_class = "jupyterhub.auth.PAMAuthenticator"
c.Authenticator.allowed_users = {"poyao", "alice", "bob", "guest1"}
c.Authenticator.admin_users = {"poyao"}
c.PAMAuthenticator.open_sessions = False        # 不開 PAM session，避免容器化下的 session 殘留
c.Authenticator.delete_invalid_users = True

# ── 網路：只綁 loopback，由 cloudflared 從本機轉發 ───────────────────────────
c.JupyterHub.bind_url = "http://127.0.0.1:8000"
# Hub API（:8081）只綁在 jupyterhub-net 的閘道上，讓容器連得到、但外部介面連不到。
# 不可寫 "0.0.0.0"：VM 實測發現那會讓 Hub 登入頁在 LAN 的 :8081 直接可達，
# 繞過 proxy 與 Cloudflare Access（第二因素）。閘道位址自動從 docker 查詢，不用手填。
def _docker_net_gateway(name="jupyterhub-net"):
    if os.environ.get("HUB_GW"):
        return os.environ["HUB_GW"]
    import docker  # dockerspawner 的相依套件
    return docker.from_env().networks.get(name).attrs["IPAM"]["Config"][0]["Gateway"]


c.JupyterHub.hub_ip = _docker_net_gateway()
c.JupyterHub.hub_connect_ip = c.JupyterHub.hub_ip
c.JupyterHub.cleanup_servers = False            # Hub 重啟不殺掉正在跑的容器

# ── Spawner：DockerSpawner ──────────────────────────────────────────────────
c.JupyterHub.spawner_class = "dockerspawner.DockerSpawner"
c.DockerSpawner.image = "lab/pytorch-arm64:2026.09"   # 自建映像，tag = 環境版本
c.DockerSpawner.remove = True
c.DockerSpawner.notebook_dir = "/workspace"
c.DockerSpawner.debug = False
c.DockerSpawner.network_name = "jupyterhub-net"

# 資源上限：CPU 側有效；統一記憶體架構下 GPU 記憶體擋不住（見 §3.1），僅為防呆
c.Spawner.mem_limit = "48G"
c.Spawner.cpu_limit = 12.0
c.Spawner.start_timeout = 300                   # 首次 pull 大映像會久
c.Spawner.http_timeout = 120
c.Spawner.default_url = "/lab"

c.DockerSpawner.extra_host_config = {
    "device_requests": [                        # 等同 --gpus all（Spark 不支援 MIG/vGPU，只能時間分享）
        {"Driver": "nvidia", "Count": -1, "Capabilities": [["gpu"]]}
    ],
    "shm_size": "8g",                           # DataLoader workers 需要
    "security_opt": ["no-new-privileges"],
    "cap_drop": ["ALL"],
    "pids_limit": 4096,
}
c.DockerSpawner.environment = {
    "NVIDIA_DRIVER_CAPABILITIES": "compute,utility",
    "PYTHONNOUSERSITE": "1",                    # 避免 ~/.local 汙染映像環境
    "HF_HOME": "/workspace/.cache/huggingface",
}


# ── spawn 前把「政策」變成「實際掛載」，並以使用者身分執行容器 ────────────────
def apply_mount_policy(spawner):
    username = spawner.user.name
    try:
        volumes = volumes_for(username)
    except PolicyViolation as exc:
        spawner.log.error("掛載政策驗證失敗，拒絕為 %s 啟動容器：%s", username, exc)
        raise                                   # fail-closed：寧可起不來，不可掛錯
    spawner.volumes = volumes

    import grp
    import pwd

    pw = pwd.getpwnam(username)
    # 容器以使用者自己的 UID/GID 執行：寫出來的檔案屬於本人，
    # 也讓 ro 掛載之外的權限（例如 /shared/models 的群組）與主機一致。
    spawner.extra_create_kwargs = {"user": f"{pw.pw_uid}:{pw.pw_gid}"}
    gids = [str(grp.getgrnam(g).gr_gid) for g in user_groups(username) if g != username]
    spawner.extra_host_config = {**c.DockerSpawner.extra_host_config, "group_add": gids}
    spawner.log.info("為 %s 掛載：%s", username, sorted(volumes))


c.Spawner.pre_spawn_hook = apply_mount_policy

# ── 閒置回收與服務 ──────────────────────────────────────────────────────────
c.JupyterHub.services = [
    {
        "name": "idle-culler",
        "command": [sys.executable, "-m", "jupyterhub_idle_culler", "--timeout=14400"],
    }
]
c.JupyterHub.load_roles = [
    {
        "name": "idle-culler",
        "scopes": ["list:users", "read:users:activity", "read:servers", "delete:servers"],  # 缺 read:servers 時 culler 看不到任何伺服器，永遠不回收（VM 實測）
        "services": ["idle-culler"],
    }
]

# ── 稽核 ────────────────────────────────────────────────────────────────────
c.JupyterHub.log_level = "INFO"
c.JupyterHub.extra_log_file = "/var/log/jupyterhub/jupyterhub.log"   # 保留 ≥180 天
c.JupyterHub.db_url = "sqlite:////srv/jupyterhub/jupyterhub.sqlite"
c.JupyterHub.cookie_secret_file = "/srv/jupyterhub/cookie_secret"
# proxy token 不寫在設定檔裡；由 systemd unit 以 EnvironmentFile 帶入 CONFIGPROXY_AUTH_TOKEN
# （原本寫成 auth_token_file，但 ConfigurableHTTPProxy 並沒有這個 trait —— 被 validate 抓到）
c.ConfigurableHTTPProxy.auth_token = os.environ.get("CONFIGPROXY_AUTH_TOKEN", "")
