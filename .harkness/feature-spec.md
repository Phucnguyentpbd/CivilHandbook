# Feature Spec — Sổ tay kinh nghiệm Civil

**Phiên bản:** v1.0  
**Ngày:** 2026-10-13  
**Trạng thái:** Đang triển khai  

---

## Mục tiêu

Xây dựng kho kinh nghiệm Civil 3D dùng chung cho Codex, Orca và Antigravity, lưu trên máy cục bộ, tìm kiếm lai (FTS5 + semantic), và đồng bộ backup một chiều lên Google Drive.

## Phạm vi v1

- Ghi ca mới (`bảo lưu`) với mã `CIV-xxxx`, revision, idempotency.
- Đọc / sửa / hoàn tác ca.
- Tìm kiếm lai: FTS5/BM25 + embedding Google `gemini-embedding-2` dim=768 + RRF.
- MCP stdio: 6 tool + 2 resource taxonomy (read-only).
- Skill nhận dạng ngữ cảnh: bảo lưu tự động / tra cứu tự động.
- Backup một chiều sang `G:\My Drive\CodexSync\backups\CivilHandbook`.
- Ba agent dùng chung SQLite WAL; không có dashboard riêng.

## Ngoài phạm vi v1

- 9router, dashboard web, mobile, import hàng loạt, chia sẻ nhóm.
- Gọi `civil3d_execute` từ MCP sổ tay.

## Giao diện & hành vi chính

| Kích hoạt | Hành động |
|---|---|
| "bảo lưu", "lưu vào sổ tay Civil", `/civil-note-save` | Lưu ca mới hoặc revision |
| "sửa mục CIV-xxxx" | Tạo revision mới |
| "hoàn tác mục vừa lưu" | Tạo reverse revision |
| "tra sổ tay…" | Tìm kiếm rõ ràng |
| Mọi câu hỏi Civil/plugin/MCP | Tra tự động, nạp ≤ 3 mục |
| "không dùng sổ tay" | Bỏ qua tra tự động |
| "lưu bản vẽ" | **KHÔNG** kích hoạt sổ tay |

## Trạng thái kiểm chứng

- `verified_live` — thấy hậu điều kiện trực tiếp trong Civil.
- `verified_offline` — kiểm tra build/test/tài liệu chính thức.
- `partial` — kiểm tra một phần.
- `unverified` — hướng dẫn/giả thuyết.

## Điều kiện nghiệm thu giai đoạn 2

- `pytest tests/test_storage.py` PASS toàn bộ.
- Cấu trúc thư mục đúng spec.
- Schema SQLite khởi tạo không lỗi.
- ID allocator trả `CIV-xxxx` đúng chuỗi, thread-safe.
