# DGX Spark 使用規則（開帳號時簽署）

1. 原始院內資料（`/data/raw`）只能由 admin 在主機上處理；任何容器、任何使用者都掛不到，也不得自行複製。
2. 去識別後的資料（`/data/deid/<題目>`）以唯讀掛載使用，不得複製到個人筆電、外接硬碟或任何雲端硬碟。
3. GPU 無法硬切分（Spark 不支援 MIG／vGPU），大型訓練前請先在 `ops/GPU_SCHEDULE.md` 登記時段。
4. 不得要求加入 `docker` 群組；需要 CLI 容器者由 admin 開通 rootless Docker。
5. 帳號與金鑰不得共用或轉借；離開實驗室當日撤銷 Tailscale 與 Cloudflare Access 授權。
6. 訓練請跑在 detached 容器或 tmux 內，不要跑在 Jupyter kernel 裡（連線斷掉就沒了）。
7. 個人 `/workspace` 有容量上限；請自行清理中間檔，不要把 checkpoint 全部留著。
