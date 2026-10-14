# Sổ tay kinh nghiệm Civil thông minh (CivilHandbook)

Sổ tay tri thức & kinh nghiệm thực chiến dành riêng cho Autodesk Civil 3D, AutoCAD, QGIS và các plugin hạ tầng, dùng chung đồng bộ giữa **Codex**, **Orca** và **Antigravity**.

---

## 1. Nguyên tắc hoạt động cốt lõi

1. **Kho cá nhân dùng chung**: Bắt đầu trống, chỉ ghi nhận những kinh nghiệm được người dùng chủ động nói **"bảo lưu"**. Không tự động quét chat, memory, PDF hay tài liệu cũ.
2. **Nguồn chân lý là Markdown/JSON**: Mỗi ca lưu tại `data/cases/CIV-xxxx/`, có `case.json` và các file revision `0001.md`, `0002.md` bất biến. SQLite chỉ là chỉ mục dẫn xuất (FTS5 + vector). Nếu SQLite hỏng hoặc xóa, toàn bộ kho dựng lại hoàn hảo 100% bằng lệnh `rebuild_index_from_sources()`.
3. **Phân biệt rạch ròi với CAD**: Lệnh *"lưu bản vẽ"* / *"lưu file"* là thao tác CAD (`QSAVE`), **tuyệt đối không kích hoạt Sổ tay**. Chỉ kích hoạt khi nói *"bảo lưu"*, *"lưu vào sổ tay Civil"*.
4. **Giới hạn nạp ngữ cảnh**: Khi người dùng hỏi lỗi/thủ thuật Civil, agent tự động tra cứu nhưng **chỉ nạp tối đa 3 mục phù hợp nhất** vào câu trả lời, nêu rõ mã `CIV-xxxx`, mức kiểm chứng (`verified_live`, `verified_offline`, `partial`, `unverified`) và điều kiện áp dụng. Nếu không có mục phù hợp, agent nói rõ *"chưa có mục phù hợp"*.
5. **Không sửa DWG**: MCP Sổ tay là công cụ độc lập, tuyệt đối không có quyền gọi `civil3d_execute` hay can thiệp bản vẽ CAD.

---

## 2. Cách tương tác với Agent

| Thao tác | Câu lệnh mẫu | Hành vi của Agent |
|---|---|---|
| **Bảo lưu kinh nghiệm** | *"Bảo lưu ca này"*, *"Lưu vào sổ tay Civil"* | Trích xuất triệu chứng, nguyên nhân, bước xử lý, kết quả; cấp mã `CIV-xxxx`, revision 1 và mã hoàn tác `operation_id`. |
| **Tra cứu tự động** | Mọi câu hỏi về lỗi Civil 3D, lệnh, plugin, quy chuẩn | Agent tự tra sổ tay, nạp tối đa 3 mục phù hợp nhất kèm mức kiểm chứng. |
| **Bỏ qua sổ tay** | *"Không dùng sổ tay"* | Agent trả lời thuần túy, không tra cứu sổ tay. |
| **Sửa / Bổ sung** | *"Sửa mục CIV-xxxx"*, *"Bổ sung ca CIV-xxxx"* | Tạo revision mới (ví dụ revision 2) kèm lý do sửa, bảo toàn nguyên vẹn revision cũ. |
| **Hoàn tác** | *"Hoàn tác mục vừa lưu"* | Tạo revision đảo ngược (reverse revision), đánh dấu đã xóa logic nhưng lưu toàn bộ lịch sử. |

---

## 3. Cấu trúc thư mục

```text
D:\AGENT_AI\CivilHandbook\
├── data\
│   ├── cases\              # Nguồn dữ liệu gốc (CIV-0001, CIV-0002...)
│   │   └── CIV-xxxx\
│   │       ├── case.json
│   │       └── revisions\
│   │           ├── 0001.md
│   │           └── 0002.md
│   ├── index\              # Chỉ mục dẫn xuất SQLite WAL (handbook.sqlite)
│   ├── taxonomy\           # 18 nhóm đối tượng Civil và danh mục plugin
│   ├── evidence\           # Bằng chứng kiểm tra đính kèm
│   └── backup-queue\       # Hàng đợi đồng bộ Drive
├── src\
│   └── handbook\
│       ├── server.py       # FastMCP stdio server (6 tools, 2 resources)
│       ├── storage.py      # Core storage engine (atomic write, optimistic concurrency, undo)
│       ├── search.py       # Hybrid search (FTS5 BM25 + Semantic + RRF fusion)
│       ├── embedding.py    # Google Gemini Embedding adapter + Mock
│       ├── models.py       # Pydantic v2 data models
│       ├── id_allocator.py # CIV-xxxx thread-safe ID generator
│       ├── secret_scanner.py # Quét lọc token, mật khẩu, connection string
│       ├── taxonomy.py     # Quản lý 18 nhóm Civil & scan PackageContents.xml
│       └── text_utils.py   # Chuẩn hóa tiếng Việt (bỏ dấu, lowercase cho FTS5)
├── skills\
│   └── civil-handbook\     # Skill định nghĩa hành vi agent (SKILL.md)
├── scripts\
│   └── backup_sync.py      # Đồng bộ 1 chiều sang Google Drive & restore-check
└── tests\                  # Bộ 63 unit test tự động (100% pass)
```

---

## 4. Công cụ MCP đã công bố

1. `civil_handbook_search_cases`: Tìm lai theo từ khóa, đối tượng Civil, plugin, phiên bản, dự án, mức kiểm chứng (mặc định tối đa 5).
2. `civil_handbook_get_case`: Đọc chi tiết một ca (theo revision hiện tại hoặc lịch sử revision).
3. `civil_handbook_save_case`: Lưu ca mới khi người dùng nói "bảo lưu" (yêu cầu `idempotency_key` chống trùng lặp).
4. `civil_handbook_revise_case`: Tạo revision mới cho ca đã có (`expected_revision` optimistic lock).
5. `civil_handbook_undo_change`: Hoàn tác thao tác lưu/sửa bằng revision đảo ngược qua `operation_id`.
6. `civil_handbook_health`: Kiểm tra file nguồn, SQLite WAL, embedding provider, backup mà không để lộ bí mật.

**Tài nguyên chỉ đọc (Resources)**:
- `civil-handbook://taxonomy/civil-groups`: 18 nhóm đối tượng Civil 3D chuẩn.
- `civil-handbook://taxonomy/plugins`: Danh mục plugin AutoCAD/Civil 3D đã nhận diện.

---

## 5. Sao lưu dự phòng & Khôi phục (Backup & Restore)

- **Đồng bộ một chiều**: Tự động mirror từ `D:\AGENT_AI\CivilHandbook\data` sang `G:\My Drive\CodexSync\backups\CivilHandbook\current`.
- **Snapshot theo ngày**: Lưu trữ snapshot `YYYY-MM-DD` (giữ tối đa 30 snapshot gần nhất).
- **Kiểm tra khôi phục an toàn (restore-check)**:
  ```powershell
  python scripts/backup_sync.py
  python -c "from scripts.backup_sync import restore_check; import tempfile, pathlib; print(restore_check(pathlib.Path(tempfile.mkdtemp())))"
  ```
  Hàm `restore_check()` kiểm tra toàn vẹn mã SHA-256 của từng file và tái dựng chỉ mục SQLite trong thư mục tạm, đảm bảo không bao giờ ảnh hưởng tới kho chính.
