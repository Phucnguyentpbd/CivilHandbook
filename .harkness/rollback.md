# Rollback Plan — CivilHandbook v1

**Cập nhật:** 2026-10-13

## Phạm vi rollback

Rollback toàn hệ thống khi một giai đoạn gây lỗi nghiêm trọng không thể sửa nhanh.

## Bước rollback (theo thứ tự)

1. **Khôi phục cấu hình agent**
   - Mỗi file cấu hình MCP đã được sao lưu trước khi sửa với timestamp.
   - Khôi phục từ bản sao có timestamp gần nhất.
   - Không sửa MCP Civil, Harkness, Excel, VNStock hoặc MCP khác.

2. **Vô hiệu hóa skill đã phát hành**
   - Xóa hoặc rename file skill `civil-handbook.md` khỏi:
     - Kho skill Codex.
     - `G:\My Drive\CodexSync\skills\shared`.
   - Khởi động lại phiên làm việc để agent không còn nhận dạng kích hoạt.

3. **Giữ nguyên dữ liệu**
   - **KHÔNG xóa** `D:\AGENT_AI\CivilHandbook`.
   - Dữ liệu Markdown/JSON vẫn còn nguyên để cài lại hoặc export thủ công.
   - **KHÔNG xóa / chỉnh** `GOOGLE_API_KEY`.

4. **Migration dữ liệu (nếu có)**
   - Backup + test phục hồi trước khi chạy bất kỳ migration schema nào.
   - Nếu schema mới lỗi, quay lại mã MCP trước đó nhưng giữ revision tương thích.

## Kiểm tra sau rollback

- Mở phiên mới ở từng agent.
- Xác nhận `civil_handbook_*` không còn xuất hiện trong danh sách tool.
- Xác nhận các MCP khác (Civil, Excel, VNStock...) vẫn hoạt động bình thường.

## Ghi chú

- Rollback chỉ cần thực hiện khi có sự cố nghiêm trọng.
- Giai đoạn thấp hơn có thể sửa tại chỗ mà không cần rollback toàn hệ thống.
