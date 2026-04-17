# Autobuild Onetree — 待修清單

> 內部 Build Server，依實際影響排序
> 建立日期：2026-04-17

## 目前狀態

- 已完成本地 commit：`5730357` `Stabilize housekeeping and runtime behavior`
- 已建立本地 tag：`v0.9.1`
- 已完成本地 commit：`dc11cb4` `Pin dependencies and align dashboard timezone`
- 已完成本地 commit：`b317825` `Unify DB: migrate jobs table from raw sqlite3 to SQLAlchemy ORM`
- 已完成本地 commit：`a08d3ad` `P2: CSRF protection, SQL injection fix, .env hardening, systemd security`
- 已完成本地 commit：`5382650` `Fix cleanup feedback, pin toggle, and duplicate build logs`
- `git push origin master` / `git push origin v0.9.1` 失敗：GitHub 回覆 `403 Permission denied to NathanLee_amient`
- 工作樹目前為乾淨狀態
- 實機已部署並驗證：CSRF、SQL injection guard、jobs pin 修正、settings cleanup 回應修正、runner duplicate log 修正
- 實機尚未落地：`.env` 仍為 `0644`；systemd hardening / `RuntimeMaxSec` 仍未套用；8080 file server 仍在執行

---

## P0 — 影響系統穩定性

- [x] **記憶體洩漏止血**：Uvicorn 跑 2 個月佔 30GB RAM（峰值 89GB）。加 systemd `RuntimeMaxSec=86400` 每日自動重啟
- [x] **Housekeeping prune 改進**：prune 邏輯修正 — artifacts/logs 保留（原設計正確），新增清理 HOME 殘留目錄 (.cache/.config/.local/.npm)，prune 失敗改為 warning 而非靜默
- [x] **清理 `/opt/autobuild/workspace/job-{7,8}`**：已手動刪除，釋放系統碟 ~13 GB

## P1 — 影響維護性與正確性

- [x] **時間設定收斂**：確認 jobs 頁面的雙重轉換 bug 已不存在；dashboard 改為使用統一 app timezone，移除硬編碼 `GMT+8`
- [x] **`requirements.txt` 版本鎖定**：已依 deployed virtualenv 版本鎖定，避免下次部署拉到 breaking change
- [x] **Dual DB 架構統一**：jobs 表已遷移至 SQLAlchemy ORM（`crud_jobs.py`），db.py 僅保留 migration DDL 和 projects.py 相容層
- [x] **`_spec_locks` dict 記憶體洩漏**：已加上限 200 + 自動清理未持有的 lock
- [x] **減少 jobs 頁輪詢負擔**：badge 改用輕量 `/jobs/{id}/status` endpoint（~100B）取代 HTMX 全頁重渲染；log 完成後停止輪詢；jobs 列表頁無 active job 時停止自動刷新

### 建議下一步順序

1. 先處理 **減少 jobs 頁輪詢負擔**：改動小、風險低、容易驗證
2. 再規劃 **Dual DB 架構統一**：屬於結構性重構，適合拆成多次小改

## P2 — 良好實踐，有空再做

- [ ] **`.env` 權限收緊**：repo 的 deploy script 已加 `chmod 600` 與 `chown`，但實機 `/opt/autobuild/.env` 目前仍為 `0644`
- [ ] **GitLab token 儲存**：明文 JSON 存在 filesystem，目前 secrets 目錄已限 `scm-bmc` group，風險可控
- [x] **SQL injection in db migration**：`_validate_identifier()` + `_DDL_RE` 防禦已加入 `db.py`
- [x] **Path traversal 檢查**：`routes/jobs.py` `_safe_job_dir()` 改用 `Path.is_relative_to()`，移除 debug print
- [x] **CSRF 保護**：Synchronizer Token Pattern 已實作（`csrf.py`），已部署並完成基本 smoke 驗證
- [ ] **HTTPS**：純內網可接受，若需要可加自簽憑證
- [ ] **Systemd security directives**：repo 內已有候選設定，但實機目前仍為 `ProtectSystem=no`、`ProtectHome=no`、`PrivateTmp=no`、`NoNewPrivileges=no`；現有版本不建議直接部署
- [ ] **Port 8080 file server**：`python3 -m http.server 8080` 由 nathan 跑在 0.0.0.0，確認是否仍需要
