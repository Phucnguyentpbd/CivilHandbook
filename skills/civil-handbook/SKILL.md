---
name: civil-handbook
description: >
  Sổ tay kinh nghiệm Civil thông minh dùng chung cho Codex, Orca và Antigravity.
  Tự động tra cứu kinh nghiệm khi gặp vấn đề Civil 3D/AutoCAD/QGIS/plugin.
  Bảo lưu kinh nghiệm đã kiểm chứng khi người dùng nói "bảo lưu", "lưu vào sổ tay Civil".
---

# Sổ tay kinh nghiệm Civil thông minh (CivilHandbook)

Kỹ năng tra cứu và bảo lưu tri thức, bài học kinh nghiệm xử lý lỗi, thủ thuật thiết kế trong Autodesk Civil 3D, AutoCAD, QGIS và các plugin liên quan.

---

## 1. Quy tắc kích hoạt quan trọng

### Khi nào TỰ ĐỘNG tra cứu:
- Bất kỳ khi nào người dùng hỏi về:
  + Lỗi phần mềm, mã lỗi (eHatchError, eWasErased, eLockViolation...), lệnh CAD bị đơ/vô hiệu.
  + Thao tác xử lý đối tượng Civil 3D (Surface, Alignment, Profile, Section, Corridor, Pipe Network...).
  + Sử dụng plugin Civil, AutoCAD Map, tool Civil nội bộ.
  + Cách xử lý quy chuẩn, tiêu chuẩn kỹ thuật (TCVN 5729, QCVN 41, hệ tọa độ VN2000...).

### Khi nào BẢO LƯU vào sổ tay:
- Chỉ bảo lưu khi người dùng CHỦ ĐỘNG nói:
  + “bảo lưu”
  + “lưu vào sổ tay Civil”
  + “lưu kinh nghiệm này”
  + “ghi nhớ ca này”
  + hoặc gõ lệnh `/civil-note-save`

> [!CAUTION]
> **TUYỆT ĐỐI KHÔNG KÍCH HOẠT SỔ TAY KHI NGƯỜI DÙNG NÓI “LƯU BẢN VẼ” HOẶC “LƯU FILE”**.
> “Lưu bản vẽ” là thao tác CAD (`QSAVE`), không liên quan đến Sổ tay kinh nghiệm!

### Khi nào BỎ QUA sổ tay:
- Khi người dùng nói: “không dùng sổ tay”, “không cần tra sổ tay”.

---

## 2. Quy trình tra cứu (Search Flow)

Khi phát hiện câu hỏi liên quan đến Civil 3D:

1. **Gọi MCP Tool `civil_handbook_search_cases`**:
   - `query`: câu hỏi hoặc từ khóa lỗi/lệnh/plugin.
   - `civil_version`: phiên bản Civil hiện tại (nếu biết, ví dụ "2027").
   - `limit`: tối đa 5 kết quả.

2. **Quy tắc đưa vào ngữ cảnh**:
   - **Chỉ đưa tối đa 3 mục phù hợp nhất** vào câu trả lời cho người dùng.
   - Nếu kết quả trả về `status: "not_found"` hoặc rỗng:
     * Nói rõ: *"Chưa có mục phù hợp trong Sổ tay Civil."*
     * Tuyệt đối không kéo một mục xa nghĩa vào câu trả lời.
   - Khi áp dụng kinh nghiệm từ sổ tay, câu trả lời **PHẢI** nêu rõ:
     * Mã mục (`CIV-xxxx`) và revision.
     * Mức kiểm chứng:
       - `verified_live`: Đã kiểm nghiệm thực tế trực tiếp trên bản vẽ Civil 3D.
       - `verified_offline`: Đã kiểm tra qua build/test/tài liệu chính thức.
       - `partial`: Mới kiểm tra một phần.
       - `unverified`: Giả thuyết hoặc hướng dẫn tham khảo.
     * Điều kiện áp dụng / cảnh báo nếu có.
   - **Xử lý mâu thuẫn**: Nếu có 2 mục mâu thuẫn (`conflicts_with`), phải nêu cả 2 mục và điều kiện áp dụng riêng của từng phiên bản; không tự ý hợp nhất.

---

## 3. Quy trình bảo lưu (Save Flow)

Khi người dùng nói "bảo lưu":

1. **Thu thập và cô đọng tri thức**:
   - Lấy đúng phần kết luận và bằng chứng hữu ích từ phiên làm việc vừa rồi (KHÔNG chép toàn bộ hội thoại).
   - Xác định:
     * **Tiêu đề**: Ngắn gọn, nêu bật triệu chứng và giải pháp.
     * **Tóm tắt**: 1-2 câu cô đọng (dùng để tìm kiếm).
     * **Vấn đề / Triệu chứng**: Mô tả cụ thể lỗi hoặc tình huống.
     * **Bối cảnh**: Phiên bản Civil 3D, loại bản vẽ, hệ tọa độ.
     * **Nguyên nhân**: Đã xác định rõ hay còn là giả thuyết (`cause_is_hypothesis`).
     * **Các bước xử lý**: Danh sách bước cụ thể, mạch lạc.
     * **Kết quả & Bằng chứng**: Kết quả đo đạc/kiểm tra thực tế sau khi xử lý.
     * **Những cách đã thử nhưng thất bại**: Ghi lại để người khác không lặp lại sai lầm.
     * **Cảnh báo / Phạm vi / Hoàn tác**: Rủi ro, điều kiện không được dùng.
     * **Nhóm Civil**: Thuộc 18 nhóm phân loại chuẩn (Surface, Alignment, Corridor...).
     * **Plugin liên quan**: Tên plugin, version, lệnh liên quan (nếu có).
     * **Mức kiểm chứng**: `verified_live` chỉ khi vừa thấy kết quả thực tế trên bản vẽ; nếu không thì `verified_offline` hoặc `partial`.

2. **Kiểm tra bảo mật (Secret Scanning)**:
   - Không đưa API key, mật khẩu, connection string, token hay đường dẫn nhạy cảm vào nội dung.

3. **Gọi MCP Tool `civil_handbook_save_case`**:
   - Truyền `idempotency_key` duy nhất để tránh tạo mục trùng lặp nếu gọi lại.

4. **Báo cáo lại cho người dùng**:
   - Trả lời ngắn gọn:
     * Mã mục được cấp: `CIV-xxxx` (Revision 1).
     * Tiêu đề & Nhóm đối tượng Civil.
     * Mức kiểm chứng.
     * Mã thao tác hoàn tác (`operation_id`) và cách sửa nếu cần.

---

## 4. Quy trình sửa & hoàn tác (Revise & Undo)

- **Khi người dùng nói "sửa mục CIV-xxxx" hoặc "bổ sung mục CIV-xxxx"**:
  1. Đọc nội dung hiện tại bằng `civil_handbook_get_case(case_id="CIV-xxxx")`.
  2. Xác định revision hiện tại (`expected_revision`).
  3. Gọi `civil_handbook_revise_case` với `expected_revision` và `revision_reason`. Không ghi đè revision cũ.
- **Khi người dùng nói "hoàn tác mục vừa lưu" hoặc "hoàn tác thao tác..."**:
  1. Gọi `civil_handbook_undo_change(operation_id=...)` để hoàn tác bằng reverse revision.

---

## 5. Quy trình đồng bộ GitHub (Sync Flow)

- **Khi người dùng nói**: "đồng bộ sổ tay", "cập nhật sổ tay từ github", "sync sổ tay":
  1. Gọi MCP Tool `civil_handbook_sync`.
  2. Công cụ này sẽ tự động kéo các ca mới nhất từ GitHub (`git pull --rebase`) và tự động dựng lại chỉ mục SQLite cục bộ với các ca mới.
  3. Báo cáo ngắn gọn: trạng thái đồng bộ và số ca mới được nạp vào máy.

