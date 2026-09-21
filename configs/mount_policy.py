"""mount_policy.py —— 依 mount_policy.yaml 產生 DockerSpawner volumes，並在 spawn 前驗證。

設計重點：fail-closed。驗證不通過就丟 PolicyViolation，讓 JupyterHub 直接拒絕啟動容器，
而不是「掛錯了但容器照起」。所有 PHI 相關的保證都集中在這一個檔案裡，可單元測試。
"""
from __future__ import annotations

import grp
import os
import pwd

import yaml

DEFAULT_POLICY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mount_policy.yaml")


class PolicyViolation(Exception):
    """掛載清單違反政策；呼叫端應中止 spawn。"""


def load_policy(path: str = DEFAULT_POLICY) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        policy = yaml.safe_load(f)
    for key in ("deny", "readonly_prefixes", "common", "groups"):
        if key not in policy:
            raise PolicyViolation(f"政策檔缺少必要欄位: {key}")
    return policy


def user_groups(username: str) -> list[str]:
    """回傳該使用者所屬的群組名稱（含主群組）。"""
    try:
        pw = pwd.getpwnam(username)
    except KeyError:
        return []
    names = {g.gr_name for g in grp.getgrall() if username in g.gr_mem}
    names.add(grp.getgrgid(pw.pw_gid).gr_name)
    return sorted(names)


def _norm(path: str) -> str:
    """正規化路徑並解析 symlink —— 防 `/data/raw/../raw` 與 symlink 迂迴。"""
    return os.path.realpath(os.path.normpath(path))


def _is_within(child: str, parent: str) -> bool:
    child, parent = _norm(child), _norm(parent)
    if parent == "/":
        # 「/」在拒絕清單裡只代表「不准掛根目錄本身」；
        # 若照一般前綴規則處理，會把所有合法掛載一併擋掉（實測踩到）。
        return child == "/"
    return child == parent or child.startswith(parent.rstrip("/") + "/")


def build_volumes(username: str, groups: list[str] | None = None, policy: dict | None = None) -> dict:
    """產生 DockerSpawner 格式的 volumes：{host_path: {"bind": ..., "mode": ...}}"""
    policy = policy or load_policy()
    groups = groups if groups is not None else user_groups(username)

    specs = list(policy["common"])
    if username in (policy.get("user_overrides") or {}):
        specs += policy["user_overrides"][username]
    else:
        for g in groups:
            specs += (policy["groups"].get(g) or [])

    volumes: dict[str, dict] = {}
    for spec in specs:
        host = spec["host"].format(username=username)
        volumes[host] = {"bind": spec["container"].format(username=username), "mode": spec["mode"]}
    return volumes


def validate_volumes(volumes: dict, policy: dict | None = None) -> None:
    """違反政策即丟 PolicyViolation。這是唯一的把關點，務必在 spawn 前呼叫。"""
    policy = policy or load_policy()

    for host, opt in volumes.items():
        mode = opt["mode"] if isinstance(opt, dict) else "rw"
        bind = opt["bind"] if isinstance(opt, dict) else opt

        if mode not in ("ro", "rw"):
            raise PolicyViolation(f"掛載模式不合法: {host} -> {mode}")

        for denied in policy["deny"]:
            if _is_within(host, denied):
                raise PolicyViolation(f"禁止掛載 {host}（落在拒絕清單 {denied} 之下）")

        for prefix in policy["readonly_prefixes"]:
            if _is_within(host, prefix) and mode != "ro":
                raise PolicyViolation(f"{host} 位於 {prefix}，只能以 ro 掛載，實際為 {mode}")

        if bind.rstrip("/") in ("", "/etc", "/root", "/usr", "/var"):
            raise PolicyViolation(f"容器內掛載點會覆蓋系統目錄: {bind}")


def volumes_for(username: str, policy: dict | None = None) -> dict:
    policy = policy or load_policy()
    volumes = build_volumes(username, policy=policy)
    validate_volumes(volumes, policy=policy)
    return volumes


if __name__ == "__main__":  # 手動檢視：python mount_policy.py alice
    import json
    import sys

    for name in sys.argv[1:] or ["poyao", "alice", "bob", "guest1"]:
        print(name, json.dumps(volumes_for(name), ensure_ascii=False, indent=2, sort_keys=True))
