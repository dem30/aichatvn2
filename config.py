import os

class Config:
    """Cấu hình ứng dụng, cung cấp các tham số cho Core và các thành phần khác."""

    # Thông tin ứng dụng
    APP_NAME = "SuperApp"
    
    APP_VERSION = "1.0.0"
    PORT = int(os.environ.get("PORT", 7860))

    # Cấu hình SQLite
    SQLITE_DB_PATH = os.environ.get("SQLITE_DB_PATH", "/tmp/app.db")
    MAX_SQLITE_SIZE = 100_000_000

    # Cấu hình cho compobox và mặc định
    SHOW_MODEL_COMBOBOX = False  # Hiển thị compobox chọn mô hình
    DEFAULT_MODEL = "llama-3.3-70b-versatile"  # Mô hình mặc định
    SHOW_MODE_COMBOBOX = True   # Hiển thị compobox chọn chế độ chat
    DEFAULT_CHAT_MODE = "Grok"  # Chế độ chat mặc định
    AVAILABLE_MODELS = ["llama-3.3-70b-versatile", "llama-3.1-70b-versatile"]
    
    # Cấu hình phiên và xác thực
    SESSION_MAX_AGE = 2_592_000
    MAX_LOGIN_ATTEMPTS = 5
    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
    ADMIN_BOT_PASSWORD = os.environ.get("ADMIN_BOT_PASSWORD", "Bot@Admin1234")
    CONFIG_PASSWORD = os.environ.get("CONFIG_PASSWORD", "superapp2025")

    # Cấu hình đồng bộ Firestore
    FIRESTORE_CREDENTIALS = os.environ.get("FIRESTORE_CREDENTIALS", "{}")
    SYNC_INTERVAL = 60
    BATCH_SIZE = 500
    SYNC_LOG_MAX_AGE = 604_800
    SYNC_MIN_INTERVAL = 30

    # Cấu hình log và file tạm
    MAX_LOG_SIZE = 10_000_000
    MAX_TMP_AGE_DAYS = 7
    SECURE_COOKIES = True

    # Cấu hình dữ liệu và schema
    MAX_COLUMNS = 50
    MAX_PAGE_SIZE = 1000
    QA_SEARCH_THRESHOLD = 0.8
    TRAINING_SEARCH_THRESHOLD = 0.8
    QA_HISTORY_LIMIT = 10

    # Danh sách bảng
    SPECIAL_TABLES = {"collection_schemas", "users", "sessions", "client_states"} # Bảng luôn đồng bộ toàn bộ
    PROTECTED_TABLES = {"protected_placeholder"}  # Bảng chỉ đồng bộ khi protected_only=True
    SYSTEM_TABLES = {"sync_log", "sqlite_sequence"}  # Bảng hệ thống không đồng bộ
    
    # Cấu hình AI (Grok)
    GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

    # Cấu hình bảo mật
    ALLOWED_FILE_EXTENSIONS = {".txt", ".pdf", ".jpg", ".png"}
    MAX_UPLOAD_SIZE = 5_000_000
    SECRET_KEY = os.environ.get("SECRET_KEY", "superapp-secret-key-2025")
