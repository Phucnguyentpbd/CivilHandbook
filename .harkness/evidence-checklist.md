# Evidence Checklist — CivilHandbook v1

**Cập nhật:** 2026-10-13

Mỗi giai đoạn phải có bằng chứng cụ thể trước khi chuyển sang giai đoạn tiếp theo.

---

## Giai đoạn 1: Preflight & Harkness

- [ ] Cấu trúc thư mục `D:\AGENT_AI\CivilHandbook\` đúng spec.
- [ ] Bốn file `.harkness/` đã tạo và có nội dung đầy đủ.
- [ ] Snapshot ba file cấu hình agent đã lưu (trước khi sửa bất kỳ file nào).

## Giai đoạn 2: Data Layer

- [ ] `pytest tests/test_storage.py` PASS 100% (không có FAIL, ERROR).
- [ ] `src/handbook/config.py` — tất cả constant đúng giá trị spec.
- [ ] `src/handbook/models.py` — đủ enum và Pydantic model, validate đúng.
- [ ] `src/handbook/id_allocator.py` — `allocate_id()` trả `CIV-XXXX`, thread-safe.
- [ ] `src/handbook/secret_scanner.py` — phát hiện đúng token/password/path traversal.
- [ ] `src/handbook/taxonomy.py` — load JSON và scan PackageContents.xml không load DLL.
- [ ] `src/handbook/storage.py` — `init_db()`, `atomic_save()`, `get_case()`, `undo_change()`, `rebuild_index_from_sources()` hoạt động.
- [ ] `src/handbook/concurrency.py` — `write_lock()` context manager hoạt động.
- [ ] `data/taxonomy/civil_groups.json` — 18 nhóm đúng tên.
- [ ] `data/taxonomy/plugins.json` — danh sách plugin khởi đầu.
- [ ] SQLite WAL mode được xác nhận bằng `PRAGMA journal_mode`.

## Giai đoạn 3: Tìm kiếm & Google Provider

- [ ] FTS5 tìm đúng với câu không dấu và câu có dấu.
- [ ] Google API Key hợp lệ (xác nhận bằng embed vô hại).
- [ ] `semantic_pending` được đặt khi embedding lỗi; file nguồn vẫn lưu thành công.
- [ ] RRF trả kết quả đúng thứ tự ưu tiên.

## Giai đoạn 4: MCP & Skill

- [ ] Sáu MCP tool hoạt động (gọi thử từ MCP Inspector hoặc agent).
- [ ] Hai taxonomy resource trả JSON đúng.
- [ ] Skill nhận dạng đúng "bảo lưu" và không nhầm "lưu bản vẽ".
- [ ] `civil_handbook_health` không lộ API key.

## Giai đoạn 5: Ba Agent & Backup

- [ ] `civil_handbook_health` trả OK từ cả ba agent (Codex, Orca, Antigravity).
- [ ] Ba agent thực sự dùng chung một kho (tạo ca từ một agent, tìm được từ agent khác).
- [ ] Backup tạo manifest/checksum và đẩy lên `G:\My Drive\CodexSync\backups\CivilHandbook`.
- [ ] Ngắt mạng: lưu thành công, status = `backup_pending`.

## Giai đoạn 6: Nghiệm thu

- [ ] Bộ 10+ câu hỏi eval PASS (độ chính xác đạt tiêu chuẩn).
- [ ] Luồng đầy đủ: lưu từ Codex → tìm từ Orca → sửa từ Antigravity → hoàn tác → xác nhận.
- [ ] `restore-check` khớp số mục, revision và SHA-256.
- [ ] Tài liệu hướng dẫn tiếng Việt đã bàn giao.
