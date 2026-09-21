# jupyterhub_config_vm.py —— 虛擬環境覆寫層：先載入「真機用的原版設定」，只改沙盒做不到的部分。
# 原則：凡是跟安全有關的設定（PAM、掛載政策、UID、cap_drop、只綁 127.0.0.1）一律沿用原版，不在這裡改。
import os
exec(open("/srv/jupyterhub/jupyterhub_config.py", encoding="utf-8").read())

# 1) 沒有 GPU / nvidia runtime：拿掉 device_requests，其餘 host_config（cap_drop、no-new-privileges…）保留
c.DockerSpawner.extra_host_config = {k: v for k, v in c.DockerSpawner.extra_host_config.items()
                                     if k != "device_requests"}
# 2) 沒有 NGC 映像：改用 debootstrap 自建、同樣提供 jupyterhub-singleuser 的沙盒映像
c.DockerSpawner.image = "lab/singleuser-sandbox:2026.09"
# 3) 沙盒只有 1 顆 CPU（docker 會拒絕 cpu_limit 大於實體核心數）
c.Spawner.cpu_limit = 1.0
c.Spawner.mem_limit = "1G"
# 5) 閒置回收縮短成 90 秒，才能在沙盒裡實際觀察到 culling
c.JupyterHub.services[0]["command"] = [
    "/home/claude/venv/bin/python", "-m", "jupyterhub_idle_culler", "--timeout=90", "--cull-every=30"]
