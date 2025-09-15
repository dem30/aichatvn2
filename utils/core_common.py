import re
from typing import Callable, Dict
from google.api_core.retry_async import AsyncRetry
from google.api_core.exceptions import GoogleAPICallError
from utils.logging import get_logger
from utils.exceptions import DatabaseError
import shutil
import os
import time
import aiosqlite
import traceback
from config import Config

logger = get_logger("CoreCommon")

def sanitize_field_name(field: str) -> str:
    """Chuẩn hóa tên field, chỉ giữ chữ thường, số và dấu gạch dưới."""
    sanitized = re.sub(r'[^a-z0-9_]', '_', field.lower())
    return sanitized if validate_name(sanitized) else "field"

def validate_name(name: str) -> bool:
    """Kiểm tra tên hợp lệ (chỉ chứa chữ, số, dấu gạch dưới; không rỗng)."""
    if not isinstance(name, str) or not name:
        logger.error("Tên không hợp lệ: phải là chuỗi không rỗng")
        return False
    if not re.match(r'^[a-zA-Z0-9_]+$', name):
        logger.warning(f"Tên không hợp lệ: {name}. Chỉ được chứa chữ cái, số, dấu gạch dưới")
        return False
    return True

def validate_password_strength(password: str) -> bool:
    """Kiểm tra mật khẩu (≥8 ký tự)."""
    if not isinstance(password, str) or len(password) < 8:
        logger.error(f"Mật khẩu không hợp lệ: phải dài ít nhất 8 ký tự")
        return False
    return True

async def retry_firestore_operation(operation: Callable, max_attempts: int = 3):
    """Thực hiện lại thao tác Firestore với cơ chế retry không đồng bộ."""
    retry_policy = AsyncRetry(
        initial=1.0,
        maximum=4.0,
        multiplier=2.0,
        timeout=60.0,
        predicate=lambda e: isinstance(e, GoogleAPICallError)
    )
    async def wrapped_operation():
        logger.debug(f"Thực thi Firestore operation: {operation.__name__}")
        return await operation()
    try:
        result = await retry_policy(wrapped_operation)()
        logger.debug(f"Kết quả Firestore operation {operation.__name__}: {result}")
        return result
    except GoogleAPICallError as e:
        logger.error(f"Lỗi Firestore sau {max_attempts} lần thử: {str(e)}")
        raise DatabaseError(f"Hết lượt thử sau {max_attempts} lần: {str(e)}")
    except Exception as e:
        logger.error(f"Lỗi không mong muốn trong Firestore operation: {str(e)}")
        raise DatabaseError(f"Lỗi không mong muốn trong Firestore operation: {str(e)}")

def check_disk_space() -> None:
    """Kiểm tra dung lượng đĩa, ném lỗi nếu dưới 1MB."""
    disk_usage = shutil.disk_usage(os.path.dirname(Config.SQLITE_DB_PATH))
    if disk_usage.free < 1024 * 1024:
        logger.error(f"Không đủ dung lượng đĩa tại {Config.SQLITE_DB_PATH}")
        raise DatabaseError("Không đủ dung lượng đĩa, ít hơn 1MB")

async def check_last_sync(core, username: str) -> dict:
    """Kiểm tra thời gian đồng bộ cuối cùng."""
    try:
        async with aiosqlite.connect(Config.SQLITE_DB_PATH) as conn:
            async with conn.execute(
                """
                SELECT MAX(timestamp) FROM sync_log 
                WHERE action IN ('SYNC_TO_SQLITE', 'SYNC_FROM_SQLITE') 
                AND details LIKE ?
                """,
                (f'%username": "{username}"%',)
            ) as cursor:
                last_sync = await cursor.fetchone()
                current_time = int(time.time())
                if last_sync and last_sync[0] and (current_time - last_sync[0]) < Config.SYNC_MIN_INTERVAL:
                    return {"success": False, "message": f"Chờ {Config.SYNC_MIN_INTERVAL} giây"}
                return {"success": True, "message": "Có thể đồng bộ"}
    except Exception as e:
        error_msg = f"Lỗi kiểm tra thời gian đồng bộ cho {username}: {str(e)}"
        has_admin_access = False
        try:
            has_admin_access = await core.sqlite_handler.has_permission(username, "admin_access")
        except Exception:
            logger.warning(f"Không thể kiểm tra quyền admin cho {username}")
        if has_admin_access:
            error_msg += f"\nChi tiết: {traceback.format_exc()}"
        logger.error(error_msg)
        return {"success": False, "message": error_msg}