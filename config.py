import os

class Config:
    """Cấu hình ứng dụng, cung cấp các tham số cho Core và các thành phần khác."""

    # Thông tin ứng dụng
    APP_NAME = "SuperApp"
    APP_VERSION = "1.0.0"
    PORT = int(os.environ.get("PORT", 7860))

    # Cấu hình Firestore
    FIRESTORE_CREDENTIALS = os.environ.get("FIRESTORE_CREDENTIALS", "/path/to/your-firestore-credentials.json")

    # Cấu hình lưu trữ cục bộ
    STORAGE_PATH = "/tmp/chat_files/"

    # Cấu hình SQLite
    SQLITE_DB_PATH = os.environ.get("SQLITE_DB_PATH", "/tmp/app.db")
    MAX_SQLITE_SIZE = 100_000_000

    # Thêm cấu hình system prompt
    
    
    SYSTEM_PROMPTS = {
        "vi": (
            "Bạn là Groq. Trả lời chính xác, ngắn gọn, hữu ích bằng tiếng Việt. "
            
            "Kết hợp thông tin từ lịch sử chat để bổ sung ngữ cảnh, ưu tiên các câu trả lời trước có chứa chi tiết cụ thể "
            "(như số tài khoản, ngày tháng, tên riêng). "
            "Nếu không có QA Context hoặc thông tin liên quan trong lịch sử chat, yêu cầu người dùng cung cấp thông tin cụ thể hơn "
            "(như tên ngân hàng, số tài khoản) hoặc trả lời dựa trên kiến thức chung nếu câu hỏi cho phép. "
            "Luôn ưu tiên QA Context, sau đó là lịch sử chat, trước khi sử dụng kiến thức bên ngoài."
        ),
        "en": (
            "You are Groq. Provide accurate, concise, and helpful responses in English. "
            
            "Combine information from chat history to supplement context, prioritizing previous responses with specific details "
            "(e.g., account numbers, dates, names). "
            "If no QA Context or relevant information is found in chat history, request the user to provide more specific details "
            "(e.g., bank name, account number) or respond based on general knowledge if the question allows. "
            "Always prioritize QA Context, followed by chat history, before using external knowledge."
        ),
    }
    
    # Cấu hình cho combobox và mặc định
    SHOW_MODEL_COMBOBOX = False
    DEFAULT_MODEL = "llama-3.3-70b-versatile"
    SHOW_MODE_COMBOBOX = True
    DEFAULT_CHAT_MODE = "Grok"
    AVAILABLE_MODELS = ["llama-3.3-70b-versatile", "llama-3.1-70b-versatile"]
    
    # Cấu hình phiên và xác thực
    SESSION_MAX_AGE = 2_592_000
    MAX_LOGIN_ATTEMPTS = 5
    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
    ADMIN_BOT_PASSWORD = os.environ.get("ADMIN_BOT_PASSWORD", "Bot@Admin1234")
    CONFIG_PASSWORD = os.environ.get("CONFIG_PASSWORD", "superapp2025")

    # Cấu hình đồng bộ Firestore
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
    CHAT_HISTORY_LIMIT = 500

    # Danh sách bảng
    SPECIAL_TABLES = {"collection_schemas", "users", "sessions", "client_states"}
    PROTECTED_TABLES = {"protected_placeholder"}
    SYSTEM_TABLES = {"sync_log", "sqlite_sequence"}

    # Cấu hình AI (Grok)
    GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
    GROK_VISION_ENABLED = False
    
    # Cấu hình bảo mật
    ALLOWED_FILE_EXTENSIONS = {".txt", ".pdf", ".jpg", ".png"}
    MAX_UPLOAD_SIZE = 5_000_000
    SECRET_KEY = os.environ.get("SECRET_KEY", "superapp-secret-key-2025")

    # Cấu hình avatar
    AVATAR_MAX_SIZE = 5_000_000
    AVATAR_ALLOWED_FORMATS = ["image/jpeg", "image/png"]
    AVATAR_STORAGE_PATH = "/tmp/avatars/"
    AVATAR_FILE_EXTENSIONS = [".jpg", ".png"]  # Thêm để dùng trong ui.upload accept

    # Cấu hình file chat
    CHAT_FILE_MAX_SIZE = 10_000_000
    CHAT_FILE_ALLOWED_FORMATS = ["image/jpeg", "image/png", "application/pdf", "text/plain"]
    CHAT_FILE_EXTENSIONS = [".jpg", ".png", ".pdf", ".txt"]  # Thêm để dùng trong ui.upload accept
    CHAT_FILE_STORAGE_PATH = STORAGE_PATH