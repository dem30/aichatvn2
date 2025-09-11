
Hướng Dẫn Phát Triển Ứng Dụng aichatvn
Hướng dẫn này cung cấp chi tiết toàn diện về cách phát triển, bảo trì và triển khai ứng dụng web aichatvn2, được xây dựng bằng FastAPI và NiceGUI. Ứng dụng hỗ trợ xác thực người dùng, giao diện chat tích hợp Grok chỉ dành cho quản trị viên, và quản lý dữ liệu câu hỏi-đáp (Q&A) với lưu trữ cục bộ bằng SQLite và đồng bộ với Google Firestore. Ứng dụng được thiết kế để chạy cục bộ hoặc triển khai trên các nền tảng như Hugging Face Spaces thông qua Docker.

Mục Lục
Tổng Quan
Thành Phần Core
Thành Phần App
Thành Phần Tab Chat
Thành Phần Tab Training
DashboardLayout
Các Thành Phần Giao Diện
ButtonComponent
ChatComponent
FormComponent
SidebarComponent
TableComponent
TrainingComponent
AuthLayout
HeaderComponent
UIManager
Tiện Ích
CoreCommon
Logging
Exceptions
Ứng Dụng Chính
Hướng Dẫn Phát Triển
Thiết Lập Môi Trường
Khởi Tạo Cơ Sở Dữ Liệu
Quản Lý Người Dùng
Thao Tác Dữ Liệu
Đồng Bộ Với Firestore
Thao Tác Chat và Training
Xử Lý Lỗi và Ghi Log
Chạy Cục Bộ
Triển Khai Với Docker
Thực Tiễn Tốt Nhất
Triển Khai Trên Hugging Face Spaces
Cập Nhật Trong Tương Lai
Tổng Quan
aichatvn2 là một ứng dụng web với các tính năng chính:
Xác Thực: Đăng nhập/đăng ký an toàn với cookie phiên.
Chat: Giao diện chat chỉ dành cho quản trị viên, tích hợp Grok, hỗ trợ các chế độ QA, Grok và Hybrid.
Quản Lý Q&A: Giao diện chỉ dành cho quản trị viên để quản lý dữ liệu câu hỏi-đáp với tìm kiếm, nhập/xuất và thao tác CRUD.
Bảng Điều Khiển: Giao diện tab với thanh bên điều hướng và điều khiển tiêu đề.
Lưu Trữ Dữ Liệu: SQLite cho lưu trữ cục bộ, đồng bộ với Firestore.
Triển Khai: Đóng gói Docker để chạy cục bộ hoặc trên các nền tảng như Hugging Face Spaces.
Các thành phần chính bao gồm:
core: Quản lý cơ sở dữ liệu và đồng bộ.
app: Ứng dụng FastAPI, xác thực và hiển thị giao diện.
uiapp/tab_chat, uiapp/tab_training: Giao diện chat và quản lý Q&A.
DashboardLayout, ButtonComponent, ChatComponent, FormComponent, SidebarComponent, TableComponent, TrainingComponent, AuthLayout, HeaderComponent, UIManager: Quản lý giao diện người dùng.
core_common, logging, exceptions: Tiện ích hỗ trợ.
main: Điểm vào ứng dụng.
Thành Phần Core
Tệp: core.py
Kiến Trúc:
Gồm ba lớp chính:
SQLiteHandler: Quản lý cơ sở dữ liệu SQLite cục bộ.
FirestoreHandler: Xử lý tương tác với Firestore.
Core: Điều phối giữa SQLiteHandler và FirestoreHandler, cung cấp giao diện thống nhất.
SQLiteHandler:
Khởi Tạo: Tạo các bảng (users, sessions, client_states, collection_schemas, sync_log, qa_data, qa_fts, chat_config, chat_messages), bật chế độ WAL và hỗ trợ FTS5 cho qa_data.
Quản Lý Người Dùng: Đăng ký (register_user), xác thực (authenticate_user), quản lý trạng thái client (get_client_state, save_client_state, clear_client_state).
Thao Tác Dữ Liệu: Tạo/xóa bảng (create_collection, drop_collection), CRUD bản ghi (create_record, update_record, delete_record, read_records), xóa theo điều kiện (delete_records_by_condition), tìm kiếm (search_collections với FTS5 hoặc LIKE).
Đồng Bộ: Sử dụng asyncio.Queue để quản lý thao tác ghi, đảm bảo an toàn luồng.
FirestoreHandler:
Khởi Tạo: Kết nối Firestore với thông tin xác thực từ Config.FIRESTORE_CREDENTIALS.
Đồng Bộ:
sync_to_sqlite: Kéo dữ liệu từ Firestore về SQLite.
sync_from_sqlite: Đẩy dữ liệu từ SQLite lên Firestore, loại trừ bảng FTS.
Quản Lý Lược Đồ: Kiểm tra và hợp nhất lược đồ giữa SQLite và Firestore (validate_schema_compatibility).
Xử Lý Lỗi: Thử lại thao tác Firestore với cơ chế backoff theo cấp số nhân.
Core:
Khởi Tạo: Gọi init_sqlite để thiết lập SQLite và đồng bộ Firestore nếu có.
Quản Lý Người Dùng: Chuyển tiếp đến SQLiteHandler.
Thao Tác Dữ Liệu: Bao bọc các phương thức của SQLiteHandler.
Đồng Bộ: Điều phối sync_to_sqlite và sync_from_sqlite.
Dọn Dẹp: Xóa phiên hết hạn (cleanup_invalid_client_states) và nhật ký đồng bộ cũ (cleanup_sync_log).
Cấu Hình Chat: Lưu cài đặt chat trong chat_config.
Thành Phần App
Tệp: app.py
Kiến Trúc:
Ứng dụng FastAPI tích hợp NiceGUI để hiển thị giao diện.
Xử lý:
Routing: Các route xác thực (/auth, /api/login, /api/register, /api/logout), bảng điều khiển (/dashboard), và đồng bộ (/api/sync).
Xác Thực: Đăng nhập/đăng ký người dùng với quản lý phiên qua cookie.
Quản Lý Phiên: Xác thực và duy trì phiên bằng session_token và username.
Bảng Điều Khiển: Hiển thị giao diện tab động với DashboardLayout và UIManager.
Middleware: Áp dụng xác thực cho các route được bảo vệ, kiểm tra Firestore.
Thiết Lập FastAPI:
Middleware CORS: Cho phép yêu cầu cross-origin (allow_origins=["*"]).
Sự Kiện Khởi Động: Khởi tạo SQLite, dọn dẹp trạng thái client không hợp lệ, thiết lập app.storage.user["clients"].
Các Route:
/auth: Trang đăng nhập/đăng ký, sử dụng UIManager.render_auth.
/: Chuyển hướng đến /dashboard nếu đã xác thực, ngược lại đến /auth.
/dashboard: Hiển thị bảng điều khiển với các tab động.
/api/login: Xử lý đăng nhập.
/api/register: Xử lý đăng ký.
/api/logout: Xóa phiên và cookie.
/api/sync: Kích hoạt đồng bộ Firestore (chỉ quản trị viên).
Xác Thực:
Đăng Nhập (/api/login):
Đầu Vào: LoginData (username, password, bot_password tùy chọn).
Quy Trình:
Kiểm tra đầu vào (validate_user_input: username ≥3 ký tự, chữ/số; password ≥8 ký tự).
Xóa phiên cũ nếu có.
Kiểm tra số lần thử đăng nhập (Config.MAX_LOGIN_ATTEMPTS).
Gọi Core.authenticate_user để xác minh.
Cập nhật và lưu trạng thái client (update_and_save_client_state).
Đặt cookie session_token và username.
Phản Hồi: JSON với thông báo thành công và chuyển hướng đến /dashboard, hoặc lỗi (ví dụ: thông tin không hợp lệ).
Đăng Ký (/api/register):
Đầu Vào: RegisterData (username, password, confirm_password, bot_password tùy chọn).
Quy Trình:
Kiểm tra đầu vào, đảm bảo mật khẩu khớp.
Gọi Core.register_user để tạo người dùng và phiên.
Ghi đăng ký vào sync_log.
Đặt cookie và cập nhật trạng thái client.
Phản Hồi: JSON với thông báo thành công hoặc lỗi (ví dụ: username đã tồn tại).
Đăng Xuất (/api/logout):
Xóa trạng thái client và cookie.
Chuyển hướng đến /auth.
Quản Lý Phiên:
Cookie: Lưu session_token và username với thời hạn Config.SESSION_MAX_AGE (ví dụ: 86400 giây).
Xác Thực (handle_session):
Kiểm tra session_token (≥32 ký tự, chữ/số).
Kiểm tra username khớp trong bảng sessions.
Đảm bảo phiên chưa hết hạn (trong SESSION_MAX_AGE).
Kiểm tra kích thước trạng thái client < 1MB.
Làm Mới: Cập nhật dấu thời gian nếu phiên gần hết hạn (80% SESSION_MAX_AGE).
Dọn Dẹp: Xóa phiên không hợp lệ hoặc hết hạn qua Core.clear_client_state.
Hiển Thị Bảng Điều Khiển:
/dashboard:
Hiển thị bằng DashboardLayout, với các tab từ UIManager.registered_tabs.
Tải tab động từ uiapp/tab_*.py qua load_tabs.
Hỗ trợ chọn tab với trạng thái lưu trữ (client_state["selected_tab"]).
Bao gồm chức năng đăng xuất và kiểm tra quyền quản trị viên.
Tải Tab (load_tabs):
Quét uiapp/ để tìm các tệp tab_*.py (ví dụ: tab_chat.py, tab_training.py).
Tải mô-đun bằng importlib.
Đăng ký tab với UIManager nếu cung cấp create_tab hợp lệ (trả về render_func, update_func).
Hạn chế tab nhạy cảm (ví dụ: tab_chat) cho quản trị viên.
Gán biểu tượng dựa trên tên tab (ví dụ: "Chat" → "chat").
Middleware:
Xác Thực:
Bỏ qua các đường dẫn công khai (/auth, /api/login, v.v.).
Xác thực phiên qua handle_session.
Kiểm tra tình trạng Firestore, đặt request.state.firestore_warning nếu không khả dụng.
Chuyển hướng đến /auth với lỗi nếu phiên không hợp lệ.
Thành Phần Tab Chat
Tệp: uiapp/tab_chat.py
Mục Đích: Cung cấp giao diện chat chỉ dành cho quản trị viên với tích hợp Grok, hỗ trợ các chế độ QA, Grok, và Hybrid.
Chi Tiết: (Như mô tả trước đây, sử dụng ChatComponent để hiển thị tin nhắn, gửi tin nhắn qua handle_send(), đặt lại lịch sử qua reset(), và thay đổi mô hình/chế độ qua dropdown nếu được bật trong Config.)
Thành Phần Tab Training
Tệp: uiapp/tab_training.py
Mục Đích: Cung cấp giao diện quản lý Q&A chỉ dành cho quản trị viên, mở rộng bởi TrainingComponent.
Chi Tiết: (Như mô tả trước đây, hỗ trợ thêm/sửa/xóa Q&A, tìm kiếm bằng FTS5, nhập/xuất JSON/CSV, và xóa hàng loạt với xác nhận.)
DashboardLayout
Tệp: uiapp/layouts/dashboard.py
Mục Đích: Hiển thị giao diện bảng điều khiển với các tab động, sử dụng HeaderComponent và SidebarComponent.
Chi Tiết: (Như mô tả trước đây, cung cấp bố cục tab với thanh bên điều hướng và tiêu đề, tích hợp với UIManager.)
Các Thành Phần Giao Diện
ButtonComponent
Tệp: uiapp/components/button.py
Mục Đích: Cung cấp nút tái sử dụng với trạng thái tải và xử lý lỗi dựa trên quyền.
ChatComponent
Tệp: uiapp/components/chat.py
Mục Đích: Triển khai giao diện chat với tích hợp Grok.
FormComponent
Tệp: uiapp/components/form.py
Mục Đích: Cung cấp biểu mẫu tái sử dụng cho nhập/sửa dữ liệu Q&A.
SidebarComponent
Tệp: uiapp/components/sidebar.py
Mục Đích: Hiển thị thanh bên với các nút điều hướng tab.
TableComponent
Tệp: uiapp/components/table.py
Mục Đích: Hiển thị dữ liệu Q&A trong bảng động với phân trang, sắp xếp, lọc và hành động trên dòng.
TrainingComponent
Tệp: uiapp/components/training.py
Mục Đích: Quản lý dữ liệu Q&A với biểu mẫu, tìm kiếm, nhập/xuất và xóa.
AuthLayout
Tệp: uiapp/layouts/auth.py
Mục Đích: Hiển thị biểu mẫu đăng nhập/đăng ký bằng tab và FormComponent.
Thuộc Tính:
on_login, on_register: Hàm gọi lại cho đăng nhập/đăng ký.
core: Đối tượng Core cho thao tác cơ sở dữ liệu.
fields: Định nghĩa trường biểu mẫu (ví dụ: username, password, bot_password).
Phương Thức:
render(): Hiển thị các tab đăng nhập/đăng ký, xử lý tình trạng Firestore và lỗi.
handle_login(data): Xử lý đăng nhập với kiểm tra kích thước dữ liệu và timeout.
handle_register(data): Xử lý đăng ký, ghi vào sync_log cho đồng bộ Firestore.
HeaderComponent
Tệp: uiapp/components/header.py
Mục Đích: Hiển thị tiêu đề với lời chào, điều khiển đồng bộ (cho quản trị viên), và nút đăng xuất.
Thuộc Tính:
username, is_admin: Thông tin người dùng và trạng thái quản trị viên.
on_logout, on_sync_to_sqlite, on_sync_from_sqlite: Hàm gọi lại.
core, client_state, ui_manager: Cho cơ sở dữ liệu, trạng thái và quản lý tab.
logo, extra_buttons: Logo tùy chọn và nút tùy chỉnh.
protected_only, selected_collections: Cài đặt đồng bộ.
Phương Thức:
render(): Hiển thị tiêu đề với lời chào, điều khiển đồng bộ và nút.
get_available_tables(): Lấy danh sách bảng SQLite.
get_sync_tables(): Lọc bảng để đồng bộ.
update_tabs_after_sync(): Cập nhật tab Chat/Training sau đồng bộ.
check_last_sync(): Kiểm tra khoảng thời gian đồng bộ.
handle_sync_to_sqlite(), handle_sync_from_sqlite(): Quản lý đồng bộ với hộp thoại tiến trình.
handle_sync(): Xử lý đồng bộ chung.
UIManager
Tệp: uiapp/ui_manager.py
Mục Đích: Điều phối hiển thị giao diện, quản lý xác thực và tab bảng điều khiển.
Chi Tiết: (Như mô tả trước đây, đăng ký và hiển thị tab động, tích hợp với DashboardLayout.)
Tiện Ích
CoreCommon
Tệp: utils/core_common.py
Mục Đích: Cung cấp các hàm tiện ích:
sanitize_field_name(field): Chuẩn hóa tên trường (chữ thường, số, dấu gạch dưới).
validate_name(name): Kiểm tra tên hợp lệ (chữ, số, dấu gạch dưới, không rỗng).
validate_password_strength(password): Đảm bảo mật khẩu ≥8 ký tự.
retry_firestore_operation(operation): Thử lại thao tác Firestore (tối đa 3 lần).
check_disk_space(): Đảm bảo dung lượng đĩa ≥1MB.
check_last_sync(core, username): Kiểm tra khoảng thời gian đồng bộ.
Logging
Tệp: utils/logging.py
Mục Đích: Cấu hình ghi log chỉ vào console, tối ưu cho Hugging Face Spaces.
setup_logging(): Thiết lập log mức INFO, chỉ xuất console.
get_logger(name): Trả về logger với tên chỉ định.
disable_verbose_logs(): Tắt log chi tiết từ aiosqlite và nicegui.
Exceptions
Tệp: utils/exceptions.py
Mục Đích: Định nghĩa ngoại lệ tùy chỉnh.
Nội Dung:
class DatabaseError(Exception):
    """Ném ra cho các lỗi liên quan đến cơ sở dữ liệu (ví dụ: thiếu dung lượng đĩa, lỗi SQLite/Firestore)."""
    pass
Ứng Dụng Chính
Tệp: main.py
Mục Đích: Điểm vào ứng dụng, khởi tạo FastAPI, logging và cơ sở dữ liệu.
Chi Tiết:
Thiết lập app.storage.STORAGE_PATH thành /tmp/nicegui.
Lifespan: Khởi tạo logging, kiểm tra dung lượng đĩa, khởi tạo SQLite, thực hiện đồng bộ Firestore ban đầu (nếu có).
Chạy ứng dụng với ui.run_with, đặt tiêu đề, khóa bí mật và thời gian chờ kết nối lại.
Hướng Dẫn Phát Triển
Thiết Lập Môi Trường
Cài Đặt Phụ Thuộc: Tạo requirements.txt:
fastapi==0.115.0
nicegui==2.0.0
firebase-admin==6.5.0
aiosqlite==0.20.0
passlib[bcrypt]==1.7.4
python-dotenv==1.0.1
jsonschema==4.23.0
groq==0.9.0
tenacity==9.0.0
bcrypt==4.0.1
fuzzywuzzy==0.18.0
python-Levenshtein==0.25.1
httpx==0.27.0
google-cloud-firestore>=2.14.0
google-api-core>=2.15.0
uvicorn==0.30.6
Cài đặt:
pip install -r requirements.txt
Cấu Hình Môi Trường: Tạo config.py:
class Config:
    APP_NAME = "aichatvn2"
    SQLITE_DB_PATH = "/tmp/app.db"
    SECRET_KEY = "your-secret-key"
    ADMIN_USERNAME = "admin"
    ADMIN_PASSWORD = "securepassword"
    SESSION_MAX_AGE = 86400
    SECURE_COOKIES = True
    SYNC_MIN_INTERVAL = 300
    MAX_LOGIN_ATTEMPTS = 5
    DEFAULT_TAB = "Chat"
    DEFAULT_MODEL = "llama3-8b-8192"
    DEFAULT_CHAT_MODE = "Hybrid"
    CHAT_HISTORY_LIMIT = 50
    QA_HISTORY_LIMIT = 50
    QA_SEARCH_THRESHOLD = 0.8
    TRAINING_SEARCH_THRESHOLD = 0.7
    GROQ_API_KEY = "your-groq-api-key"
    FIRESTORE_CREDENTIALS = {"project_id": "your-project-id", ...}
    PROTECTED_TABLES = {"users", "sessions", "client_states", "collection_schemas"}
    SYSTEM_TABLES = {"sync_log", "qa_fts"}
    SPECIAL_TABLES = {"qa_data", "chat_messages"}
    SHOW_MODEL_COMBOBOX = True
    SHOW_MODE_COMBOBOX = True
    AVAILABLE_MODELS = ["llama3-8b-8192", "mixtral-8x7b-32768"]
    MAX_UPLOAD_SIZE = 1048576
Cấu Trúc Thư Mục:
project/
├── main.py
├── app.py
├── core.py
├── config.py
├── requirements.txt
├── Dockerfile
├── app.yaml
├── uiapp/
│   ├── __init__.py
│   ├── ui_manager.py
│   ├── tab_chat.py
│   ├── tab_training.py
│   ├── layouts/
│   │   ├── __init__.py
│   │   ├── auth.py
│   │   ├── dashboard.py
│   ├── components/
│   │   ├── __init__.py
│   │   ├── button.py
│   │   ├── chat.py
│   │   ├── form.py
│   │   ├── sidebar.py
│   │   ├── table.py
│   │   ├── training.py
│   │   ├── header.py
├── utils/
│   ├── __init__.py
│   ├── logging.py
│   ├── core_common.py
│   ├── exceptions.py
└── tmp/
    ├── app.db
    ├── nicegui/
Triển Khai Thành Phần Thiếu:
Exceptions (utils/exceptions.py):
class DatabaseError(Exception):
    """Ném ra cho các lỗi liên quan đến cơ sở dữ liệu (ví dụ: thiếu dung lượng đĩa, lỗi SQLite/Firestore)."""
    pass
Tải Tab Động: Trong uiapp/ui_manager.py, đảm bảo load_tabs:
async def load_tabs(ui_manager: UIManager, core: Core, username: str, client_state: Dict):
    from uiapp.tab_chat import TabChat
    from uiapp.tab_training import TabTraining
    tab_chat = TabChat(core=core, client_state=client_state)
    tab_training = TabTraining(core=core, client_state=client_state)
    ui_manager.register_tab("Chat", tab_chat.render, tab_chat.update, icon="chat")
    if await core.sqlite_handler.has_permission(username, "admin_access"):
        ui_manager.register_tab("Training", tab_training.render, tab_training.update, icon="school")
Khởi Tạo Cơ Sở Dữ Liệu
Gọi Core.init_sqlite() trong main.lifespan để tạo các bảng: users, sessions, client_states, chat_messages, chat_config, qa_data, qa_fts, sync_log.
Bật chế độ WAL của SQLite trong TrainingComponent.enable_wal_mode().
Khởi tạo dữ liệu quản trị viên mặc định và đồng bộ Firestore nếu có.
Quản Lý Người Dùng
Đăng Nhập (POST /api/login):
curl -X POST "http://localhost:8000/api/login" -d '{"username": "user", "password": "password123"}'
Đặt cookie và chuyển hướng đến /dashboard.
Đăng Ký (POST /api/register):
curl -X POST "http://localhost:8000/api/register" -d '{"username": "user", "password": "password123", "confirm_password": "password123"}'
Đăng Xuất (POST /api/logout):
curl -X POST "http://localhost:8000/api/logout" -H "Cookie: session_token=...; username=user"
Quản Lý Phiên: Sử dụng session_token trong client_state, lưu trong client_states.
Thao Tác Dữ Liệu
Tin Nhắn Chat: Lưu trong chat_messages với id, session_token, username, content, role, type, timestamp.
Dữ Liệu Q&A: Lưu trong qa_data với id, question, answer, category, created_by, created_at, timestamp.
Tìm Kiếm: Sử dụng FTS5 (qa_fts) cho tìm kiếm toàn văn, dự phòng bằng SQL LIKE hoặc so khớp mờ (rapidfuzz).
Nhật Ký Đồng Bộ: Ghi hành động (CREATE, UPDATE, DELETE, EXPORT, SYNC) trong sync_log.
Đồng Bộ Với Firestore
Chỉ Quản Trị Viên: POST /api/sync kích hoạt Core.sync_to_sqlite hoặc Core.sync_from_sqlite qua HeaderComponent.
Kiểm tra SYNC_MIN_INTERVAL bằng check_last_sync.
Hỗ trợ đồng bộ bảng chọn lọc (protected_only, selected_collections).
Ghi hành động vào sync_log.
Thao Tác Chat và Training
Chat:
Gửi tin nhắn qua ChatComponent.handle_send() trong chế độ QA, Grok, hoặc Hybrid.
Đặt lại lịch sử qua ChatComponent.reset().
Thay đổi mô hình/chế độ qua dropdown (nếu bật trong Config).
Training:
Thêm/sửa Q&A bằng FormComponent.
Tìm kiếm Q&A với TrainingComponent.handle_search() sử dụng FTS5.
Nhập Q&A qua JSON/CSV với handle_json_submit() và handle_file_upload().
Xuất Q&A sang JSON với handle_export_qa().
Xóa bản ghi Q&A bằng delete_row() hoặc on_reset() (xóa hàng loạt với xác nhận).
Xử Lý Lỗi và Ghi Log
Xử Lý Lỗi:
Sử dụng tenacity để thử lại (safe_ui_update, retry_firestore_operation).
Thông báo qua ui.notify (stack trace chi tiết cho quản trị viên).
Xử lý timeout (30 giây cho bảng điều khiển/xác thực, 60 giây cho lấy dữ liệu, 300 giây cho đồng bộ).
Kiểm tra kích thước dữ liệu (MAX_UPLOAD_SIZE) và trùng lặp trong TrainingComponent.
Ném DatabaseError cho các vấn đề nghiêm trọng (ví dụ: thiếu dung lượng đĩa).
Ghi Log:
Sử dụng get_logger cho các mức debug, info, warning, error.
Chỉ ghi log vào console để tương thích với Hugging Face Spaces.
Ghi hành động vào sync_log (thay đổi tab, Q&A CRUD, đồng bộ).
Chạy Cục Bộ
python -m nicegui main.py
Truy cập tại http://localhost:8000.
Triển Khai Với Docker
Xây dựng hình ảnh Docker:
docker build -t aichatvn2 .
Chạy container:
docker run -p 7860:7860 -e LOG_LEVEL=INFO -e GROQ_API_KEY=your-groq-api-key -e FIRESTORE_CREDENTIALS='{"project_id": "your-project-id", ...}' -e SECRET_KEY=your-secret-key aichatvn2
Truy cập tại http://localhost:7860.
Thực Tiễn Tốt Nhất
Bảo Mật:
Hạn chế tab_chat và tab_training chỉ cho quản trị viên qua Core.has_permission.
Kiểm tra đầu vào (validate_name, validate_password_strength, jsonschema, sanitize_field_name).
Giới hạn kích thước dữ liệu (MAX_UPLOAD_SIZE) và kiểm tra trùng lặp.
Sử dụng HTTPS với Config.SECURE_COOKIES.
Chạy với người dùng không phải root (appuser) trong Docker.
Hiệu Suất:
Sử dụng asyncio.Lock và Semaphore để đảm bảo an toàn luồng.
Giới hạn truy xuất dữ liệu (CHAT_HISTORY_LIMIT, QA_HISTORY_LIMIT).
Lưu trữ bộ nhớ đệm thành phần trong app.storage.client.
Sử dụng FTS5 để tìm kiếm hiệu quả, dự phòng bằng SQL LIKE hoặc so khớp mờ.
Tắt log chi tiết cho aiosqlite và nicegui.
Tính Mô-đun:
Giữ các thành phần (ButtonComponent, FormComponent, TableComponent) tái sử dụng.
Tập trung quản lý tab trong UIManager.
Sử dụng DashboardLayout cho cấu trúc giao diện nhất quán.
Khôi Phục Lỗi:
Xử lý timeout, lỗi JSON, và vấn đề cơ sở dữ liệu một cách nhẹ nhàng.
Dọn dẹp bộ nhớ client khi ngắt kết nối (app.on_disconnect).
Kiểm tra JSON/CSV nhập vào với process_qa_list.
Thử lại thao tác Firestore với retry_firestore_operation.
Giao Diện/Người Dùng:
Sử dụng CSS đáp ứng (lớp Tailwind trong các thành phần).
Cung cấp phản hồi tiến trình (ui.linear_progress) cho thao tác dài.
Xác nhận hành động phá hủy (hộp thoại on_reset).
Triển Khai Trên Hugging Face Spaces
Chuẩn Bị Repository:
Đảm bảo tất cả tệp nằm trong repository theo cấu trúc thư mục.
Bao gồm app.yaml:
title: aichatvn
emoji: 👁
colorFrom: red
colorTo: gray
sdk: docker
pinned: false
Cài Đặt Biến Môi Trường:
Trong cài đặt Hugging Face Space:
LOG_LEVEL=INFO
GROQ_API_KEY=your-groq-api-key
FIRESTORE_CREDENTIALS={"project_id": "your-project-id", ...}
SECRET_KEY=your-secret-key
 
