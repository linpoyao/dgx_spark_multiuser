#!/usr/bin/env python3
"""audit_arm64_wheels.py —— 盤點醫學影像相依套件在 linux aarch64 上的安裝方式。

為什麼要做：DGX Spark 是 arm64（GB10 Grace）。PyPI 上不是每個套件都有 aarch64 wheel，
沒有就得在映像裡現場編譯。這份清單決定 Dockerfile 要不要塞 build-essential / cmake，
也決定「建映像要 20 分鐘還是 2 小時」。在筆電（x86）上 pip install 成功不代表 Spark 上可行。

用法：python3 tests/audit_arm64_wheels.py [--python 312] [--markdown out.md]
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

PACKAGES = [
    # 影像 I/O 與格式
    "pydicom", "SimpleITK", "nibabel", "itk", "highdicom", "dicom2nifti", "pylibjpeg",
    # 科學計算
    "numpy", "scipy", "pandas", "scikit-learn", "scikit-image", "h5py", "opencv-python-headless",
    # 醫學影像模型生態
    "monai", "torchio", "batchgenerators", "nnunetv2", "connected-components-3d",
    "lungmask", "TotalSegmentator", "pyradiomics",
    # 訓練與工具
    "timm", "einops", "transformers", "tensorboard", "matplotlib", "tqdm", "PyYAML",
    "jupyterlab", "notebook", "xgboost", "optuna",
    # 伺服器端
    "jupyterhub", "dockerspawner", "jupyterhub-idle-culler",
]


def fetch(pkg: str) -> dict | None:
    url = f"https://pypi.org/pypi/{pkg}/json"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.load(r)
    except Exception:
        return None


def classify(meta: dict, pytag: str) -> tuple[str, str]:
    """回傳 (結論, 說明)。結論 ∈ {PURE, WHEEL, SDIST, NOTFOUND}"""
    files = meta["urls"]
    version = meta["info"]["version"]
    wheels = [f["filename"] for f in files if f["packagetype"] == "bdist_wheel"]
    has_sdist = any(f["packagetype"] == "sdist" for f in files)

    pure = [w for w in wheels if "-none-any.whl" in w]
    # 只看 linux aarch64（manylinux/musllinux），macOS 的 arm64 不算
    aarch = [w for w in wheels if "aarch64" in w and "linux" in w]
    target = int(pytag)

    def compatible(fn: str) -> bool:
        parts = fn[:-4].split("-")            # name-version-pytag-abitag-plat
        if len(parts) < 5:
            return False
        pytags, abitag = parts[-3], parts[-2]
        # py3-none-<plat>：任何 CPython 3 都能用（例如 xgboost）
        if pytags.startswith("py3") or pytags.startswith("py2.py3"):
            return True
        # cpXY-abi3：穩定 ABI，XY 以上的 CPython 都能用（例如 SimpleITK / itk / opencv）
        if abitag == "abi3" and pytags.startswith("cp"):
            return int(pytags[2:]) <= target
        return f"cp{pytag}" in pytags

    aarch_ok = [w for w in aarch if compatible(w)]

    if aarch_ok:
        kinds = "abi3" if any("abi3" in w for w in aarch_ok) else (
            "py3-none" if any(w.split("-")[-3].startswith("py3") for w in aarch_ok) else f"cp{pytag}")
        return "WHEEL", f"{version}｜有可用的 linux aarch64 wheel（{kinds}）"
    if pure:
        return "PURE", f"{version}｜純 Python wheel，任何架構可用"
    if aarch:
        tags = sorted({w.split("-")[-3] for w in aarch})
        return "WHEEL*", f"{version}｜有 aarch64 wheel 但都不相容 cp{pytag}（{", ".join(tags)}）"
    if has_sdist:
        return "SDIST", f"{version}｜只有原始碼，需在 arm64 上編譯"
    return "NOTFOUND", f"{version}｜PyPI 上沒有可用的 linux aarch64 檔案"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", default="312", help="CPython tag，例如 312 / 311")
    ap.add_argument("--markdown", help="另外輸出 markdown 表格到檔案")
    args = ap.parse_args()

    rows = []
    for pkg in PACKAGES:
        meta = fetch(pkg)
        if meta is None:
            rows.append((pkg, "NOTFOUND", "PyPI 查不到此套件名稱"))
            continue
        verdict, note = classify(meta, args.python)
        rows.append((pkg, verdict, note))
        print(f"{verdict:<9} {pkg:<28} {note}")

    need_build = [r[0] for r in rows if r[1] in ("SDIST", "WHEEL*", "NOTFOUND")]
    print("\n── 摘要 ──────────────────────────────────────────────")
    for v in ("PURE", "WHEEL", "WHEEL*", "SDIST", "NOTFOUND"):
        n = sum(1 for r in rows if r[1] == v)
        if n:
            print(f"{v:<9} {n} 個")
    print("需要在映像內編譯／需人工確認：" + (", ".join(need_build) or "（無）"))

    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as f:
            f.write(f"| 套件 | 結論 | 說明（cp{args.python} / linux aarch64） |\n|---|---|---|\n")
            for pkg, verdict, note in rows:
                f.write(f"| `{pkg}` | {verdict} | {note} |\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
