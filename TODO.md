# Autobuild Onetree — 待修清單

> 內部 Build Server，依實際影響排序
> 建立日期：2026-04-17

---

## P0 — 影響系統穩定性

- [x] **記憶體洩漏止血**：Uvicorn 跑 2 個月佔 30GB RAM（峰值 89GB）。加 systemd `RuntimeMaxSec=86400` 每日自動重啟
- [x] **Housekeeping prune 改進**：prune 邏輯修正 — artifacts/logs 保留（原設計正確），新增清理 HOME 殘留目錄 (.cache/.config/.local/.npm)，prune 失敗改為 warning 而非靜默
- [x] **清理 `/opt/autobuild/workspace/job-{7,8}`**：已手動刪除，釋放系統碟 ~13 GB

## P1 — 影響維護性與正確性

- [x] **時間設定收斂**：確認 jobs 頁面的雙重轉換 bug 已不存在；dashboard 改為使用統一 app timezone，移除硬編碼 `GMT+8`
- [x] **`requirements.txt` 版本鎖定**：已依 deployed virtualenv 版本鎖定，避免下次部署拉到 breaking change
- [ ] **Dual DB 架構統一**：SQLAlchemy ORM（SystemSettings）+ raw sqlite3（jobs）並存，增加維護複雜度。長期應統一
- [x] **`_spec_locks` dict 記憶體洩漏**：已加上限 200 + 自動清理未持有的 lock
- [ ] **減少 jobs 頁輪詢負擔**：log 已用 SSE；目前仍以 4-5 秒 polling 更新 status/artifacts，可評估合併或降頻

## P2 — 良好實踐，有空再做

- [ ] **`.env` 權限收緊**：目前 `-rw-r--r--` 所有人可讀（含 SMTP 密碼），改為 `0600`
- [ ] **GitLab token 儲存**：明文 JSON 存在 filesystem，目前 secrets 目錄已限 `scm-bmc` group，風險可控
- [ ] **SQL injection in db migration**：`app/db.py` 用 f-string 拼 ALTER TABLE，值來自程式碼常量非用戶輸入，風險極低
- [ ] **Path traversal 檢查**：`routes/jobs.py` `_safe_job_dir()` 用字串比對，應改用 `Path.is_relative_to()`
- [ ] **CSRF 保護**：內網信任用戶，風險低，但 POST endpoint 目前皆無 CSRF token
- [ ] **HTTPS**：純內網可接受，若需要可加自簽憑證
- [ ] **Systemd security directives**：目前 `ProtectSystem=off`，內部機器風險可控
- [ ] **Port 8080 file server**：`python3 -m http.server 8080` 由 nathan 跑在 0.0.0.0，確認是否仍需要
