import asyncio
import os
import logging  # Import để fallback temp logger
from fastapi import FastAPI
from nicegui import ui, app
from app import fastapi_app, core
from config import Config
from contextlib import asynccontextmanager
from pathlib import Path
from utils.logging import get_logger, setup_logging, disable_verbose_logs
from utils.core_common import check_disk_space

# Đặt STORAGE_PATH ngay khi import (giữ nguyên)
try:
    storage_path = "/tmp/nicegui"
    os.makedirs(storage_path, exist_ok=True)
    if not os.access(storage_path, os.W_OK):
        raise PermissionError(f"Không có quyền ghi vào {storage_path}")
    app.storage.STORAGE_PATH = storage_path
except Exception as e:
    # Fallback print nếu logger chưa sẵn sàng
    print(f"Lỗi khi thiết lập STORAGE_PATH: {str(e)}")
    raise

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger = None  # Khởi tạo sau setup_logging
    try:
        # Setup logging đầu tiên (INFO mặc định, chỉ stdout)
        setup_logging()
        disable_verbose_logs()
        logger = get_logger("Main")
        logger.info("Bắt đầu lifespan: Setup logging hoàn tất")

        # Tắt HTTP access logs từ uvicorn/FastAPI (giảm log static files)
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        logging.getLogger("uvicorn").setLevel(logging.WARNING)
        logging.getLogger("fastapi").setLevel(logging.WARNING)
        logger.info("Đã tắt HTTP access logs từ uvicorn/FastAPI")

        # Kiểm tra cấu hình
        if not hasattr(Config, "SQLITE_DB_PATH") or not Config.SQLITE_DB_PATH:
            logger.error("Thiếu cấu hình SQLITE_DB_PATH")
            raise ValueError("Thiếu cấu hình SQLITE_DB_PATH")

        logger.info("Kiểm tra không gian đĩa trước khi khởi tạo SQLite")
        check_disk_space()

        logger.info("Bắt đầu khởi tạo ứng dụng")
        await core.init_sqlite()  # Cleanup sync_log sẽ chạy ở đây (sau CREATE TABLE)
        if core.firestore_available:
            logger.info("Firestore khả dụng, kiểm tra quyền admin")
            if await core.sqlite_handler.has_permission("admin", "sync_data"):
                logger.info("Thực hiện đồng bộ ban đầu")
                result = await core.sync_to_sqlite("admin", protected_only=True)
                if "success" in result:
                    logger.info("Đồng bộ ban đầu thành công")
                else:
                    logger.warning("Đồng bộ ban đầu thất bại: %s", result.get("error", "Unknown error"))
            else:
                logger.warning("Tài khoản admin không có quyền đồng bộ, bỏ qua")
        else:
            logger.warning("Firestore không khả dụng, chạy với SQLite cục bộ")

        logger.info("Khởi tạo ứng dụng thành công")
        yield
    except Exception as e:
        if logger:
            logger.error(f"Lỗi trong lifespan: {str(e)}", exc_info=True)
        else:
            print(f"Lỗi lifespan (logger chưa sẵn sàng): {str(e)}")  # Fallback print
        raise
    finally:
        if logger:
            logger.info("Kết thúc lifespan")

fastapi_app.router.lifespan_context = lifespan

# Kiểm tra dung lượng /tmp (sau lifespan, dùng logger đã sẵn sàng)
try:
    check_disk_space()
except ValueError as e:
    logger = get_logger("Main")
    logger.warning(f"Dung lượng /tmp thấp: {str(e)}")
    ui.notify(f"Dung lượng /tmp thấp: {str(e)}", type="warning")

# Chạy app (bỏ timer, vì cleanup trong init_sqlite)
ui.run_with(
    fastapi_app,
    title=Config.APP_NAME,
    storage_secret=Config.SECRET_KEY,
    reconnect_timeout=30
)
