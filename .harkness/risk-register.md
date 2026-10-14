# Risk Register — CivilHandbook v1

**Cập nhật:** 2026-10-13

| ID | Rủi ro | Xác suất | Tác động | Biện pháp giảm thiểu | Trạng thái |
|---|---|---|---|---|---|
| R-01 | Google API Key không hợp lệ / đã bị từ chối (chính sách mới T9/2026) | Trung bình | Cao | Gọi embed kiểm tra 1 lần lúc cài; chặn 401/403, không tự đổi provider; dùng FTS fallback | Mở |
| R-02 | Conflict ghi đồng thời từ 3 agent | Thấp | Trung bình | SQLite WAL + busy_timeout=5000 + expected_revision + idempotency_key | Đã xử lý |
| R-03 | Mất SQLite (hỏng file) | Thấp | Cao | Nguồn chân lý là Markdown/JSON; `rebuild_index_from_sources()` phục hồi hoàn toàn | Đã xử lý |
| R-04 | Lộ bí mật (API key, token, cookie) trong nội dung ca | Trung bình | Cao | `secret_scanner.py` chạy trước lưu; cảnh báo và chặn nếu phát hiện | Đã xử lý |
| R-05 | Google Drive không sẵn sàng khi backup | Thấp | Thấp | Lưu cục bộ thành công; status = `backup_pending`; retry tự động 6 giờ | Đã xử lý |
| R-06 | Path traversal trong evidence | Thấp | Cao | Kiểm tra đường dẫn tuyệt đối trong evidence root; từ chối symlink ngoài root | Đã xử lý |
| R-07 | Agent tự nâng mức `verified_live` sai | Trung bình | Trung bình | Rule rõ trong spec + validation model; chỉ người dùng xác nhận | Mở |
| R-08 | MCP sổ tay vô tình gọi civil3d_execute | Thấp | Cao | Không import/khai báo civil3d tools; test kiểm tra không có đường gọi | Đã xử lý |
| R-09 | Import DLL từ PackageContents.xml | Thấp | Cao | `scan_package_contents()` chỉ đọc XML, không load DLL | Đã xử lý |
| R-10 | SQLite không đủ hiệu năng cho FTS với dữ liệu lớn | Thấp | Thấp | v1 quy mô nhỏ; có thể migrate sau | Theo dõi |
