import logging
import os

def setup_logging():
    """Setup logging với mức INFO mặc định, chỉ stdout (an toàn cho Hugging Face Spaces)."""
    root_logger = logging.getLogger()  # root logger
    if root_logger.handlers:
        return  # Tránh duplicate handlers

    # Mức log từ env (mặc định INFO cho production)
    log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    root_logger.setLevel(log_level)

    # Formatter đơn giản
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # Chỉ console handler (stdout - an toàn, hiển thị trên Hugging Face dashboard)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Optional: File handler với rotation ở /tmp (comment out để bỏ, tránh rủi ro đầy disk)
    # log_file = "/tmp/app.log"
    # try:
    #     test_fd = os.open(log_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    #     os.close(test_fd)
    #     file_handler = RotatingFileHandler(
    #         log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    #     )
    #     file_handler.setLevel(log_level)
    #     file_handler.setFormatter(formatter)
    #     root_logger.addHandler(file_handler)
    #     root_logger.info(f"Logging setup hoàn tất, mức: {log_level_str}, file: {log_file} (với rotation)")
    # except (OSError, PermissionError) as e:
    #     root_logger.warning(f"Không thể tạo file log {log_file}: {str(e)}. Chỉ dùng console.")
    
    root_logger.info(f"Logging setup hoàn tất, mức: {log_level_str} (chỉ stdout - an toàn cho Spaces)")

def get_logger(name: str) -> logging.Logger:
    """Lấy logger với tên được chỉ định, kế thừa mức từ root."""
    logger = logging.getLogger(name)
    # Áp dụng mức từ env nếu chưa set
    if not logger.level:
        log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
        logger.setLevel(getattr(logging, log_level_str, logging.INFO))
    return logger

def disable_verbose_logs():
    """Tắt log verbose từ aiosqlite và nicegui để giảm dung lượng."""
    # Tắt aiosqlite DEBUG (loại bỏ execute/commit/close logs)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    
    # Tắt nicegui static access logs (fonts/CSS/JS)
    logging.getLogger("nicegui").setLevel(logging.WARNING)
    
    # Log xác nhận (sẽ in nếu mức >= INFO)
    logging.getLogger().info("Đã tắt log verbose từ aiosqlite và nicegui")
