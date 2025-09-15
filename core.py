import asyncio
from jsonschema import validate, ValidationError as JSONSchemaValidationError
from rapidfuzz import process, fuzz
import aiosqlite
from groq import AsyncGroq
from Levenshtein import ratio
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import httpx
import aiohttp
import time
import uuid
import hashlib
import json
import os
from passlib.context import CryptContext
from config import Config
from utils.logging import get_logger
from utils.core_common import (
    sanitize_field_name,
    validate_name,
    validate_password_strength,
    check_disk_space,
    retry_firestore_operation
)
from utils.core_common import validate_name
from google.cloud.firestore_v1 import FieldFilter  # Thêm import
from typing import Dict, Optional, Any, Callable, List
from fastapi.responses import JSONResponse, RedirectResponse
try:
    from google.cloud.firestore_v1 import AsyncClient
    from google.oauth2.service_account import Credentials
    from google.api_core.retry_async import AsyncRetry
except ImportError:
    AsyncClient = None
    Credentials = None
    AsyncRetry = None
import datetime



class DatabaseError(Exception):
    """Lỗi cơ sở dữ liệu tùy chỉnh."""
    pass

# Sửa đổi: Xóa sqlite_lock toàn cục, sẽ sử dụng self.sqlite_lock trong Core
    
class SQLiteHandler:
    """Xử lý các thao tác SQLite."""
    
    def __init__(self, logger, core):
        self.logger = logger
        self.core = core
        self.pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
        self.protected_collections = Config.PROTECTED_TABLES | Config.SPECIAL_TABLES | Config.SYSTEM_TABLES
        self.write_queue = asyncio.Queue()
        self.running = False
        
    
    async def init_sqlite(self, max_attempts: int = 5, retry_delay: float = 1.0):
        """Khởi tạo SQLite với các bảng bảo vệ và dữ liệu mặc định."""
        try:
            async with asyncio.timeout(300):  # Timeout 5 phút
                for attempt in range(max_attempts):
                    try:
                        check_disk_space()
                        if os.path.exists(Config.SQLITE_DB_PATH):
                            self.logger.info(
                                f"Kiểm tra cơ sở dữ liệu SQLite tại {Config.SQLITE_DB_PATH}"
                            )
                            if await self._is_database_locked():
                                self.logger.warning(
                                    f"Cơ sở dữ liệu bị khóa, thử lại {attempt+1}/{max_attempts}"
                                )
                                if attempt < max_attempts - 1:
                                    await asyncio.sleep(retry_delay * (2 ** attempt))
                                    continue
                                raise DatabaseError("Cơ sở dữ liệu vẫn bị khóa sau khi thử lại")

                        async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                            await conn.execute("PRAGMA journal_mode=WAL")
                            await conn.execute("PRAGMA busy_timeout=5000")

                            # Tạo bảng users với trường avatar
                            await conn.execute("""
                                CREATE TABLE IF NOT EXISTS users (
                                    id TEXT PRIMARY KEY,
                                    username TEXT UNIQUE NOT NULL,
                                    password TEXT NOT NULL,
                                    bot_password TEXT,
                                    role TEXT NOT NULL,
                                    avatar TEXT,  -- Thêm trường avatar
                                    created_at INTEGER NOT NULL,
                                    updated_at INTEGER NOT NULL,
                                    timestamp INTEGER NOT NULL
                                )
                            """)

                            # Tạo bảng sessions với UNIQUE trên username
                            await conn.execute("""
                                CREATE TABLE IF NOT EXISTS sessions (
                                    id TEXT PRIMARY KEY,
                                    username TEXT UNIQUE NOT NULL,
                                    session_token TEXT NOT NULL,
                                    created_at INTEGER NOT NULL,
                                    expires_at INTEGER NOT NULL,
                                    timestamp INTEGER NOT NULL
                                )
                            """)

                            # Tạo bảng client_states với UNIQUE trên username
                            await conn.execute("""
                                CREATE TABLE IF NOT EXISTS client_states (
                                    id TEXT PRIMARY KEY,
                                    username TEXT UNIQUE NOT NULL,
                                    session_token TEXT NOT NULL,
                                    state TEXT NOT NULL,
                                    timestamp INTEGER NOT NULL
                                )
                            """)

                            # Tạo bảng collection_schemas
                            await conn.execute("""
                                CREATE TABLE IF NOT EXISTS collection_schemas (
                                    id TEXT PRIMARY KEY,
                                    collection_name TEXT UNIQUE NOT NULL,
                                    fields TEXT NOT NULL,
                                    timestamp INTEGER NOT NULL
                                )
                            """)

                            # Tạo bảng sync_log
                            await conn.execute("""
                                CREATE TABLE IF NOT EXISTS sync_log (
                                    id TEXT PRIMARY KEY,
                                    table_name TEXT NOT NULL,
                                    record_id TEXT,
                                    action TEXT NOT NULL,
                                    timestamp INTEGER NOT NULL,
                                    details TEXT,
                                    last_sync INTEGER
                                )
                            """)

                            # Tạo bảng qa_data
                            await conn.execute("""
                                CREATE TABLE IF NOT EXISTS qa_data (
                                    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                                    id TEXT UNIQUE NOT NULL,
                                    question TEXT NOT NULL,
                                    answer TEXT NOT NULL,
                                    category TEXT,
                                    created_by TEXT NOT NULL,
                                    created_at INTEGER NOT NULL,
                                    timestamp INTEGER NOT NULL
                                )
                            """)

                            # Tạo virtual table FTS5 cho qa_data
                            await conn.execute("""
                                CREATE VIRTUAL TABLE IF NOT EXISTS qa_fts USING fts5(
                                    question, answer, category,
                                    content='qa_data', content_rowid='rowid'
                                )
                            """)

                            # Trigger sync sau INSERT
                            await conn.execute("""
                                CREATE TRIGGER IF NOT EXISTS qa_data_ai AFTER INSERT ON qa_data BEGIN
                                    INSERT INTO qa_fts(rowid, question, answer, category)
                                    VALUES (new.rowid, new.question, new.answer, new.category);
                                END;
                            """)

                            # Trigger sync sau DELETE
                            await conn.execute("""
                                CREATE TRIGGER IF NOT EXISTS qa_data_ad AFTER DELETE ON qa_data BEGIN
                                    INSERT INTO qa_fts(qa_fts, rowid, question, answer, category)
                                    VALUES('delete', old.rowid, old.question, old.answer, old.category);
                                END;
                            """)

                            # Trigger sync sau UPDATE
                            await conn.execute("""
                                CREATE TRIGGER IF NOT EXISTS qa_data_au AFTER UPDATE ON qa_data BEGIN
                                    INSERT INTO qa_fts(qa_fts, rowid, question, answer, category)
                                    VALUES('delete', old.rowid, old.question, old.answer, old.category);
                                    INSERT INTO qa_fts(rowid, question, answer, category)
                                    VALUES(new.rowid, new.question, new.answer, new.category);
                                END;
                            """)

                            # Migrate data cũ
                            await conn.execute("""
                                INSERT OR IGNORE INTO qa_fts(rowid, question, answer, category)
                                SELECT rowid, question, answer, category FROM qa_data
                                WHERE rowid NOT IN (SELECT rowid FROM qa_fts WHERE qa_fts != 'delete')
                            """)

                            self.logger.info("Đã setup FTS5 cho qa_data với INTEGER rowid (full-text search)")

                            # Tạo bảng chat_config
                            await conn.execute("""
                                CREATE TABLE IF NOT EXISTS chat_config (
                                    id TEXT PRIMARY KEY,
                                    username TEXT NOT NULL,
                                    personalization TEXT,
                                    event_schedule TEXT,
                                    similarity_threshold REAL,
                                    ai_context TEXT,
                                    data_sources TEXT,
                                    model TEXT,
                                    chat_mode TEXT,
                                    timestamp INTEGER NOT NULL
                                )
                            """)

                            # Tạo bảng chat_messages với trường file_url
                            await conn.execute("""
                                CREATE TABLE IF NOT EXISTS chat_messages (
                                    id TEXT PRIMARY KEY,
                                    session_token TEXT NOT NULL,
                                    username TEXT NOT NULL,
                                    content TEXT,
                                    role TEXT,
                                    type TEXT,
                                    file_url TEXT,  -- Thêm trường file_url
                                    timestamp INTEGER NOT NULL
                                )
                            """)

                            # Thêm dữ liệu mặc định
                            current_time = int(time.time())
                            async with conn.execute(
                                "SELECT COUNT(*) FROM users WHERE role = 'admin'"
                            ) as cursor:
                                admin_count = (await cursor.fetchone())[0]
                                if admin_count == 0:
                                    admin_id = hashlib.sha256(
                                        Config.ADMIN_USERNAME.encode()
                                    ).hexdigest()
                                    password_hash = self.pwd_context.hash(
                                        Config.ADMIN_PASSWORD
                                    )
                                    bot_password_hash = self.pwd_context.hash(
                                        Config.ADMIN_BOT_PASSWORD
                                    ) if Config.ADMIN_BOT_PASSWORD else None
                                    await conn.execute(
                                        "INSERT INTO users (id, username, password, "
                                        "bot_password, role, created_at, updated_at, "
                                        "timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                        (
                                            admin_id,
                                            Config.ADMIN_USERNAME,
                                            password_hash,
                                            bot_password_hash,
                                            "admin",
                                            current_time,
                                            current_time,
                                            current_time
                                        )
                                    )
                                    self.logger.info(
                                        f"Tạo tài khoản admin mặc định: {Config.ADMIN_USERNAME}"
                                    )

                            async with conn.execute(
                                "SELECT COUNT(*) FROM sessions WHERE username = ?",
                                (Config.ADMIN_USERNAME,)
                            ) as cursor:
                                session_count = (await cursor.fetchone())[0]
                                if session_count == 0:
                                    session_token = hashlib.sha256(
                                        f"{Config.ADMIN_USERNAME}_{current_time}".encode()
                                    ).hexdigest()
                                    session_id = hashlib.sha256(
                                        session_token.encode()
                                    ).hexdigest()
                                    expires_at = current_time + Config.SESSION_MAX_AGE
                                    await conn.execute(
                                        "INSERT INTO sessions (id, username, session_token, "
                                        "created_at, expires_at, timestamp) "
                                        "VALUES (?, ?, ?, ?, ?, ?)",
                                        (
                                            session_id,
                                            Config.ADMIN_USERNAME,
                                            session_token,
                                            current_time,
                                            expires_at,
                                            current_time
                                        )
                                    )
                                    self.logger.info(
                                        f"Tạo session mặc định cho {Config.ADMIN_USERNAME}"
                                    )

                            async with conn.execute(
                                "SELECT COUNT(*) FROM client_states WHERE username = ?",
                                (Config.ADMIN_USERNAME,)
                            ) as cursor:
                                state_count = (await cursor.fetchone())[0]
                                if state_count == 0:
                                    session_token = hashlib.sha256(
                                        f"{Config.ADMIN_USERNAME}_{current_time}".encode()
                                    ).hexdigest()
                                    state = {
                                        "username": Config.ADMIN_USERNAME,
                                        "session_token": session_token,
                                        "authenticated": True,
                                        "login_attempts": 0,
                                        "selected_tab": "Chat",
                                        "timestamp": current_time
                                    }
                                    state_id = hashlib.sha256(
                                        f"{Config.ADMIN_USERNAME}_{session_token}".encode()
                                    ).hexdigest()
                                    state_json = json.dumps(state, ensure_ascii=False)
                                    await conn.execute(
                                        "INSERT INTO client_states (id, username, "
                                        "session_token, state, timestamp) "
                                        "VALUES (?, ?, ?, ?, ?)",
                                        (
                                            state_id,
                                            Config.ADMIN_USERNAME,
                                            session_token,
                                            state_json,
                                            current_time
                                        )
                                    )
                                    self.logger.info(
                                        f"Tạo client_state mặc định cho {Config.ADMIN_USERNAME}"
                                    )
                                else:
                                    await conn.execute(
                                        "UPDATE client_states SET timestamp = ? "
                                        "WHERE username = ?",
                                        (current_time, Config.ADMIN_USERNAME)
                                    )
                                    self.logger.info(
                                        f"Cập nhật timestamp cho client_state của "
                                        f"{Config.ADMIN_USERNAME}"
                                    )

                            # Cập nhật schema trong collection_schemas
                            schemas = {
                                "users": {
                                    "id": "TEXT",
                                    "username": "TEXT",
                                    "password": "TEXT",
                                    "bot_password": "TEXT",
                                    "role": "TEXT",
                                    "avatar": "TEXT",  # Thêm trường avatar
                                    "created_at": "INTEGER",
                                    "updated_at": "INTEGER",
                                    "timestamp": "INTEGER"
                                },
                                "sessions": {
                                    "id": "TEXT",
                                    "username": "TEXT",
                                    "session_token": "TEXT",
                                    "created_at": "INTEGER",
                                    "expires_at": "INTEGER",
                                    "timestamp": "INTEGER"
                                },
                                "client_states": {
                                    "id": "TEXT",
                                    "username": "TEXT",
                                    "session_token": "TEXT",
                                    "state": "TEXT",
                                    "timestamp": "INTEGER"
                                },
                                "collection_schemas": {
                                    "id": "TEXT",
                                    "collection_name": "TEXT",
                                    "fields": "TEXT",
                                    "timestamp": "INTEGER"
                                },
                                "sync_log": {
                                    "id": "TEXT",
                                    "table_name": "TEXT",
                                    "record_id": "TEXT",
                                    "action": "TEXT",
                                    "timestamp": "INTEGER",
                                    "details": "TEXT",
                                    "last_sync": "INTEGER"
                                },
                                "qa_data": {
                                    "rowid": "INTEGER",
                                    "id": "TEXT",
                                    "question": "TEXT",
                                    "answer": "TEXT",
                                    "category": "TEXT",
                                    "created_by": "TEXT",
                                    "created_at": "INTEGER",
                                    "timestamp": "INTEGER"
                                },
                                "chat_config": {
                                    "id": "TEXT",
                                    "username": "TEXT",
                                    "personalization": "TEXT",
                                    "event_schedule": "TEXT",
                                    "similarity_threshold": "REAL",
                                    "ai_context": "TEXT",
                                    "data_sources": "TEXT",
                                    "model": "TEXT",
                                    "chat_mode": "TEXT",
                                    "timestamp": "INTEGER"
                                },
                                "chat_messages": {
                                    "id": "TEXT",
                                    "session_token": "TEXT",
                                    "username": "TEXT",
                                    "content": "TEXT",
                                    "role": "TEXT",
                                    "type": "TEXT",
                                    "file_url": "TEXT",  # Thêm trường file_url
                                    "timestamp": "INTEGER"
                                }
                            }
                            for collection_name, fields in schemas.items():
                                schema_json = json.dumps(fields, ensure_ascii=False)
                                try:
                                    json.loads(schema_json)
                                except json.JSONDecodeError as e:
                                    self.logger.error(
                                        f"Invalid JSON for schema {collection_name}: {str(e)}"
                                    )
                                    raise DatabaseError(f"Invalid JSON for schema: {str(e)}")
                                await conn.execute(
                                    "INSERT OR REPLACE INTO collection_schemas "
                                    "(id, collection_name, fields, timestamp) "
                                    "VALUES (?, ?, ?, ?)",
                                    (
                                        hashlib.sha256(collection_name.encode()).hexdigest(),
                                        collection_name,
                                        schema_json,
                                        current_time
                                    )
                                )

                            await conn.commit()
                            self.logger.info(
                                "Khởi tạo SQLite thành công với các bảng và dữ liệu mặc định"
                            )
                            return
                    except aiosqlite.OperationalError as e:
                        self.logger.error(
                            f"Lỗi khởi tạo SQLite (thử {attempt+1}/{max_attempts}): {str(e)}"
                        )
                        if "database is locked" in str(e).lower() and attempt < max_attempts - 1:
                            await asyncio.sleep(retry_delay * (2 ** attempt))
                            continue
                        raise DatabaseError(f"Lỗi khởi tạo SQLite: {str(e)}")
                    except Exception as e:
                        self.logger.error(
                            f"Lỗi không xác định khi khởi tạo SQLite: {str(e)}"
                        )
                        raise DatabaseError(f"Lỗi khởi tạo SQLite: {str(e)}")
        except asyncio.TimeoutError as e:
            self.logger.error(f"Timeout khi khởi tạo SQLite: {str(e)}")
            raise DatabaseError(f"Timeout khi khởi tạo SQLite: {str(e)}")

    async def register_user(self, username: str, password: str, bot_password: Optional[str] = None) -> Dict:
        try:
            async with asyncio.timeout(60):  # Timeout 1 phút
                if not validate_name(username):
                    return {"error": "Tên người dùng không hợp lệ"}
                if not validate_password_strength(password):
                    return {"error": "Mật khẩu không đáp ứng yêu cầu bảo mật"}
                if bot_password and not validate_password_strength(bot_password):
                    return {"error": "Mật khẩu bot không đáp ứng yêu cầu bảo mật"}

                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                    async with conn.execute(
                        "SELECT COUNT(*) FROM users WHERE username = ?",
                        (username,)
                    ) as cursor:
                        if (await cursor.fetchone())[0] > 0:
                            self.logger.warning(f"Tên người dùng {username} đã tồn tại")
                            return {"error": "Tên người dùng đã tồn tại"}

                    current_time = int(time.time())
                    user_id = hashlib.sha256(username.encode()).hexdigest()
                    password_hash = self.pwd_context.hash(password)
                    bot_password_hash = self.pwd_context.hash(bot_password) if bot_password else None
                    role = "admin" if username == Config.ADMIN_USERNAME else "user"

                    await conn.execute(
                        "INSERT INTO users "
                        "(id, username, password, bot_password, role, created_at, updated_at, timestamp) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (user_id, username, password_hash, bot_password_hash, role, current_time,
                         current_time, current_time)
                    )
                    self.logger.info(f"Tạo người dùng mới: {username}")

                    session_token = hashlib.sha256(f"{username}_{current_time}".encode()).hexdigest()
                    session_id = hashlib.sha256(session_token.encode()).hexdigest()
                    expires_at = current_time + Config.SESSION_MAX_AGE
                    await conn.execute(
                        "INSERT INTO sessions "
                        "(id, username, session_token, created_at, expires_at, timestamp) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (session_id, username, session_token, current_time, expires_at, current_time)
                    )
                    self.logger.info(f"Tạo session mới cho {username}: {session_token[:10]}...")

                    state = {
                        "username": username,
                        "session_token": session_token,
                        "authenticated": True,
                        "login_attempts": 0,
                        "selected_tab": "Chat",
                        "timestamp": current_time,
                        "role": role
                    }
                    state = self.sanitize_state(state)
                    if len(json.dumps(state, ensure_ascii=False).encode()) > 1048576:
                        self.logger.error(f"Kích thước client_state vượt quá 1MB cho {username}")
                        return {"error": "Trạng thái phiên quá lớn"}
                    state_id = hashlib.sha256(f"{username}_{session_token}".encode()).hexdigest()
                    state_json = json.dumps(state, ensure_ascii=False)
                    await conn.execute(
                        "INSERT INTO client_states "
                        "(id, username, session_token, state, timestamp) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (state_id, username, session_token, state_json, current_time)
                    )
                    self.logger.info(f"Tạo client_state mới cho {username}: {state_id[:10]}...")

                    await conn.execute(
                        "INSERT INTO sync_log "
                        "(id, table_name, record_id, action, timestamp, details) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            str(uuid.uuid4()),
                            "users",
                            user_id,
                            "INSERT",
                            current_time,
                            json.dumps({"username": username, "action": "create_user"})
                        )
                    )
                    await conn.execute(
                        "INSERT INTO sync_log "
                        "(id, table_name, record_id, action, timestamp, details) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            str(uuid.uuid4()),
                            "sessions",
                            session_id,
                            "INSERT",
                            current_time,
                            json.dumps({"username": username, "action": "create_session"})
                        )
                    )
                    await conn.execute(
                        "INSERT INTO sync_log "
                        "(id, table_name, record_id, action, timestamp, details) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            str(uuid.uuid4()),
                            "client_states",
                            state_id,
                            "INSERT",
                            current_time,
                            json.dumps({"username": username, "action": "create_client_state"})
                        )
                    )

                    await conn.commit()
                    self.logger.info(f"Đăng ký thành công cho {username}, session_token={session_token[:10]}...")
                    return {
                        "success": "Đăng ký thành công",
                        "session_token": session_token,
                        "role": role
                    }

        except aiosqlite.IntegrityError as e:
            self.logger.error(f"Lỗi tính toàn vẹn khi đăng ký {username}: {str(e)}")
            return {"error": "Tên người dùng đã tồn tại hoặc lỗi cơ sở dữ liệu"}
        except asyncio.TimeoutError as e:
            self.logger.error(f"Timeout khi đăng ký {username}: {str(e)}")
            return {"error": f"Timeout khi đăng ký: {str(e)}"}
        except Exception as e:
            self.logger.error(f"Lỗi đăng ký cho {username}: {str(e)}", exc_info=True)
            return {"error": f"Lỗi đăng ký: {str(e)}"}
            
    
    async def get_client_state(self, session_token: str, username: str) -> Dict:
        """Lấy trạng thái phiên của người dùng từ cơ sở dữ liệu SQLite.

        Args:
            session_token (str): Mã phiên của người dùng.
            username (str): Tên người dùng.

        Returns:
            Dict: Trạng thái phiên hoặc trạng thái lỗi nếu có vấn đề.

        Raises:
            DatabaseError: Nếu xảy ra lỗi khi truy vấn cơ sở dữ liệu.
        """
        try:
            async with asyncio.timeout(60):  # Timeout 1 phút
                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                    # Lấy trạng thái từ client_states
                    async with conn.execute(
                        "SELECT state, timestamp FROM client_states WHERE username = ? AND session_token = ?",
                        (username, session_token)
                    ) as cursor:
                        row = await cursor.fetchone()
                        if row:
                            try:
                                state = json.loads(row[0])
                                timestamp = row[1]
                                # Kiểm tra trạng thái hết hạn
                                if timestamp < int(time.time()) - Config.SESSION_MAX_AGE:
                                    self.logger.warning(
                                        f"Trạng thái hết hạn cho {username}, session_token={session_token[:10]}..."
                                    )
                                    await self.clear_client_state(session_token, username, log_sync=True)
                                    return {
                                        "username": username,
                                        "session_token": session_token,
                                        "authenticated": False,
                                        "login_attempts": 0,
                                        "selected_tab": Config.DEFAULT_TAB if hasattr(Config, 'DEFAULT_TAB') else "Chat",
                                        "timestamp": int(time.time())
                                    }
                                state["authenticated"] = state.get("authenticated", False)
                                self.logger.info(
                                    f"get_client_state: Tìm thấy trạng thái cho {username}, "
                                    f"session_token={session_token[:10]}..., authenticated={state['authenticated']}"
                                )
                                return state
                            except json.JSONDecodeError as e:
                                self.logger.error(
                                    f"Trạng thái JSON hòng cho {username}, session_token={session_token[:10]}...: {str(e)}"
                                )
                                # Khởi tạo trạng thái mới thay vì trả về lỗi
                                await self.clear_client_state(session_token, username, log_sync=True)
                                return {
                                    "username": username,
                                    "session_token": session_token,
                                    "authenticated": False,
                                    "login_attempts": 0,
                                    "selected_tab": Config.DEFAULT_TAB if hasattr(Config, 'DEFAULT_TAB') else "Chat",
                                    "timestamp": int(time.time())
                                }

                    # Kiểm tra phiên trong sessions nếu không tìm thấy trạng thái
                    self.logger.warning(
                        f"Không tìm thấy trạng thái cho {username}, session_token={session_token[:10]}..."
                    )
                    async with conn.execute(
                        "SELECT session_token, expires_at FROM sessions WHERE username = ? AND session_token = ?",
                        (username, session_token)
                    ) as cursor:
                        session_row = await cursor.fetchone()
                        if session_row and session_row[1] > int(time.time()):
                            self.logger.info(
                                f"Tìm thấy phiên hợp lệ trong sessions cho {username}, "
                                f"session_token={session_token[:10]}..."
                            )
                            return {
                                "username": username,
                                "session_token": session_token,
                                "authenticated": True,
                                "login_attempts": 0,
                                "selected_tab": Config.DEFAULT_TAB if hasattr(Config, 'DEFAULT_TAB') else "Chat",
                                "timestamp": int(time.time())
                            }

                    # Trả về trạng thái mặc định nếu không tìm thấy phiên
                    self.logger.warning(
                        f"Không tìm thấy phiên hợp lệ trong sessions cho {username}, "
                        f"session_token={session_token[:10]}..."
                    )
                    return {
                        "username": username,
                        "session_token": session_token,
                        "authenticated": False,
                        "login_attempts": 0,
                        "selected_tab": "",
                        "timestamp": int(time.time())
                    }

        except asyncio.TimeoutError as e:
            self.logger.error(f"Timeout khi lấy trạng thái cho {username}: {str(e)}")
            raise DatabaseError(f"Timeout khi lấy trạng thái: {str(e)}")
        except Exception as e:
            self.logger.error(f"Lỗi lấy trạng thái cho {username}: {str(e)}", exc_info=True)
            raise DatabaseError(f"Lỗi lấy trạng thái: {str(e)}")

    async def save_client_state(self, session_token: str, state: Dict) -> None:
        """Lưu trạng thái phiên của người dùng vào cơ sở dữ liệu SQLite.

        Args:
            session_token (str): Mã phiên của người dùng.
            state (Dict): Trạng thái phiên cần lưu.

        Raises:
            DatabaseError: Nếu xảy ra lỗi khi lưu trạng thái hoặc phiên không hợp lệ.
        """
        try:
            async with asyncio.timeout(60):  # Timeout 1 phút
                state = self.sanitize_state(state)
                state["timestamp"] = int(time.time())
                username = state.get("username")
                if not username:
                    self.logger.error("Không thể lưu trạng thái: thiếu username")
                    raise DatabaseError("Thiếu username trong trạng thái")

                state_json = json.dumps(state, ensure_ascii=False)
                if len(state_json.encode()) > 1_000_000:
                    self.logger.error(f"Kích thước trạng thái vượt quá 1MB cho {username}")
                    raise DatabaseError("Trạng thái phiên quá lớn")

                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                    # Kiểm tra session hợp lệ
                    async with conn.execute(
                        "SELECT session_token, expires_at FROM sessions WHERE username = ? AND session_token = ?",
                        (username, session_token)
                    ) as cursor:
                        row = await cursor.fetchone()
                        state_id = hashlib.sha256(f"{username}_{session_token}".encode()).hexdigest()

                        if not row or row[1] <= int(time.time()):
                            self.logger.error(
                                f"Phiên không hợp lệ hoặc hết hạn cho {username}, "
                                f"session_token={session_token[:10]}..."
                            )
                            raise DatabaseError("Phiên không hợp lệ, không thể lưu trạng thái")

                        # Hợp nhất trạng thái hiện tại
                        async with conn.execute(
                            "SELECT state FROM client_states WHERE username = ? AND session_token = ?",
                            (username, session_token)
                        ) as cursor:
                            existing_row = await cursor.fetchone()
                            if existing_row:
                                try:
                                    current_state = json.loads(existing_row[0])
                                    current_state.update(state)
                                    state = current_state
                                    state_json = json.dumps(state, ensure_ascii=False)
                                except json.JSONDecodeError as e:
                                    self.logger.error(
                                        f"Trạng thái JSON hỏng cho {username}, "
                                        f"session_token={session_token[:10]}...: {str(e)}"
                                    )
                                    # Khởi tạo trạng thái mới thay vì giữ trạng thái hỏng
                                    state = {
                                        "username": username,
                                        "session_token": session_token,
                                        "authenticated": state.get("authenticated", False),
                                        "login_attempts": state.get("login_attempts", 0),
                                        "selected_tab": state.get(
                                            "selected_tab",
                                            Config.DEFAULT_TAB if hasattr(Config, 'DEFAULT_TAB') else "Chat"
                                        ),
                                        "timestamp": int(time.time())
                                    }
                                    state_json = json.dumps(state, ensure_ascii=False)

                        # Lưu trạng thái
                        await conn.execute(
                            "INSERT OR REPLACE INTO client_states "
                            "(id, username, session_token, state, timestamp) VALUES (?, ?, ?, ?, ?)",
                            (state_id, username, session_token, state_json, state["timestamp"])
                        )
                        # Ghi log hành động
                        await conn.execute(
                            "INSERT INTO sync_log "
                            "(id, table_name, record_id, action, timestamp, details) VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                str(uuid.uuid4()),
                                "client_states",
                                state_id,
                                "UPDATE",
                                state["timestamp"],
                                json.dumps({
                                    "username": username,
                                    "action": "update_client_state",
                                    "selected_tab": state.get("selected_tab")
                                }, ensure_ascii=False)
                            )
                        )
                        await conn.commit()
                        self.logger.info(
                            f"Cập nhật trạng thái cho {username}, session_token={session_token[:10]}..., "
                            f"selected_tab={state.get('selected_tab')}"
                        )

        except json.JSONDecodeError as e:
            self.logger.error(f"Lỗi JSON khi lưu trạng thái cho {username}: {str(e)}")
            raise DatabaseError(f"Lỗi JSON: {str(e)}")
        except asyncio.TimeoutError as e:
            self.logger.error(f"Timeout khi lưu trạng thái cho {username}: {str(e)}")
            raise DatabaseError(f"Timeout khi lưu trạng thái: {str(e)}")
        except Exception as e:
            self.logger.error(f"Lỗi lưu trạng thái cho {username}: {str(e)}", exc_info=True)
            raise DatabaseError(f"Lỗi lưu trạng thái: {str(e)}")

    def sanitize_state(self, state: Dict) -> Dict:
        """Làm sạch trạng thái để đảm bảo chỉ lưu các giá trị hợp lệ.

        Args:
            state (Dict): Trạng thái cần làm sạch.

        Returns:
            Dict: Trạng thái đã được làm sạch.
        """
        clean_state = {}
        for key, value in state.items():
            if key.endswith("_container"):
                continue
            if isinstance(value, (str, int, float, bool, type(None))):
                clean_state[key] = value
            elif isinstance(value, (list, dict)):
                try:
                    json.dumps(value)
                    clean_state[key] = value
                except (TypeError, OverflowError):
                    self.logger.warning(f"Bỏ qua khóa {key} vì không thể tuần tự hóa JSON")
            else:
                self.logger.warning(f"Bỏ qua khóa {key} vì kiểu dữ liệu không hỗ trợ JSON: {type(value)}")
        return clean_state

    async def clear_client_state(self, session_token: str, username: str, log_sync: bool = False) -> None:
        """Xóa trạng thái phiên của người dùng.

        Args:
            session_token (str): Mã phiên của người dùng.
            username (str): Tên người dùng.
            log_sync (bool): Có ghi log hành động vào sync_log hay không.

        Raises:
            DatabaseError: Nếu xảy ra lỗi khi xóa trạng thái.
        """
        try:
            async with asyncio.timeout(60):
                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                    state_id = hashlib.sha256(f"{username}_{session_token}".encode()).hexdigest()
                    await conn.execute(
                        "DELETE FROM client_states WHERE username = ? AND session_token = ?",
                        (username, session_token)
                    )
                    if log_sync:
                        await conn.execute(
                            "INSERT INTO sync_log "
                            "(id, table_name, record_id, action, timestamp, details) VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                str(uuid.uuid4()),
                                "client_states",
                                state_id,
                                "DELETE",
                                int(time.time()),
                                json.dumps({"username": username, "action": "clear_client_state"})
                            )
                        )
                    await conn.commit()
                    self.logger.info(
                        f"Đã xóa trạng thái cho {username}, session_token={session_token[:10]}..."
                    )
        except asyncio.TimeoutError as e:
            self.logger.error(f"Timeout khi xóa trạng thái cho {username}: {str(e)}")
            raise DatabaseError(f"Timeout khi xóa trạng thái: {str(e)}")
        except Exception as e:
            self.logger.error(f"Lỗi xóa trạng thái cho {username}: {str(e)}", exc_info=True)
            raise DatabaseError(f"Lỗi xóa trạng thái: {str(e)}")
    
    async def _is_database_locked(self) -> bool:
        """Kiểm tra xem cơ sở dữ liệu có bị khóa không."""
        try:
            async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=5.0) as conn:
                await conn.execute("PRAGMA journal_mode=WAL")
                return False
        except aiosqlite.OperationalError as e:
            if "database is locked" in str(e).lower():
                return True
            raise DatabaseError(f"Lỗi kiểm tra trạng thái khóa: {str(e)}")

    
    async def get_collection_schema(self, collection_name: str) -> Dict:
        """Lấy schema của một collection từ SQLite."""
        try:
            async with asyncio.timeout(60):  # Timeout 1 phút
                if not validate_name(collection_name):
                    return {"error": "Tên collection không hợp lệ"}

                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                    # Kiểm tra xem bảng có tồn tại
                    async with conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
                        (collection_name,)
                    ) as cursor:
                        if not await cursor.fetchone():
                            return {"error": f"Collection {collection_name} không tồn tại"}

                    # Lấy danh sách trường từ collection_schemas
                    async with conn.execute(
                        "SELECT fields FROM collection_schemas WHERE collection_name = ?",
                        (collection_name,)
                    ) as cursor:
                        row = await cursor.fetchone()
                        if not row:
                            return {"error": f"Không tìm thấy schema cho collection {collection_name}"}
                        fields = json.loads(row[0])

                    # Lấy kiểu dữ liệu từ PRAGMA table_info
                    async with conn.execute(f'PRAGMA table_info("{collection_name}")') as cursor:
                        columns = {row[1]: row[2] for row in await cursor.fetchall() if row[1] in fields}

                    self.logger.info(f"Lấy schema cho {collection_name}: {columns}")
                    return {"success": f"Schema cho {collection_name}", "schema": columns}

        except asyncio.TimeoutError as e:
            self.logger.error(f"Timeout khi lấy schema cho {collection_name}: {str(e)}")
            return {"error": f"Timeout khi lấy schema: {str(e)}"}
        except Exception as e:
            self.logger.error(f"Lỗi lấy schema cho {collection_name}: {str(e)}")
            return {"error": f"Lỗi lấy schema: {str(e)}"}

    async def has_permission(self, username: str, action: str) -> bool:
        """Kiểm tra quyền của người dùng."""
        if not validate_name(action):
            self.logger.warning(f"Action không hợp lệ: {action}")
            return False

        try:
            async with asyncio.timeout(30):  # Timeout 30 giây
                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                    async with conn.execute(
                        "SELECT role FROM users WHERE username = ?",
                        (username,)
                    ) as cursor:
                        row = await cursor.fetchone()
                        if not row:
                            self.logger.warning(f"Không tìm thấy người dùng {username}")
                            return False

                        role = row[0]
                        # Admin có mọi quyền
                        if role == "admin":
                            return True

                        # Quyền cụ thể cho các vai trò khác
                        allowed_actions = {
                            "user": ["chat_access", "read_records"],
                            # Thêm các vai trò và quyền khác nếu cần
                        }
                        return action in allowed_actions.get(role, [])

        except asyncio.TimeoutError as e:
            self.logger.error(f"Timeout khi kiểm tra quyền cho {username}: {str(e)}")
            return False
        except Exception as e:
            self.logger.error(f"Lỗi kiểm tra quyền cho {username}: {str(e)}")
            return False

    async def create_collection(self, collection_name: str, fields: Dict, username: str) -> Dict:
        """Tạo một collection mới trong SQLite."""
        if not await self.has_permission(username, "create_collection"):
            self.logger.error(f"{username}: Không có quyền tạo collection")
            return {"error": "Không có quyền tạo collection"}

        try:
            async with asyncio.timeout(60):  # Timeout 1 phút
                if not validate_name(collection_name):
                    return {"error": "Tên collection không hợp lệ"}

                if collection_name in self.protected_collections:
                    self.logger.error(f"{username}: Không thể tạo collection được bảo vệ: {collection_name}")
                    return {"error": f"Không thể tạo collection được bảo vệ: {collection_name}"}

                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                    # Kiểm tra xem bảng đã tồn tại
                    async with conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
                        (collection_name,)
                    ) as cursor:
                        if await cursor.fetchone():
                            return {"error": f"Collection {collection_name} đã tồn tại"}

                    # Tạo danh sách cột và schema
                    columns = ["id TEXT PRIMARY KEY", "timestamp INTEGER NOT NULL"]
                    schema_fields = {"id": "TEXT", "timestamp": "INTEGER"}
                    valid_dtypes = ["TEXT", "INTEGER", "REAL", "BLOB"]
                    for field, info in fields.items():
                        if field in ["id", "timestamp"]:
                            self.logger.debug(f"{username}: Bỏ qua field {field} trong schema")
                            continue
                        dtype = info.get("type", "TEXT").upper() if isinstance(info, dict) else str(info).upper()
                        if dtype not in valid_dtypes:
                            self.logger.warning(f"{username}: Kiểu dữ liệu không hợp lệ cho {field}: {dtype}, mặc định là TEXT")
                            dtype = "TEXT"
                        columns.append(f'"{field}" {dtype}')
                        schema_fields[field] = dtype

                    # Tạo bảng
                    columns_sql = ", ".join(columns)
                    await conn.execute(f'CREATE TABLE "{collection_name}" ({columns_sql})')

                    # Lưu schema vào collection_schemas
                    schema_json = json.dumps(schema_fields, ensure_ascii=False)
                    await conn.execute(
                        "INSERT INTO collection_schemas (id, collection_name, fields, timestamp) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            hashlib.sha256(collection_name.encode()).hexdigest(),
                            collection_name,
                            schema_json,
                            int(time.time())
                        )
                    )

                    # Ghi log
                    current_time = int(time.time())
                    details = {
                        "username": username,
                        "action": "create_collection",
                        "fields": schema_fields
                    }
                    try:
                        details_json = json.dumps(details, ensure_ascii=False)
                    except TypeError as e:
                        self.logger.error(f"{username}: Lỗi mã hóa JSON cho sync_log: {str(e)}")
                        details_json = json.dumps({"error": "Không thể mã hóa details"}, ensure_ascii=False)

                    await conn.execute(
                        "INSERT INTO sync_log (id, table_name, record_id, action, timestamp, details) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            str(uuid.uuid4()),
                            collection_name,
                            None,
                            "CREATE_TABLE",
                            current_time,
                            details_json
                        )
                    )
                    await conn.commit()

                    self.logger.info(f"{username}: Tạo collection {collection_name} với fields {schema_fields}")
                    return {"success": f"Đã tạo collection {collection_name}"}

        except asyncio.TimeoutError as e:
            self.logger.error(f"{username}: Timeout khi tạo collection {collection_name}: {str(e)}")
            return {"error": f"Timeout khi tạo collection: {str(e)}"}
        except Exception as e:
            self.logger.error(f"{username}: Lỗi tạo collection {collection_name}: {str(e)}")
            return {"error": f"Lỗi tạo collection: {str(e)}"}

    
    async def drop_collection(self, collection_name: str, username: str) -> Dict:
        """Xóa một bảng/collection."""
        try:
            async with asyncio.timeout(60):  # Timeout 1 phút
                # Kiểm tra collection được bảo vệ
                if collection_name in self.protected_collections:
                    self.logger.error(f"{username}: Không thể xóa collection được bảo vệ: {collection_name}")
                    return {"error": f"Collection {collection_name} là collection được bảo vệ"}

                # Kiểm tra tên collection hợp lệ
                if not validate_name(collection_name):
                    return {"error": "Tên collection không hợp lệ"}

                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                    # Kiểm tra xem bảng có tồn tại
                    async with conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
                        (collection_name,)
                    ) as cursor:
                        if not await cursor.fetchone():
                            return {"error": f"Collection {collection_name} không tồn tại"}

                    # Xóa bảng
                    await conn.execute(f'DROP TABLE "{collection_name}"')

                    # Xóa lược đồ trong collection_schemas
                    await conn.execute(
                        "DELETE FROM collection_schemas WHERE collection_name = ?",
                        (collection_name,)
                    )

                    # Ghi log đồng bộ
                    current_time = int(time.time())
                    await conn.execute(
                        "INSERT INTO sync_log (id, table_name, record_id, action, timestamp, details) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            str(uuid.uuid4()),
                            collection_name,
                            None,
                            "DROP_TABLE",
                            current_time,
                            json.dumps({"username": username, "action": "drop_collection"})
                        )
                    )
                    await conn.commit()
                    self.logger.info(f"{username}: Đã xóa collection {collection_name}")

                    # Xóa collection trên Firestore nếu khả dụng
                    if self.core.firestore_handler.firestore_available:
                        collection_ref = self.core.firestore_handler.db.collection(collection_name)
                        async for doc in collection_ref.stream():
                            await doc.reference.delete()
                        self.logger.info(f"{username}: Đã xóa tất cả bản ghi trong collection {collection_name} trên Firestore")
                        sync_result = await self.core.firestore_handler.sync_from_sqlite(batch_size=1)
                        self.logger.info(f"{username}: Kết quả đồng bộ Firestore sau khi xóa collection: {sync_result}")

                    return {"success": f"Đã xóa collection {collection_name}"}

        except asyncio.TimeoutError as e:
            self.logger.error(f"{username}: Timeout khi xóa collection {collection_name}: {str(e)}")
            return {"error": f"Timeout khi xóa collection: {str(e)}"}
        except Exception as e:
            self.logger.error(f"{username}: Lỗi xóa collection {collection_name}: {str(e)}")
            return {"error": f"Lỗi xóa collection: {str(e)}"}

    
    
    async def delete_records_by_condition(self, collection_name: str, conditions: Dict, username: str) -> Dict:
        """Xóa các bản ghi trong SQLite theo điều kiện và ghi log."""
        try:
            async with asyncio.timeout(60):  # Timeout 1 phút
                if not validate_name(collection_name):
                    self.logger.error(f"{username}: Tên collection không hợp lệ: {collection_name}")
                    return {"error": "Tên collection không hợp lệ"}

                if collection_name in self.protected_collections:
                    self.logger.error(f"{username}: Không thể xóa bản ghi trong collection được bảo vệ: {collection_name}")
                    return {"error": f"Không thể xóa bản ghi trong collection được bảo vệ: {collection_name}"}

                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                    # Kiểm tra bảng có tồn tại
                    async with conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
                        (collection_name,)
                    ) as cursor:
                        if not await cursor.fetchone():
                            self.logger.error(f"{username}: Collection {collection_name} không tồn tại")
                            return {"error": f"Collection {collection_name} không tồn tại"}

                    # Xây dựng câu truy vấn SQL từ conditions
                    where_clauses = []
                    params = []
                    for field, value in conditions.items():
                        where_clauses.append(f'"{field}" = ?')
                        params.append(value)
                    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

                    # Lấy danh sách ID của các bản ghi sẽ bị xóa để ghi log
                    async with conn.execute(
                        f'SELECT id FROM "{collection_name}" WHERE {where_sql}', params
                    ) as cursor:
                        record_ids = [row[0] for row in await cursor.fetchall()]

                    if not record_ids:
                        self.logger.info(f"{username}: Không tìm thấy bản ghi nào trong {collection_name} khớp với điều kiện")
                        return {"success": f"Không tìm thấy bản ghi nào trong {collection_name} để xóa", "deleted_count": 0}

                    # Xóa các bản ghi
                    await conn.execute(
                        f'DELETE FROM "{collection_name}" WHERE {where_sql}', params
                    )

                    # Ghi log cho từng bản ghi bị xóa
                    current_time = int(time.time())
                    for record_id in record_ids:
                        details = {
                            "username": username,
                            "action": "delete_records_by_condition",
                            "collection_name": collection_name,
                            "record_id": record_id,
                            "conditions": conditions
                        }
                        try:
                            details_json = json.dumps(details, ensure_ascii=False)
                        except TypeError as e:
                            self.logger.error(f"{username}: Lỗi mã hóa JSON cho sync_log: {str(e)}")
                            details_json = json.dumps({"error": "Không thể mã hóa details"}, ensure_ascii=False)

                        await conn.execute(
                            "INSERT INTO sync_log (id, table_name, record_id, action, timestamp, details) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                str(uuid.uuid4()),
                                collection_name,
                                record_id,
                                "DELETE",
                                current_time,
                                details_json
                            )
                        )

                    await conn.commit()

                    deleted_count = len(record_ids)
                    self.logger.info(f"{username}: Đã xóa {deleted_count} bản ghi từ {collection_name}")
                    return {"success": f"Đã xóa {deleted_count} bản ghi từ {collection_name}", "deleted_count": deleted_count}

        except asyncio.TimeoutError as e:
            self.logger.error(f"{username}: Timeout khi xóa bản ghi trong {collection_name}: {str(e)}")
            return {"error": f"Timeout khi xóa bản ghi: {str(e)}", "deleted_count": 0}
        except Exception as e:
            self.logger.error(f"{username}: Lỗi xóa bản ghi trong {collection_name}: {str(e)}")
            return {"error": f"Lỗi xóa bản ghi: {str(e)}", "deleted_count": 0}

    async def read_records(self, collection_name: str, username: str, page: int = 1, page_size: int = 10, created_by: Optional[str] = None) -> Dict:
        """Đọc các bản ghi từ collection (bảng) được chỉ định, với filter created_by nếu có."""
        try:
            async with asyncio.timeout(60):  # Timeout 1 phút
                # Kiểm tra tên collection hợp lệ
                if not validate_name(collection_name):
                    self.logger.error(f"{username}: Tên collection không hợp lệ: {collection_name}")
                    return {"error": "Tên collection không hợp lệ"}

                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                    # Kiểm tra xem bảng có tồn tại
                    async with conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
                        (collection_name,)
                    ) as cursor:
                        if not await cursor.fetchone():
                            self.logger.error(f"{username}: Collection {collection_name} không tồn tại")
                            return {"error": f"Collection {collection_name} không tồn tại"}

                    # Lấy danh sách cột
                    async with conn.execute(f'PRAGMA table_info("{collection_name}")') as cursor:
                        columns = [row[1] for row in await cursor.fetchall()]

                    # Xây dựng query với filter created_by nếu có
                    query = f'SELECT * FROM "{collection_name}"'
                    params = []
                    if created_by and collection_name == "qa_data":
                        query += ' WHERE created_by = ?'
                        params.append(created_by)
                    query += ' LIMIT ? OFFSET ?'
                    params.extend([page_size, (page - 1) * page_size])

                    # Đọc bản ghi
                    async with conn.execute(query, params) as cursor:
                        rows = await cursor.fetchall()
                        results = [{columns[i]: row[i] for i in range(len(columns))} for row in rows]

                    # Lấy tổng số bản ghi với filter
                    count_query = f'SELECT COUNT(*) FROM "{collection_name}"'
                    count_params = []
                    if created_by and collection_name == "qa_data":
                        count_query += ' WHERE created_by = ?'
                        count_params.append(created_by)
                    async with conn.execute(count_query, count_params) as cursor:
                        total = (await cursor.fetchone())[0]

                    self.logger.info(f"{username}: Đã đọc {len(results)} bản ghi từ {collection_name}, trang {page}")
                    return {
                        "results": results,
                        "total": total,
                        "page": page,
                        "page_size": page_size
                    }

        except asyncio.TimeoutError as e:
            self.logger.error(f"{username}: Timeout khi đọc bản ghi từ {collection_name}: {str(e)}")
            return {"error": f"Timeout khi đọc bản ghi: {str(e)}"}
        except Exception as e:
            self.logger.error(f"{username}: Lỗi đọc bản ghi từ {collection_name}: {str(e)}")
            return {"error": f"Lỗi đọc bản ghi: {str(e)}"}

    async def update_record(self, collection_name: str, record_id: str, data: Dict, username: str) -> Dict:
        """Cập nhật một bản ghi trong collection (bảng) được chỉ định, chỉ cho created_by."""
        try:
            async with asyncio.timeout(60):  # Timeout 1 phút
                # Kiểm tra collection được bảo vệ
                if collection_name in self.protected_collections:
                    self.logger.error(f"{username}: Không thể cập nhật bản ghi trong collection được bảo vệ: {collection_name}")
                    return {"error": f"Collection {collection_name} là collection được bảo vệ"}

                # Kiểm tra tên collection hợp lệ
                if not validate_name(collection_name):
                    return {"error": "Tên collection không hợp lệ"}

                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                    # Kiểm tra xem bảng có tồn tại
                    async with conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
                        (collection_name,)
                    ) as cursor:
                        if not await cursor.fetchone():
                            return {"error": f"Collection {collection_name} không tồn tại"}

                    # Kiểm tra xem bản ghi có tồn tại và thuộc user
                    async with conn.execute(
                        f'SELECT id FROM "{collection_name}" WHERE id = ? AND created_by = ?',
                        (record_id, username)
                    ) as cursor:
                        if not await cursor.fetchone():
                            self.logger.error(f"{username}: Bản ghi {record_id} không tồn tại hoặc không thuộc user trong {collection_name}")
                            return {"error": f"Bản ghi {record_id} không tồn tại hoặc không thuộc {username} trong {collection_name}"}

                    # Lấy thông tin lược đồ hiện tại
                    async with conn.execute(f'PRAGMA table_info("{collection_name}")') as cursor:
                        existing_columns = {row[1]: row[2] for row in await cursor.fetchall()}
                    schema_fields = {"id": "TEXT", "timestamp": "INTEGER"}
                    async with conn.execute(
                        "SELECT fields FROM collection_schemas WHERE collection_name = ?",
                        (collection_name,)
                    ) as cursor:
                        row = await cursor.fetchone()
                        if row:
                            schema_fields.update(json.loads(row[0]))

                    # Thêm các cột mới nếu cần
                    new_fields = [k for k in data.keys() if k not in existing_columns and k not in ["id", "timestamp"]]
                    for field in new_fields:
                        dtype = "TEXT"
                        try:
                            await conn.execute(f'ALTER TABLE "{collection_name}" ADD COLUMN "{field}" {dtype}')
                            schema_fields[field] = dtype
                            self.logger.debug(f"Thêm cột {field} ({dtype}) vào bảng {collection_name}")
                        except aiosqlite.OperationalError as e:
                            self.logger.error(f"Lỗi khi thêm cột {field} vào {collection_name}: {str(e)}")
                            return {"error": f"Lỗi khi thêm cột {field}: {str(e)}"}

                    # Cập nhật lược đồ
                    await conn.execute(
                        "INSERT OR REPLACE INTO collection_schemas (id, collection_name, fields, timestamp) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            hashlib.sha256(collection_name.encode()).hexdigest(),
                            collection_name,
                            json.dumps(schema_fields, ensure_ascii=False),
                            int(time.time())
                        )
                    )

                    # Chuẩn bị dữ liệu để cập nhật
                    current_time = int(time.time())
                    data["timestamp"] = current_time
                    columns = [k for k in data.keys() if k in schema_fields]
                    set_clause = ", ".join([f'"{k}" = ?' for k in columns])
                    values = [data[k] for k in columns] + [record_id]

                    # Cập nhật bản ghi
                    await conn.execute(
                        f'UPDATE "{collection_name}" SET {set_clause} WHERE id = ? AND created_by = ?',
                        values + [username]
                    )

                    # Ghi log đồng bộ
                    await conn.execute(
                        "INSERT INTO sync_log (id, table_name, record_id, action, timestamp, details) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            str(uuid.uuid4()),
                            collection_name,
                            record_id,
                            "UPDATE",
                            current_time,
                            json.dumps({"username": username, "action": "update_record"}, ensure_ascii=False)
                        )
                    )
                    await conn.commit()
                    self.logger.info(f"{username}: Đã cập nhật bản ghi {record_id} trong {collection_name}")
                    return {"success": f"Đã cập nhật bản ghi {record_id} trong {collection_name}", "message": "Cập nhật thành công"}

        except asyncio.TimeoutError as e:
            self.logger.error(f"{username}: Timeout khi cập nhật bản ghi {record_id} trong {collection_name}: {str(e)}")
            return {"error": f"Timeout khi cập nhật bản ghi: {str(e)}"}
        except Exception as e:
            self.logger.error(f"{username}: Lỗi cập nhật bản ghi {record_id} trong {collection_name}: {str(e)}")
            return {"error": f"Lỗi cập nhật bản ghi: {str(e)}"}

    async def delete_record(self, collection_name: str, record_id: str, username: str) -> Dict:
        """Xóa một bản ghi từ collection (bảng) được chỉ định, chỉ cho created_by."""
        try:
            async with asyncio.timeout(60):  # Timeout 1 phút
                # Kiểm tra collection được bảo vệ
                if collection_name in self.protected_collections:
                    self.logger.error(f"{username}: Không thể xóa bản ghi trong collection được bảo vệ: {collection_name}")
                    return {"error": f"Collection {collection_name} là collection được bảo vệ"}

                # Kiểm tra tên collection hợp lệ
                if not validate_name(collection_name):
                    return {"error": "Tên collection không hợp lệ"}

                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                    # Kiểm tra xem bảng có tồn tại
                    async with conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
                        (collection_name,)
                    ) as cursor:
                        if not await cursor.fetchone():
                            return {"error": f"Collection {collection_name} không tồn tại"}

                    # Kiểm tra xem bản ghi có tồn tại và thuộc user
                    async with conn.execute(
                        f'SELECT id FROM "{collection_name}" WHERE id = ? AND created_by = ?',
                        (record_id, username)
                    ) as cursor:
                        if not await cursor.fetchone():
                            self.logger.error(f"{username}: Bản ghi {record_id} không tồn tại hoặc không thuộc user trong {collection_name}")
                            return {"error": f"Bản ghi {record_id} không tồn tại hoặc không thuộc {username} trong {collection_name}"}

                    # Xóa bản ghi
                    await conn.execute(
                        f'DELETE FROM "{collection_name}" WHERE id = ? AND created_by = ?',
                        (record_id, username)
                    )

                    # Ghi log đồng bộ
                    current_time = int(time.time())
                    await conn.execute(
                        "INSERT INTO sync_log (id, table_name, record_id, action, timestamp, details) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            str(uuid.uuid4()),
                            collection_name,
                            record_id,
                            "DELETE",
                            current_time,
                            json.dumps({"username": username, "action": "delete_record"}, ensure_ascii=False)
                        )
                    )
                    await conn.commit()
                    self.logger.info(f"{username}: Đã xóa bản ghi {record_id} trong {collection_name}")
                    return {"success": f"Đã xóa bản ghi {record_id} trong {collection_name}"}

        except asyncio.TimeoutError as e:
            self.logger.error(f"{username}: Timeout khi xóa bản ghi {record_id} trong {collection_name}: {str(e)}")
            return {"error": f"Timeout khi xóa bản ghi: {str(e)}"}
        except Exception as e:
            self.logger.error(f"{username}: Lỗi xóa bản ghi {record_id} trong {collection_name}: {str(e)}")
            return {"error": f"Lỗi xóa bản ghi: {str(e)}"}
    
    async def authenticate_user(self, username: str, password: str, bot_password: Optional[str] = None) -> Dict:
        """Authenticate a user and manage session/client state."""
        try:
            async with asyncio.timeout(60):  # 1-minute timeout
                # Validate username and password
                if not validate_name(username):
                    return {"error": "Tên người dùng không hợp lệ"}
                if not validate_password_strength(password):
                    return {"error": "Mật khẩu không đáp ứng yêu cầu bảo mật"}

                # Connect to SQLite database
                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                    # Fetch user data
                    async with conn.execute(
                        "SELECT id, password, bot_password, role FROM users WHERE username = ?",
                        (username,)
                    ) as cursor:
                        row = await cursor.fetchone()
                        if not row:
                            return {"error": "Người dùng không tồn tại"}
                        user_id, stored_password, stored_bot_password, role = row

                        # Verify passwords
                        if not self.pwd_context.verify(password, stored_password):
                            return {"error": "Mật khẩu không đúng"}
                        if bot_password and stored_bot_password and not self.pwd_context.verify(
                            bot_password, stored_bot_password
                        ):
                            return {"error": "Mật khẩu bot không đúng"}

                    current_time = int(time.time())

                    # Check for existing valid session
                    async with conn.execute(
                        "SELECT session_token, expires_at FROM sessions WHERE username = ? AND expires_at > ?",
                        (username, current_time)
                    ) as cursor:
                        existing_session = await cursor.fetchone()
                        if existing_session:
                            session_token = existing_session[0]
                            self.logger.info(f"Tái sử dụng session hiện có cho {username}: {session_token}")
                            state = {
                                "username": username,
                                "session_token": session_token,
                                "authenticated": True,
                                "login_attempts": 0,
                                "selected_tab": "Chat",
                                "timestamp": current_time,
                                "role": role
                            }
                            state = self.sanitize_state(state)
                            state_id = hashlib.sha256(f"{username}_{session_token}".encode()).hexdigest()
                            state_json = json.dumps(state, ensure_ascii=False)

                            # Update client state and session
                            await conn.execute(
                                "INSERT OR REPLACE INTO client_states (id, username, session_token, state, timestamp) "
                                "VALUES (?, ?, ?, ?, ?)",
                                (state_id, username, session_token, state_json, current_time)
                            )
                            await conn.execute(
                                "UPDATE sessions SET timestamp = ? WHERE session_token = ?",
                                (current_time, session_token)
                            )
                            await conn.execute(
                                "INSERT INTO sync_log (id, table_name, record_id, action, timestamp, details) "
                                "VALUES (?, ?, ?, ?, ?, ?)",
                                (
                                    str(uuid.uuid4()),
                                    "sessions",
                                    user_id,
                                    "UPDATE",
                                    current_time,
                                    json.dumps({"username": username, "action": "update_session"})
                                )
                            )
                            await conn.execute(
                                "INSERT INTO sync_log (id, table_name, record_id, action, timestamp, details) "
                                "VALUES (?, ?, ?, ?, ?, ?)",
                                (
                                    str(uuid.uuid4()),
                                    "client_states",
                                    state_id,
                                    "UPDATE",
                                    current_time,
                                    json.dumps({"username": username, "action": "update_client_state"})
                                )
                            )
                            await conn.commit()
                            self.logger.info(f"Đã cập nhật session và client_state cho {username}, session_token={session_token}")
                            return {
                                "success": "Đăng nhập thành công",
                                "session_token": session_token,
                                "role": role
                            }

                    # Delete expired sessions and client states (except for admin)
                    if username != Config.ADMIN_USERNAME:
                        await conn.execute(
                            "DELETE FROM sessions WHERE username = ? AND expires_at <= ?",
                            (username, current_time)
                        )
                        await conn.execute(
                            "DELETE FROM client_states WHERE username = ?",
                            (username,)
                        )

                    # Create new session
                    session_token = hashlib.sha256(f"{username}_{current_time}".encode()).hexdigest()
                    expires_at = current_time + Config.SESSION_MAX_AGE
                    await conn.execute(
                        "INSERT INTO sessions (id, username, session_token, created_at, expires_at, timestamp) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            hashlib.sha256(session_token.encode()).hexdigest(),
                            username,
                            session_token,
                            current_time,
                            expires_at,
                            current_time
                        )
                    )

                    # Create new client state
                    state = {
                        "username": username,
                        "session_token": session_token,
                        "authenticated": True,
                        "login_attempts": 0,
                        "selected_tab": "Chat",
                        "timestamp": current_time,
                        "role": role
                    }
                    state = self.sanitize_state(state)
                    state_id = hashlib.sha256(f"{username}_{session_token}".encode()).hexdigest()
                    state_json = json.dumps(state, ensure_ascii=False)
                    await conn.execute(
                        "INSERT INTO client_states (id, username, session_token, state, timestamp) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (state_id, username, session_token, state_json, current_time)
                    )

                    # Log session and client state creation
                    await conn.execute(
                        "INSERT INTO sync_log (id, table_name, record_id, action, timestamp, details) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            str(uuid.uuid4()),
                            "sessions",
                            user_id,
                            "INSERT",
                            current_time,
                            json.dumps({"username": username, "action": "create_session"})
                        )
                    )
                    await conn.execute(
                        "INSERT INTO sync_log (id, table_name, record_id, action, timestamp, details) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            str(uuid.uuid4()),
                            "client_states",
                            state_id,
                            "INSERT",
                            current_time,
                            json.dumps({"username": username, "action": "create_client_state"})
                        )
                    )
                    await conn.commit()
                    self.logger.info(f"Xác thực thành công, tạo session mới cho {username}: {session_token}")
                    return {
                        "success": "Đăng nhập thành công",
                        "session_token": session_token,
                        "role": role
                    }

        except json.JSONDecodeError as e:
            self.logger.error(f"Lỗi JSON khi xác thực cho {username}: {str(e)}")
            return {"error": f"Lỗi JSON: {str(e)}"}
        except asyncio.TimeoutError as e:
            self.logger.error(f"Timeout khi xác thực {username}: {str(e)}")
            return {"error": f"Timeout khi xác thực: {str(e)}"}
        except Exception as e:
            self.logger.error(f"Lỗi xác thực cho {username}: {str(e)}")
            return {"error": f"Lỗi xác thực: {str(e)}"}
            
 
    
    async def search_collections(self, query: str, username: str, page: int, page_size: int, collection: str = None) -> Dict:
        """Tìm kiếm Q&A trong bảng được chỉ định dựa trên từ khóa trong question hoặc answer."""
        try:
            async with asyncio.timeout(60):  # Timeout 1 phút
                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                    # Nếu không chỉ định collection, lấy tất cả bảng không được bảo vệ
                    tables = [collection] if collection else [
                        row[0] async for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                        if row[0] not in self.protected_collections
                    ]
                    results = []
                    query = query.lower().strip()
                    for table in tables:
                        # Kiểm tra bảng có tồn tại và có các cột question, answer
                        async with conn.execute(f'PRAGMA table_info("{table}")') as cursor:
                            columns = [row[1] async for row in cursor]
                        if "question" not in columns or "answer" not in columns:
                            continue
                        # Tìm kiếm trong question và answer
                        async with conn.execute(
                            f'SELECT * FROM "{table}" WHERE lower(question) LIKE ? OR lower(answer) LIKE ? LIMIT ? OFFSET ?',
                            (f'%{query}%', f'%{query}%', page_size, (page - 1) * page_size)
                        ) as cursor:
                            rows = await cursor.fetchall()
                            results.extend([{columns[i]: row[i] for i in range(len(columns))} for row in rows])
                    total = len(results)
                    self.logger.info(f"{username}: Tìm kiếm Q&A với query '{query}' trong {collection or 'tất cả bảng'}, tìm thấy {total} bản ghi")
                    return {
                        "success": "Tìm kiếm thành công",
                        "results": results,
                        "total": total,
                        "page": page,
                        "page_size": page_size
                    }
        except asyncio.TimeoutError as e:
            self.logger.error(f"{username}: Timeout khi tìm kiếm Q&A: {str(e)}")
            return {"error": f"Timeout khi tìm kiếm: {str(e)}"}
        except Exception as e:
            self.logger.error(f"{username}: Lỗi tìm kiếm Q&A: {str(e)}")
            return {"error": f"Lỗi tìm kiếm: {str(e)}"}
    
    async def list_collections(self, username: str) -> Dict:
        """Trả về danh sách tất cả collection từ collection_schemas."""
        try:
            async with asyncio.timeout(60):  # Timeout 1 phút
                # Kiểm tra tệp cơ sở dữ liệu
                if not os.path.exists(Config.SQLITE_DB_PATH):
                    self.logger.error(f"Database file {Config.SQLITE_DB_PATH} does not exist")
                    return {"error": "Database file not found", "collections": [], "total": 0}

                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                    # Kiểm tra bảng collection_schemas
                    async with conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='collection_schemas'"
                    ) as cursor:
                        if not await cursor.fetchone():
                            self.logger.error("Table collection_schemas does not exist")
                            return {"error": "Table collection_schemas not found", "collections": [], "total": 0}

                    # Lấy danh sách collection
                    async with conn.execute("SELECT collection_name FROM collection_schemas") as cursor:
                        rows = await cursor.fetchall()
                        if not rows:
                            return {"success": True, "collections": [], "total": 0}

                        protected = getattr(self, 'protected_collections', set())
                        collections = [row[0] for row in rows if row[0] and row[0] not in protected]
                        self.logger.info(f"{username}: Liệt kê {len(collections)} collection")
                        return {
                            "success": True,
                            "collections": collections,
                            "total": len(collections)
                        }

        except asyncio.TimeoutError as e:
            self.logger.error(f"{username}: Timeout khi liệt kê collection: {str(e)}")
            return {"error": f"Timeout khi liệt kê collection: {str(e)}", "collections": [], "total": 0}
        except aiosqlite.Error as e:
            self.logger.error(f"{username}: Lỗi cơ sở dữ liệu: {str(e)}")
            return {"error": f"Lỗi cơ sở dữ liệu: {str(e)}", "collections": [], "total": 0}
        except AttributeError as e:
            self.logger.error(f"{username}: Cấu hình không hợp lệ: {str(e)}")
            return {"error": "Cấu hình không hợp lệ", "collections": [], "total": 0}
        except Exception as e:
            self.logger.error(f"{username}: Lỗi không xác định: {str(e)}")
            return {"error": f"Lỗi không xác định: {str(e)}", "collections": [], "total": 0}
            



    async def create_record(self, collection_name: str, data: Dict, username: str) -> Dict:
        """Tạo một bản ghi mới trong collection (bảng) được chỉ định."""
        try:
            async with asyncio.timeout(30):  # Timeout 30 giây
                # Kiểm tra collection được bảo vệ
                if collection_name in self.protected_collections:
                    self.logger.error(
                        f"{username}: Không thể tạo bản ghi trong collection được bảo vệ: {collection_name}"
                    )
                    return {"error": f"Collection {collection_name} là collection được bảo vệ"}

                # Kiểm tra tên collection hợp lệ
                if not validate_name(collection_name):
                    self.logger.error(f"{username}: Tên collection không hợp lệ: {collection_name}")
                    return {"error": "Tên collection không hợp lệ, chỉ chấp nhận chữ, số, dấu gạch dưới"}

                # Kiểm tra kích thước dữ liệu
                data_json = json.dumps(data, ensure_ascii=False)
                if len(data_json.encode()) > 1_000_000:
                    self.logger.error(f"{username}: Dữ liệu quá lớn (vượt quá 1MB) trong {collection_name}")
                    return {"error": "Dữ liệu quá lớn (vượt quá 1MB)"}

                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                    await conn.execute("PRAGMA busy_timeout = 30000")

                    # Kiểm tra và tạo bảng nếu chưa tồn tại
                    async with conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
                        (collection_name,),
                    ) as cursor:
                        if not await cursor.fetchone():
                            fields = {
                                "id": "TEXT PRIMARY KEY",
                                "content": "TEXT",
                                "role": "TEXT",
                                "type": "TEXT",
                                "timestamp": "INTEGER"
                            }
                            columns = ", ".join([f'"{k}" {v}' for k, v in fields.items()])
                            await conn.execute(f'CREATE TABLE "{collection_name}" ({columns})')
                            await conn.execute(
                                "INSERT INTO collection_schemas (id, collection_name, fields, timestamp) "
                                "VALUES (?, ?, ?, ?)",
                                (
                                    hashlib.sha256(collection_name.encode()).hexdigest(),
                                    collection_name,
                                    json.dumps(fields, ensure_ascii=False),
                                    int(time.time()),
                                ),
                            )
                            await conn.execute(f'CREATE INDEX IF NOT EXISTS idx_timestamp ON "{collection_name}" (timestamp)')
                            self.logger.info(f"{username}: Tạo bảng mới: {collection_name}")

                    # Chuẩn bị dữ liệu
                    record_id = data.get("id", str(uuid.uuid4()))  # Ưu tiên id từ data, nếu không thì tạo mới
                    current_time = int(time.time())
                    data = data.copy()
                    data["id"] = record_id
                    data["timestamp"] = current_time
                    columns = ", ".join([f'"{k}"' for k in data.keys()])
                    placeholders = ", ".join(["?" for _ in data])
                    values = list(data.values())

                    # Chèn bản ghi với INSERT OR REPLACE
                    await conn.execute(
                        f'INSERT OR REPLACE INTO "{collection_name}" ({columns}) VALUES ({placeholders})',
                        values,
                    )

                    # Ghi log đồng bộ
                    await conn.execute(
                        "INSERT INTO sync_log (id, table_name, record_id, action, timestamp, details) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            str(uuid.uuid4()),
                            collection_name,
                            record_id,
                            "INSERT",
                            current_time,
                            json.dumps({"username": username, "action": "create_record"}, ensure_ascii=False),
                        ),
                    )
                    await conn.commit()
                    self.logger.info(f"{username}: Đã tạo bản ghi {record_id} trong {collection_name}")
                    return {"success": f"Đã tạo bản ghi trong {collection_name}", "id": record_id}

        except asyncio.TimeoutError as e:
            self.logger.error(f"{username}: Timeout khi tạo bản ghi trong {collection_name}: {str(e)}")
            return {"error": f"Timeout khi tạo bản ghi: {str(e)}"}
        except aiosqlite.OperationalError as e:
            self.logger.error(f"{username}: Lỗi cơ sở dữ liệu khi tạo bản ghi trong {collection_name}: {str(e)}")
            return {"error": f"Lỗi cơ sở dữ liệu: {str(e)}"}
        except Exception as e:
            self.logger.error(f"{username}: Lỗi tạo bản ghi trong {collection_name}: {str(e)}")
            return {"error": f"Lỗi tạo bản ghi: {str(e)}"}

    
    async def start(self):
        if not self.running:
            self.running = True
            asyncio.create_task(self._worker())

    async def _worker(self):
        while self.running:
            try:
                coro, future = await self.write_queue.get()
                try:
                    result = await coro()
                    if not future.done():
                        future.set_result(result)
                except Exception as e:
                    if not future.done():
                        future.set_exception(e)
                    self.logger.error(f"Lỗi trong worker: {e}")
                finally:
                    self.write_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Lỗi ngoài worker: {e}")

    async def enqueue_write(self, coro: Callable[[], Any], timeout: int = 120) -> Any:
        future = asyncio.get_running_loop().create_future()
        await self.write_queue.put((coro, future))
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            self.logger.error("Timeout khi chờ kết quả từ worker")
            raise DatabaseError("Hết thời gian khi chờ kết quả từ worker")

    async def stop(self):
        self.logger.debug("Dừng worker hàng đợi")
        self.running = False
        while not self.write_queue.empty():
            try:
                await self.write_queue.get()
                self.write_queue.task_done()
            except:
                pass
    
class FirestoreHandler:
    """Xử lý các thao tác Firestore."""
    
    def __init__(self, logger, core):
        self.logger = logger
        self.core = core
        self.db: Optional[AsyncClient] = None
        self.firestore_available = False
        self._initialize_firestore()

    def _serialize_value(self, value):
        """Serialize giá trị thành kiểu dữ liệu SQLite hỗ trợ."""
        if isinstance(value, dict):
            try:
                return json.dumps(value, ensure_ascii=False)
            except (TypeError, ValueError) as e:
                self.logger.error(f"Lỗi serialize giá trị: {value}, lỗi: {str(e)}")
                return json.dumps({}, ensure_ascii=False)
        elif isinstance(value, (str, int, float, type(None))):
            return value
        else:
            self.logger.warning(f"Kiểu dữ liệu không hỗ trợ: {type(value)}, chuyển thành str: {value}")
            return str(value)

    def _deserialize_schema(self, fields):
        """Deserialize chuỗi JSON thành dict, với fallback an toàn."""
        if isinstance(fields, str):
            try:
                fields = json.loads(fields)
                if not isinstance(fields, dict):
                    self.logger.error(f"Schema không phải dict sau khi deserialize: {fields}")
                    return {"id": "TEXT", "timestamp": "INTEGER"}
                return fields
            except json.JSONDecodeError as e:
                self.logger.error(f"Lỗi deserialize JSON: {fields}, lỗi: {str(e)}")
                return {"id": "TEXT", "timestamp": "INTEGER"}
        elif isinstance(fields, dict):
            return fields
        self.logger.error(f"Schema không hợp lệ: {fields}")
        return {"id": "TEXT", "timestamp": "INTEGER"}

    def validate_schema_compatibility(self, local_schema: Dict, firestore_schema: Dict) -> Dict:
        """Kiểm tra và giải quyết xung đột kiểu dữ liệu giữa SQLite và Firestore."""
        merged_schema = {}
        for field, dtype in firestore_schema.items():
            clean_field = sanitize_field_name(field)
            if clean_field != field:
                self.logger.warning(f"Đổi tên trường {field} thành {clean_field}")
            merged_schema[clean_field] = dtype
        for field, dtype in local_schema.items():
            clean_field = sanitize_field_name(field)
            if clean_field not in merged_schema:
                merged_schema[clean_field] = dtype
            elif merged_schema[clean_field] != dtype:
                self.logger.warning(f"Xung đột kiểu dữ liệu cho {clean_field}: SQLite={dtype}, Firestore={merged_schema[clean_field]}")
                merged_schema[clean_field] = "TEXT"
        return merged_schema

    def _check_parameters(self, params, query):
        """Kiểm tra các tham số trước khi execute SQL."""
        for i, param in enumerate(params):
            if not isinstance(param, (str, int, float, type(None))):
                self.logger.error(f"Tham số {i} trong query '{query}' có kiểu không hỗ trợ: {type(param)}, giá trị: {param}")
                raise ValueError(f"Tham số {i} không hợp lệ: {type(param)}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(Exception)
    )
    def _initialize_firestore(self):
        if AsyncClient is None or Credentials is None:
            self.logger.warning("Thư viện Google Cloud Firestore không được cài đặt, Firestore không khả dụng")
            return
        
        try:
            if hasattr(Config, "FIRESTORE_CREDENTIALS") and Config.FIRESTORE_CREDENTIALS:
                if isinstance(Config.FIRESTORE_CREDENTIALS, str):
                    cred_dict = json.loads(Config.FIRESTORE_CREDENTIALS)
                else:
                    cred_dict = Config.FIRESTORE_CREDENTIALS
                
                if "project_id" not in cred_dict:
                    self.logger.error("Missing project_id in FIRESTORE_CREDENTIALS")
                    return
                
                credentials = Credentials.from_service_account_info(cred_dict)
                self.db = AsyncClient(credentials=credentials, project=cred_dict.get("project_id"))
                self.firestore_available = True
                self.logger.info("Firestore đã khởi tạo thành công")
            else:
                self.logger.error("FIRESTORE_CREDENTIALS không được cấu hình")
                self.firestore_available = False
                self.db = None
        except Exception as e:
            self.logger.error(f"Lỗi khởi tạo Firestore: {str(e)}", exc_info=True)
            self.firestore_available = False
            self.db = None

    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(Exception)
    )
    def _initialize_firestore(self):
        if AsyncClient is None or Credentials is None:
            self.logger.warning("Thư viện Google Cloud Firestore không được cài đặt, Firestore không khả dụng")
            return
        
        try:
            if hasattr(Config, "FIRESTORE_CREDENTIALS") and Config.FIRESTORE_CREDENTIALS:
                if isinstance(Config.FIRESTORE_CREDENTIALS, str):
                    cred_dict = json.loads(Config.FIRESTORE_CREDENTIALS)
                else:
                    cred_dict = Config.FIRESTORE_CREDENTIALS
                
                if "project_id" not in cred_dict:
                    self.logger.error("Missing project_id in FIRESTORE_CREDENTIALS")
                    return
                
                credentials = Credentials.from_service_account_info(cred_dict)
                self.db = AsyncClient(credentials=credentials, project=cred_dict.get("project_id"))
                self.firestore_available = True
                self.logger.info("Firestore đã khởi tạo thành công")
            else:
                self.logger.error("FIRESTORE_CREDENTIALS không được cấu hình")
                self.firestore_available = False
                self.db = None
        except Exception as e:
            self.logger.error(f"Lỗi khởi tạo Firestore: {str(e)}", exc_info=True)
            self.firestore_available = False
            self.db = None

    async def sync_firestore_batch(self, batch, username: str) -> int:
        """Đồng bộ một batch bản ghi lên Firestore."""
        count = 0
        for collection_id, doc_id, doc_data in batch:
            doc_ref = self.db.collection(collection_id).document(doc_id)
            existing_doc = await doc_ref.get()
            if existing_doc.exists and existing_doc.to_dict() == doc_data:
                continue
            await doc_ref.set(doc_data, merge=True)
            self.logger.debug(f"{username}: Đồng bộ bản ghi {doc_id} trong {collection_id}")
            count += 1
        return count

    

    
    async def sync_to_sqlite(
        self,
        username: str,
        progress_callback: Optional[Callable[[float], None]] = None,
        protected_only: bool = False,
        specific_collections: Optional[List[str]] = None,
        batch_size: int = 100
    ) -> Dict:
        if not await self.core.sqlite_handler.has_permission(username, "sync_data"):
            self.logger.error(f"{username}: Không có quyền đồng bộ dữ liệu")
            return {"error": "Không có quyền đồng bộ dữ liệu", "synced_records": 0}

        try:
            if not self.firestore_available or not self.db:
                self.logger.error(f"{username}: Firestore không khả dụng")
                return {"error": "Firestore không khả dụng", "synced_records": 0}

            check_disk_space()
            async with asyncio.timeout(300):  # Timeout 5 phút
                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                    async with conn.execute(
                        "SELECT MAX(timestamp) FROM sync_log WHERE action = 'sync_to_sqlite'"
                    ) as cursor:
                        last_sync = (await cursor.fetchone())[0] or 0
                        self.logger.info(f"{username}: Thời gian đồng bộ xuống SQLite cuối cùng: {last_sync}")

                    async with conn.execute("SELECT collection_name, fields FROM collection_schemas") as cursor:
                        local_schemas = {
                            row[0]: self._deserialize_schema(row[1])
                            for row in await cursor.fetchall()
                        }

                    async def get_firestore_schemas():
                        schemas = {}
                        async for doc in self.db.collection("collection_schemas").stream():
                            doc_data = doc.to_dict()
                            if isinstance(doc_data, dict) and "collection_name" in doc_data and "fields" in doc_data:
                                schemas[doc_data["collection_name"]] = self._deserialize_schema(doc_data["fields"])
                        return schemas

                    schemas = await retry_firestore_operation(get_firestore_schemas)

                    collections = [coll.id async for coll in self.db.collections()]
                    if specific_collections:
                        collections = [coll for coll in collections if coll in specific_collections]
                    elif protected_only:
                        collections = [
                            coll for coll in collections
                            if coll in Config.PROTECTED_TABLES or coll in Config.SPECIAL_TABLES
                        ]
                    else:
                        collections = [coll for coll in collections if coll not in Config.PROTECTED_TABLES]
                    collections = list(set(collections) | set(Config.SPECIAL_TABLES))
                    collections = [coll for coll in collections if coll not in Config.SYSTEM_TABLES]
                    self.logger.info(f"{username}: Đồng bộ các collection: {collections}")

                    if not collections:
                        self.logger.info(f"{username}: Không có collection nào để đồng bộ")
                        if progress_callback:
                            await progress_callback(1.0)  # Hoàn thành ngay nếu không có dữ liệu
                        return {"success": "Không có collection để đồng bộ", "synced_records": 0}

                    total_collections = len(collections)
                    synced_records = 0
                    processed_records = 0
                    total_records = 0

                    # Ước tính tổng số bản ghi để tính tiến trình chính xác hơn
                    for collection_name in collections:
                        query = self.db.collection(collection_name)
                        async for _ in query.stream():
                            total_records += 1

                    for collection_name in collections:
                        if not validate_name(collection_name):
                            self.logger.warning(f"{username}: Tên collection {collection_name} không hợp lệ, bỏ qua")
                            continue

                        self.logger.debug(f"{username}: Đồng bộ collection {collection_name}")
                        firestore_schema = schemas.get(collection_name, {"id": "TEXT", "timestamp": "INTEGER"})
                        local_schema = local_schemas.get(collection_name, {"id": "TEXT", "timestamp": "INTEGER"})
                        merged_schema = self.validate_schema_compatibility(local_schema, firestore_schema)

                        async with conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", 
                            (collection_name,)
                        ) as cursor:
                            exists = await cursor.fetchone()
                        if not exists:
                            columns_def = [f'"{field}" {dtype}' for field, dtype in merged_schema.items()]
                            await conn.execute(f'CREATE TABLE "{collection_name}" ({", ".join(columns_def)})')
                            self.logger.info(f"{username}: Tạo bảng {collection_name} với schema: {merged_schema}")
                        else:
                            async with conn.execute(f'PRAGMA table_info("{collection_name}")') as cursor:
                                existing_columns = {row[1]: row[2] for row in await cursor.fetchall()}
                            for field, dtype in merged_schema.items():
                                if field not in existing_columns:
                                    await conn.execute(f'ALTER TABLE "{collection_name}" ADD COLUMN "{field}" {dtype}')
                                    self.logger.debug(f"{username}: Thêm cột {field} ({dtype}) vào {collection_name}")

                        schema_json = self._serialize_value(merged_schema)
                        query = (
                            "INSERT OR REPLACE INTO collection_schemas "
                            "(id, collection_name, fields, timestamp) VALUES (?, ?, ?, ?)"
                        )
                        params = (
                            hashlib.sha256(collection_name.encode()).hexdigest(),
                            collection_name,
                            schema_json,
                            int(time.time())
                        )
                        self._check_parameters(params, query)
                        await conn.execute(query, params)

                        async def update_firestore_schema():
                            await self.db.collection("collection_schemas").document(
                                hashlib.sha256(collection_name.encode()).hexdigest()
                            ).set({
                                "collection_name": collection_name,
                                "fields": merged_schema,
                                "timestamp": int(time.time())
                            }, merge=True)
                            return {"success": "Cập nhật schema Firestore thành công"}

                        await retry_firestore_operation(update_firestore_schema)

                        async with conn.execute(f'SELECT COUNT(*) FROM "{collection_name}"') as cursor:
                            row_count = (await cursor.fetchone())[0]
                        is_empty = row_count == 0
                        self.logger.debug(f"{username}: Bảng {collection_name} {'rỗng' if is_empty else f'có {row_count} bản ghi'}")

                        async def fetch_firestore_records():
                            batch = []
                            query = self.db.collection(collection_name)
                            if is_empty or collection_name in Config.SPECIAL_TABLES or collection_name in Config.PROTECTED_TABLES:
                                self.logger.debug(f"{username}: Đồng bộ toàn bộ bản ghi cho {collection_name} (bảng rỗng hoặc đặc biệt)")
                            else:
                                query = query.where(filter=FieldFilter("timestamp", ">", last_sync))
                                self.logger.debug(f"{username}: Đồng bộ bản ghi mới cho {collection_name} (timestamp > {last_sync})")
                            async for doc in query.stream():
                                doc_data = doc.to_dict()
                                if not isinstance(doc_data, dict):
                                    self.logger.warning(f"{username}: Dữ liệu Firestore không hợp lệ cho {collection_name}, bỏ qua")
                                    continue
                                doc_data["id"] = doc.id
                                doc_data["timestamp"] = doc_data.get("timestamp", int(time.time()))
                                valid_fields = [k for k in doc_data if k in merged_schema]
                                if not valid_fields:
                                    self.logger.warning(f"{username}: Không có trường hợp lệ để đồng bộ cho bản ghi {doc_data['id']} trong {collection_name}")
                                    continue
                                columns = [f'"{sanitize_field_name(k)}"' for k in valid_fields]
                                placeholders = ["?" for _ in valid_fields]
                                values = [self._serialize_value(doc_data[k]) for k in valid_fields]
                                batch.append((columns, placeholders, values, doc_data["id"]))
                            return batch

                        batch = await retry_firestore_operation(fetch_firestore_records)
                        if not isinstance(batch, list):
                            self.logger.error(f"{username}: Kết quả từ fetch_firestore_records không phải danh sách: {batch}")
                            continue

                        if batch:
                            for batch_item in batch:
                                if not isinstance(batch_item, tuple) or len(batch_item) != 4:
                                    self.logger.error(f"{username}: Batch item không hợp lệ cho {collection_name}: {batch_item}")
                                    continue
                                columns, placeholders, values, record_id = batch_item
                                try:
                                    await conn.execute(
                                        f'INSERT OR REPLACE INTO "{collection_name}" ({", ".join(columns)}) VALUES ({", ".join(placeholders)})',
                                        values
                                    )
                                    synced_records += 1
                                    processed_records += 1
                                    query = (
                                        "INSERT INTO sync_log (id, table_name, record_id, action, timestamp, details) "
                                        "VALUES (?, ?, ?, ?, ?, ?)"
                                    )
                                    params = (
                                        str(uuid.uuid4()),
                                        collection_name,
                                        record_id,
                                        "sync_to_sqlite",
                                        int(time.time()),
                                        self._serialize_value({"username": username, "record_id": record_id})
                                    )
                                    self._check_parameters(params, query)
                                    await conn.execute(query, params)
                                    # Cập nhật tiến trình sau mỗi bản ghi
                                    if progress_callback and total_records > 0:
                                        await progress_callback(processed_records / total_records)
                                except Exception as e:
                                    self.logger.error(f"{username}: Lỗi khi chèn bản ghi {record_id} vào {collection_name}: {str(e)}")
                                    continue
                            await conn.commit()
                            self.logger.debug(f"{username}: Đồng bộ {len(batch)} bản ghi trong {collection_name}")
                        else:
                            self.logger.debug(f"{username}: Không có bản ghi mới để đồng bộ trong {collection_name}")

                        if progress_callback and total_records > 0:
                            await progress_callback(processed_records / total_records)

                    if synced_records > 0:
                        query = (
                            "INSERT INTO sync_log (id, table_name, record_id, action, timestamp, details) "
                            "VALUES (?, ?, ?, ?, ?, ?)"
                        )
                        params = (
                            str(uuid.uuid4()),
                            "all_tables",
                            None,
                            "sync_to_sqlite",
                            int(time.time()),
                            self._serialize_value({
                                "username": username,
                                "synced_records": synced_records,
                                "collections": collections
                            })
                        )
                        self._check_parameters(params, query)
                        await conn.execute(query, params)
                        await conn.commit()
                        self.logger.info(f"{username}: Ghi log đồng bộ cho {synced_records} bản ghi")

                    self.logger.info(f"{username}: Đồng bộ {synced_records} bản ghi từ Firestore sang SQLite")
                    if progress_callback:
                        await progress_callback(1.0)  # Đảm bảo tiến trình đạt 100% khi hoàn thành
                    return {
                        "success": f"Đồng bộ {synced_records} bản ghi từ Firestore sang SQLite",
                        "synced_records": synced_records
                    }

        except asyncio.TimeoutError as e:
            self.logger.error(f"{username}: Timeout trong sync_to_sqlite: {str(e)}")
            if progress_callback:
                await progress_callback(1.0)  # Đảm bảo tiến trình đạt 100% khi lỗi
            return {"error": f"Timeout khi đồng bộ: {str(e)}", "synced_records": 0}
        except Exception as e:
            self.logger.error(f"{username}: Lỗi đồng bộ từ Firestore sang SQLite: {str(e)}")
            if progress_callback:
                await progress_callback(1.0)  # Đảm bảo tiến trình đạt 100% khi lỗi
            return {"error": f"Lỗi đồng bộ: {str(e)}", "synced_records": 0}
    
    
    
    async def sync_from_sqlite(
        self,
        username: str,
        batch_size: int = 100,
        progress_callback: Optional[Callable[[float], None]] = None,
        protected_only: bool = False,
        specific_collections: Optional[List[str]] = None,
        record_limit: Optional[int] = None
    ) -> Dict:
        self.logger.debug(f"{username}: Bắt đầu đồng bộ từ SQLite sang Firestore")
        try:
            if not self.firestore_available or not self.db:
                self.logger.error(f"{username}: Firestore không khả dụng")
                if progress_callback:
                    await progress_callback(1.0)
                return {"error": "Firestore không khả dụng", "synced_records": 0}

            check_disk_space()
            async with asyncio.timeout(300):
                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                    async with conn.execute(
                        "SELECT MAX(timestamp) FROM sync_log WHERE action = 'sync_to_firestore'"
                    ) as cursor:
                        last_sync = (await cursor.fetchone())[0] or 0
                        self.logger.info(f"{username}: Thời gian đồng bộ lên Firestore cuối cùng: {last_sync}")

                    async with conn.execute(
                        "SELECT table_name, record_id FROM sync_log WHERE action = 'DELETE' AND timestamp > ?",
                        (last_sync,)
                    ) as cursor:
                        delete_logs = await cursor.fetchall()

                    async def delete_firestore_records():
                        count = 0
                        for table_name, record_id in delete_logs:
                            if specific_collections and table_name not in specific_collections:
                                continue
                            await self.db.collection(table_name).document(record_id).delete()
                            self.logger.debug(f"{username}: Đã xóa {record_id} trong Firestore collection {table_name}")
                            count += 1
                        return count

                    deleted_count = await retry_firestore_operation(delete_firestore_records)
                    self.logger.debug(f"{username}: Đã xóa {deleted_count} bản ghi trên Firestore")

                    async with conn.execute("SELECT collection_name, fields FROM collection_schemas") as cursor:
                        local_schemas = {
                            row[0]: self._deserialize_schema(row[1])
                            for row in await cursor.fetchall()
                        }
                        self.logger.info(f"{username}: Tìm thấy {len(local_schemas)} lược đồ trong SQLite")

                    async def get_firestore_schemas():
                        schemas = {}
                        async for doc in self.db.collection("collection_schemas").stream():
                            doc_data = doc.to_dict()
                            if not isinstance(doc_data, dict) or "collection_name" not in doc_data or "fields" not in doc_data:
                                continue
                            schemas[doc_data["collection_name"]] = self._deserialize_schema(doc_data["fields"])
                        return schemas

                    firestore_schemas = await retry_firestore_operation(get_firestore_schemas)

                    async with conn.execute("SELECT name FROM sqlite_master WHERE type='table'") as cursor:
                        tables = [row[0] for row in await cursor.fetchall() if row[0] not in Config.SYSTEM_TABLES]
                    tables = [t for t in tables if not t.endswith('_fts') and 'fts' not in t.lower()]
                    if specific_collections:
                        tables = [t for t in tables if t in specific_collections]
                    elif protected_only:
                        tables = [
                            t for t in tables
                            if t in Config.PROTECTED_TABLES or t in Config.SPECIAL_TABLES
                        ]
                    else:
                        tables = [t for t in tables if t not in Config.PROTECTED_TABLES]
                    tables = list(set(tables) | set(Config.SPECIAL_TABLES))
                    self.logger.info(f"{username}: Đồng bộ các bảng (excluded FTS): {tables}")

                    if not tables:
                        self.logger.info(f"{username}: Không có bảng nào để đồng bộ")
                        if progress_callback:
                            await progress_callback(1.0)
                        return {"success": "Không có bảng nào để đồng bộ", "synced_records": 0}

                    total_tables = len(tables)
                    synced_records = 0
                    processed_records = 0
                    total_records = 0

                    # Ước tính tổng số bản ghi
                    for table in tables:
                        async with conn.execute(f'SELECT COUNT(*) FROM "{table}"') as cursor:
                            total_records += (await cursor.fetchone())[0]

                    async def fetch_rows(table, last_sync, record_limit, page_size=1000):
                        async with conn.execute(f'SELECT COUNT(*) FROM "{table}"') as cursor:
                            row_count = (await cursor.fetchone())[0]
                        is_empty = row_count == 0
                        self.logger.debug(f"{username}: Bảng {table} {'rỗng' if is_empty else f'có {row_count} bản ghi'}")

                        async with conn.execute(f'PRAGMA table_info("{table}")') as cursor:
                            columns_info = {row[1]: row[2] for row in await cursor.fetchall()}
                        has_timestamp = "timestamp" in columns_info
                        self.logger.debug(f"{username}: Table {table} has timestamp column: {has_timestamp}")

                        offset = 0
                        while True:
                            if is_empty or table in Config.SPECIAL_TABLES or table in Config.PROTECTED_TABLES or not has_timestamp:
                                query = f'SELECT * FROM "{table}" LIMIT ? OFFSET ?'
                                params = [page_size, offset]
                                self.logger.debug(f"{username}: Đồng bộ toàn bộ cho {table} (empty/special/no timestamp)")
                            else:
                                query = f'SELECT * FROM "{table}" WHERE timestamp > ? LIMIT ? OFFSET ?'
                                params = [last_sync, page_size, offset]
                                self.logger.debug(f"{username}: Đồng bộ bản ghi mới cho {table} (timestamp > {last_sync})")

                            if record_limit:
                                params[-2] = min(page_size, record_limit - offset)

                            try:
                                async with conn.execute(query, params) as cursor:
                                    rows = await cursor.fetchall()
                                    if not rows:
                                        break
                                    yield rows
                                    offset += len(rows)
                            except Exception as query_error:
                                self.logger.error(
                                    f"{username}: Lỗi query cho table {table}: {str(query_error)} (query: {query}, params: {params}). Skip table."
                                )
                                break

                            if record_limit and offset >= record_limit:
                                break

                    for table in tables:
                        if not validate_name(table):
                            self.logger.warning(f"{username}: Tên bảng {table} không hợp lệ, bỏ qua")
                            continue

                        self.logger.debug(f"{username}: Đồng bộ bảng {table}")
                        async with conn.execute(f'PRAGMA table_info("{table}")') as cursor:
                            columns = {sanitize_field_name(row[1]): row[2] for row in await cursor.fetchall()}
                        local_schema = local_schemas.get(table, {"id": "TEXT", "timestamp": "INTEGER"})
                        firestore_schema = firestore_schemas.get(table, {})
                        merged_schema = self.validate_schema_compatibility(local_schema, firestore_schema)

                        if merged_schema != local_schema:
                            schema_json = self._serialize_value(merged_schema)
                            query = (
                                "INSERT OR REPLACE INTO collection_schemas "
                                "(id, collection_name, fields, timestamp) VALUES (?, ?, ?, ?)"
                            )
                            params = (
                                hashlib.sha256(table.encode()).hexdigest(),
                                table,
                                schema_json,
                                int(time.time())
                            )
                            self._check_parameters(params, query)
                            await conn.execute(query, params)

                            async def update_firestore_schema():
                                await self.db.collection("collection_schemas").document(
                                    hashlib.sha256(table.encode()).hexdigest()
                                ).set({
                                    "collection_name": table,
                                    "fields": merged_schema,
                                    "timestamp": int(time.time())
                                }, merge=True)
                                return 1

                            updated_count = await retry_firestore_operation(update_firestore_schema)
                            self.logger.debug(f"{username}: Cập nhật {updated_count} schema cho {table}")

                        valid_columns = list(merged_schema.keys())
                        column_list = list(columns.keys())
                        batch = []

                        async for rows in fetch_rows(table, last_sync, record_limit):
                            for row in rows:
                                try:
                                    data = {
                                        col: row[column_list.index(col)]
                                        for col in valid_columns if col in column_list and col != "rowid"
                                    }
                                    data = {k: v for k, v in data.items() if v is not None}
                                    if not data:
                                        continue
                                    doc_id = data.get("id", str(uuid.uuid4()))
                                    if table in Config.SPECIAL_TABLES or table in Config.PROTECTED_TABLES:
                                        key_field = "collection_name" if table == "collection_schemas" else \
                                            "username" if table in Config.SPECIAL_TABLES else "collection_name"
                                        key_value = data.get(key_field)
                                        if not key_value:
                                            continue
                                        doc_id = hashlib.sha256(key_value.encode()).hexdigest()
                                        await retry_firestore_operation(
                                            lambda: self.db.collection(table).document(doc_id).set(data, merge=True)
                                        )
                                        synced_records += 1
                                        processed_records += 1
                                        self.logger.debug(f"{username}: Đồng bộ bản ghi {doc_id} trong {table}")
                                        if progress_callback and total_records > 0:
                                            await progress_callback(processed_records / total_records)
                                        continue
                                    batch.append((table, doc_id, data))
                                    self.logger.debug(
                                        f"{username}: Added to batch: table={table}, doc_id={doc_id}, data_keys={list(data.keys())}"
                                    )
                                except Exception as row_error:
                                    self.logger.error(
                                        f"{username}: Lỗi xử lý row cho table {table}: {str(row_error)} (row: {row}). Skip row."
                                    )
                                    continue

                                if len(batch) >= batch_size:
                                    try:
                                        batch_count = await retry_firestore_operation(
                                            lambda: self.sync_firestore_batch(batch, username)
                                        )
                                        synced_records += batch_count
                                        processed_records += batch_count
                                        self.logger.debug(
                                            f"{username}: Synced batch {len(batch)} for {table}, count={batch_count}"
                                        )
                                        if progress_callback and total_records > 0:
                                            await progress_callback(processed_records / total_records)
                                    except Exception as batch_error:
                                        self.logger.error(
                                            f"{username}: Lỗi sync batch cho {table}: {str(batch_error)}. Skip batch."
                                        )
                                    batch.clear()

                            if batch:
                                try:
                                    batch_count = await retry_firestore_operation(
                                        lambda: self.sync_firestore_batch(batch, username)
                                    )
                                    synced_records += batch_count
                                    processed_records += batch_count
                                    self.logger.debug(
                                        f"{username}: Synced final batch {len(batch)} for {table}, count={batch_count}"
                                    )
                                    if progress_callback and total_records > 0:
                                        await progress_callback(processed_records / total_records)
                                except Exception as batch_error:
                                    self.logger.error(
                                        f"{username}: Lỗi sync final batch cho {table}: {str(batch_error)}"
                                    )
                                batch.clear()

                            if synced_records > 0:
                                await conn.execute(
                                    "DELETE FROM sync_log WHERE table_name = ? AND action IN ('INSERT', 'UPDATE', 'DELETE')",
                                    (table,)
                                )
                                await conn.commit()
                                self.logger.debug(f"{username}: Xóa sync_log cũ cho {table}")

                        if progress_callback and total_records > 0:
                            await progress_callback(processed_records / total_records)

                    if synced_records > 0:
                        query = (
                            "INSERT INTO sync_log (id, table_name, record_id, action, timestamp, details) "
                            "VALUES (?, ?, ?, ?, ?, ?)"
                        )
                        details = {"username": username, "synced_records": synced_records, "tables": tables}
                        details_json = self._serialize_value(details)
                        params = (
                            str(uuid.uuid4()),
                            "ALL_TABLES",
                            None,
                            "sync_to_firestore",
                            int(time.time()),
                            details_json
                        )
                        self._check_parameters(params, query)
                        await conn.execute(query, params)
                        await conn.commit()
                        self.logger.info(f"{username}: Ghi log đồng bộ cho {synced_records} bản ghi")

                    self.logger.info(f"{username}: Đồng bộ {synced_records} bản ghi từ SQLite sang Firestore")
                    if progress_callback:
                        await progress_callback(1.0)
                    return {
                        "success": f"Đồng bộ {synced_records} bản ghi từ SQLite sang Firestore",
                        "synced_records": synced_records
                    }

        except asyncio.TimeoutError as e:
            self.logger.error(f"{username}: Timeout trong sync_from_sqlite: {str(e)}")
            if progress_callback:
                await progress_callback(1.0)
            return {"error": f"Timeout khi đồng bộ: {str(e)}", "synced_records": 0}
        except Exception as e:
            self.logger.error(f"{username}: Lỗi đồng bộ từ SQLite sang Firestore: {str(e)}")
            if progress_callback:
                await progress_callback(1.0)
            return {"error": f"Lỗi đồng bộ: {str(e)}", "synced_records": 0}
    
    
    
class Core:
    """Điều phối giữa SQLite và Firestore."""

    def __init__(self):
        self.logger = get_logger("Core")
        
        self.sqlite_handler = SQLiteHandler(self.logger, self)
        asyncio.create_task(self.sqlite_handler.start())
        self.firestore_handler = FirestoreHandler(self.logger, self)
        self.firestore_available = self.firestore_handler.firestore_available
        self.groq_client = None
        if hasattr(Config, 'GROQ_API_KEY') and Config.GROQ_API_KEY:
            try:
                self.groq_client = AsyncGroq(api_key=Config.GROQ_API_KEY)
                self.logger.info("Khởi tạo Grok client thành công")
            except Exception as e:
                self.logger.error(f"Lỗi khi khởi tạo Grok client: {str(e)}")
                self.groq_client = None

    async def cleanup_invalid_client_states(self):
        try:
            async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                current_time = int(time.time())
                await conn.execute(
                    "DELETE FROM client_states WHERE timestamp < ? AND username != ?",
                    (current_time - Config.SESSION_MAX_AGE, Config.ADMIN_USERNAME)
                )
                await conn.execute(
                    "DELETE FROM sessions WHERE expires_at < ? AND username != ?",
                    (current_time, Config.ADMIN_USERNAME)
                )
                await conn.commit()
                self.logger.info("Đã xóa các client_state và session hết hạn, bảo vệ admin")
        except Exception as e:
            self.logger.error(f"Lỗi xóa client_state hết hạn: {str(e)}")
            raise DatabaseError(f"Lỗi xóa client_state: {str(e)}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(Exception)
    )
    
    
    async def init_sqlite(self, max_attempts: int = 5, retry_delay: float = 1.0):
        """Khởi tạo SQLite và đồng bộ với Firestore nếu cần."""
        if not hasattr(Config, "ADMIN_USERNAME") or not Config.ADMIN_USERNAME:
            raise DatabaseError("Config.ADMIN_USERNAME không được cấu hình")

        username = Config.ADMIN_USERNAME
        self.logger.debug(f"{username}: Bắt đầu khởi tạo SQLite")
        
        # Khởi tạo SQLite
        await self.sqlite_handler.init_sqlite(max_attempts, retry_delay)

        if self.firestore_handler.firestore_available:
            try:
                # Kiểm tra xem SQLite có dữ liệu không
                has_sqlite_data = False
                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                    protected_collections = ["users", "sessions", "client_states", "collection_schemas"]
                    for table in protected_collections:
                        async with conn.execute(f"SELECT COUNT(*) FROM {table}") as cursor:
                            count = (await cursor.fetchone())[0]
                            if count > 0:
                                has_sqlite_data = True
                                break

                # Kiểm tra xem Firestore có dữ liệu không
                has_firestore_data = False
                async for coll in self.firestore_handler.db.collections():
                    if coll.id in protected_collections:
                        async for doc in coll.limit(1).stream():
                            has_firestore_data = True
                            break
                    if has_firestore_data:
                        break

                if has_sqlite_data and has_firestore_data:
                    self.logger.info(f"{username}: Cả SQLite và Firestore đều có dữ liệu, đồng bộ từ Firestore sang SQLite")
                    result = await self.firestore_handler.sync_to_sqlite(
                        username, batch_size=100, protected_only=True
                    )
                    if "error" in result:
                        self.logger.error(f"{username}: Lỗi đồng bộ bảng bảo vệ từ Firestore: {result['error']}")
                    else:
                        self.logger.info(f"{username}: Đồng bộ bảng bảo vệ từ Firestore thành công: {result}")
                elif has_firestore_data:
                    self.logger.info(f"{username}: SQLite trống, đồng bộ bảng bảo vệ từ Firestore sang SQLite")
                    result = await self.firestore_handler.sync_to_sqlite(
                        username, batch_size=100, protected_only=True
                    )
                    if "error" in result:
                        self.logger.error(f"{username}: Lỗi đồng bộ bảng bảo vệ từ Firestore: {result['error']}")
                    else:
                        self.logger.info(f"{username}: Đồng bộ bảng bảo vệ từ Firestore thành công: {result}")
                else:
                    self.logger.info(f"{username}: Firestore trống, đồng bộ bảng bảo vệ từ SQLite sang Firestore")
                    result = await self.firestore_handler.sync_from_sqlite(
                        username, batch_size=100, protected_only=True
                    )
                    if "error" in result:
                        self.logger.error(f"{username}: Lỗi đồng bộ bảng bảo vệ lên Firestore: {result['error']}")
                    else:
                        self.logger.info(f"{username}: Đồng bộ bảng bảo vệ lên Firestore thành công: {result}")

                # Dọn dẹp sau khi đồng bộ
                await self.cleanup_invalid_client_states()

                # *** THÊM CLEANUP SYNC_LOG Ở ĐÂY (sau đồng bộ, bảng đã tồn tại và data full) ***
                await self.cleanup_sync_log(days_old=7)  # Xóa log cũ >7 ngày, log info nếu xóa thành công

            except Exception as e:
                self.logger.error(f"{username}: Lỗi khi kiểm tra hoặc đồng bộ bảng bảo vệ: {str(e)}")
                raise DatabaseError(f"Lỗi đồng bộ bảng bảo vệ: {str(e)}")
        else:
            self.logger.info(f"{username}: Firestore không khả dụng, chỉ khởi tạo SQLite")
            # Dọn dẹp sau khi khởi tạo nếu không có Firestore
            await self.cleanup_invalid_client_states()
            
            # *** THÊM CLEANUP SYNC_LOG CHO TRƯỜNG HỢP KHÔNG FIRESTORE ***
            await self.cleanup_sync_log(days_old=7)  # Đảm bảo cleanup luôn chạy
            
    async def create_collection(self, collection_name: str, fields: Dict, username: str) -> Dict:
        """Tạo một collection mới trong SQLite và đồng bộ với Firestore nếu khả dụng."""
        try:
            async with asyncio.timeout(60):  # Timeout 60s theo tài liệu
                # Kiểm tra quyền create_collection
                if not await self.sqlite_handler.has_permission(username, "create_collection"):
                    self.logger.error(f"{username}: Không có quyền tạo collection")
                    return {"error": "Không có quyền tạo collection"}

                # Tạo collection trong SQLite
                result = await self.sqlite_handler.create_collection(collection_name, fields, username)
                if "error" in result:
                    self.logger.error(f"{username}: Lỗi tạo collection trong SQLite: {result['error']}")
                    return result

                # Đồng bộ schema với Firestore nếu khả dụng
                if self.firestore_handler and self.firestore_handler.firestore_available:
                    try:
                        schema_fields = {
                            key: info.get("type", "TEXT") if isinstance(info, dict) else str(info)
                            for key, info in fields.items()
                        }
                        # Lưu schema vào Firestore collection_schemas
                        schema_id = hashlib.sha256(collection_name.encode()).hexdigest()
                        await self.firestore_handler.firestore.collection("collection_schemas").document(schema_id).set({
                            "collection_name": collection_name,
                            "fields": schema_fields,
                            "timestamp": int(time.time())
                        })
                        self.logger.info(f"{username}: Đã đồng bộ schema {collection_name} lên Firestore")
                    except Exception as e:
                        self.logger.error(f"{username}: Lỗi đồng bộ schema {collection_name} lên Firestore: {str(e)}")
                        return {"error": f"Lỗi đồng bộ schema lên Firestore: {str(e)}"}

                self.logger.info(f"{username}: Đã tạo collection {collection_name} thành công")
                return result

        except asyncio.TimeoutError:
            self.logger.error(f"{username}: Timeout khi tạo collection {collection_name}")
            return {"error": "Hết thời gian tạo collection"}
        except Exception as e:
            error_result = await self.handle_error(e, f"Tạo collection {collection_name} cho {username}")
            self.logger.error(f"{error_result['error']}")
            return error_result
            
    async def drop_collection(self, collection_name: str, username: str) -> Dict:
        """Xóa một bảng/collection."""
        try:
            return await self.sqlite_handler.drop_collection(collection_name, username)
        except Exception as e:
            return await self.handle_error(e, f"Lỗi xóa collection {collection_name}")

    async def create_record(self, collection_name: str, data: Dict, username: str) -> Dict:
        """Tạo một bản ghi mới trong collection (bảng) được chỉ định."""
        try:
            return await self.sqlite_handler.create_record(collection_name, data, username)
        except Exception as e:
            return await self.handle_error(e, f"Lỗi tạo bản ghi trong {collection_name}")

    async def read_records(self, collection_name: str, username: str, page: int = 1, page_size: int = 10) -> Dict:
        try:
            result = await self.sqlite_handler.read_records(collection_name, username, page, page_size)
            if "error" in result:
                self.logger.error(f"{username}: Lỗi đọc bản ghi từ {collection_name}: {result['error']}")
                return result
            self.logger.info(f"{username}: Đọc {len(result.get('results', []))} bản ghi từ {collection_name}")
            return result
        except Exception as e:
            self.logger.error(f"{username}: Lỗi đọc bản ghi từ {collection_name}: {str(e)}")
            return {"error": f"Lỗi đọc bản ghi: {str(e)}"}
        
    async def update_record(self, collection_name: str, record_id: str, data: Dict, username: str) -> Dict:
        """Cập nhật một bản ghi trong collection (bảng) được chỉ định."""
        try:
            return await self.sqlite_handler.update_record(collection_name, record_id, data, username)
        except Exception as e:
            return await self.handle_error(e, f"Lỗi cập nhật bản ghi {record_id} trong {collection_name}")

    async def delete_record(self, collection_name: str, record_id: str, username: str) -> Dict:
        """Xóa một bản ghi từ collection (bảng) được chỉ định."""
        try:
            return await self.sqlite_handler.delete_record(collection_name, record_id, username)
        except Exception as e:
            return await self.handle_error(e, f"Lỗi xóa bản ghi {record_id} trong {collection_name}")

    async def get_client_state(self, session_token: str, username: str) -> Dict:
        """Lấy trạng thái phiên của người dùng."""
        state = await self.sqlite_handler.get_client_state(session_token, username)
        state["firestore_available"] = self.firestore_available
        return state

    async def save_client_state(self, session_token: str, state: Dict):
        """Lưu trạng thái phiên của người dùng."""
        if "username" not in state or not state["username"]:
            self.logger.error("Không thể lưu trạng thái: thiếu hoặc tên người dùng không hợp lệ")
            raise DatabaseError("Tên người dùng thiếu hoặc không hợp lệ trong trạng thái")
        await self.sqlite_handler.save_client_state(session_token, state)

    async def clear_client_state(self, session_token: str, username: str, log_sync: bool = False) -> RedirectResponse:
        """Xóa trạng thái phiên của người dùng, ánh xạ tới SQLiteHandler."""
        return await self.sqlite_handler.clear_client_state(session_token, username, log_sync)

    async def register_user(self, username: str, password: str, bot_password: Optional[str] = None) -> Dict:
        """Đăng ký người dùng mới."""
        return await self.sqlite_handler.register_user(username, password, bot_password)

    
    async def authenticate_user(
        self, 
        username: str, 
        password: str, 
        bot_password: Optional[str] = None
    ) -> Dict:
        """Xác thực thông tin đăng nhập."""
        try:
            result = await self.sqlite_handler.authenticate_user(
                username, password, bot_password
            )
            if "success" in result:
                return {
                    "success": "Đăng nhập thành công",
                    "session_token": result["session_token"],
                    "role": result["role"]
                }
            return result  # Trả về lỗi từ SQLiteHandler
        except Exception as e:
            self.logger.error(
                f"Lỗi xác thực thông tin đăng nhập cho {username}: {str(e)}"
            )
            return {"error": f"Lỗi xác thực: {str(e)}"}
            
    
    
    async def handle_error(self, error: Exception, message: str) -> Dict:
        """Xử lý lỗi và trả về Dict."""
        error_message = f"{message}: {str(error)}"
        self.logger.error(error_message, exc_info=True)
        return {"error": error_message}

    
    
    
    async def delete_records_by_condition(self, collection_name: str, conditions: Dict, username: str) -> Dict:
        """Xóa các bản ghi theo điều kiện trong SQLite, đồng bộ sang Firestore qua sync_from_sqlite."""
        try:
            result = await self.sqlite_handler.delete_records_by_condition(collection_name, conditions, username)
            if "error" in result:
                return result
            deleted_count = result.get("deleted_count", 0)
            self.logger.info(f"{username}: Đã xóa {deleted_count} bản ghi trong {collection_name} (SQLite)")
            return {
                "success": f"Đã xóa {deleted_count} bản ghi trong {collection_name}",
                "deleted_count": deleted_count
            }
        except Exception as e:
            self.logger.error(f"{username}: Lỗi xóa bản ghi trong {collection_name}: {str(e)}")
            return {"error": f"Lỗi xóa bản ghi: {str(e)}", "deleted_count": 0}

    async def sync_from_sqlite(
        self,
        username: str,
        batch_size: int = 100,
        progress_callback: Optional[Callable[[float], None]] = None,
        protected_only: bool = False,
        specific_collections: Optional[List[str]] = None,
        record_limit: Optional[int] = None
    ) -> Dict:
        """Đồng bộ dữ liệu từ SQLite sang Firestore."""
        return await self.firestore_handler.sync_from_sqlite(
            username, batch_size, progress_callback, protected_only, specific_collections, record_limit
        )

    async def sync_to_sqlite(
        self,
        username: str,
        progress_callback: Optional[Callable[[float], None]] = None,
        protected_only: bool = False,
        specific_collections: Optional[List[str]] = None,
        batch_size: int = 100
    ) -> Dict:
        """Đồng bộ dữ liệu từ Firestore sang SQLite."""
        return await self.firestore_handler.sync_to_sqlite(
            username, progress_callback, protected_only, specific_collections, batch_size
        )

    async def search_collections(self, query: str, username: str, page: int = 1, page_size: int = 100, collection: str = None) -> Dict:
        """Tìm kiếm Q&A trong bảng được chỉ định dựa trên từ khóa trong question hoặc answer."""
        try:
            async with qa_data_lock:  # Sử dụng qa_data_lock từ tab_training.py
                return await self.sqlite_handler.search_collections(query, username, page, page_size, collection)
        except asyncio.TimeoutError as e:
            self.logger.error(f"{username}: Timeout khi tìm kiếm Q&A: {str(e)}")
            return {"error": f"Timeout khi tìm kiếm: {str(e)}"}
        except Exception as e:
            self.logger.error(f"{username}: Lỗi tìm kiếm Q&A: {str(e)}")
            return {"error": f"Lỗi tìm kiếm: {str(e)}"}
    
    
    async def get_sync_status(self, page: int = 1, page_size: int = 10) -> Dict:
        """Đọc trạng thái đồng bộ từ sync_log một cách an toàn với mọi kiểu timestamp."""
        try:
            # Logging để debug giá trị đầu vào
            self.logger.debug(
                f"get_sync_status: page={page}, type(page)={type(page)}, "
                f"page_size={page_size}, type(page_size)={type(page_size)}"
            )

            # Ép kiểu an toàn
            try:
                page = int(page)
                page_size = int(page_size)
            except (ValueError, TypeError) as e:
                self.logger.error(
                    f"Lỗi ép kiểu: page={page}, page_size={page_size}, lỗi: {str(e)}"
                )
                return {"error": f"Tham số page hoặc page_size không hợp lệ: {str(e)}"}

            async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                # Lấy log
                async with conn.execute(
                    """
                    SELECT
                        id,
                        table_name,
                        record_id,
                        action,
                        CASE
                            WHEN typeof(timestamp) IN ('integer','real') THEN timestamp
                            WHEN typeof(timestamp)='text' AND trim(timestamp) GLOB '[0-9]*' 
                                THEN CAST(timestamp AS REAL)
                            ELSE NULL
                        END AS ts,
                        details
                    FROM sync_log
                    WHERE UPPER(action) IN ('SYNC_TO_SQLITE','SYNC_TO_FIRESTORE')
                    ORDER BY ts DESC NULLS LAST
                    LIMIT ? OFFSET ?
                    """,
                    (page_size, (page - 1) * page_size),
                ) as cursor:
                    now = float(time.time())
                    logs = []
                    rows = await cursor.fetchall()

                    for row in rows:
                        raw_ts = row[4]
                        if raw_ts is None:
                            self.logger.warning(
                                f"Bỏ qua sync_log có timestamp không hợp lệ: "
                                f"id={row[0]}, action={row[3]}"
                            )
                            continue

                        timestamp = float(raw_ts)
                        details = {}

                        raw_details = row[5]
                        if isinstance(raw_details, str) and raw_details.strip():
                            try:
                                details = json.loads(raw_details)
                            except json.JSONDecodeError:
                                self.logger.warning(
                                    f"Không parse được details JSON cho id={row[0]}"
                                )
                                details = {"_raw": raw_details}
                        elif isinstance(raw_details, dict):
                            details = raw_details

                        logs.append({
                            "id": row[0],
                            "table_name": row[1],
                            "record_id": row[2],
                            "action": row[3],
                            "timestamp": timestamp,
                            "time_diff_seconds": now - timestamp,
                            "details": details,
                        })

                # Lấy tổng số record
                async with conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM sync_log
                    WHERE UPPER(action) IN ('SYNC_TO_SQLITE','SYNC_TO_FIRESTORE')
                    """
                ) as cursor:
                    total = (await cursor.fetchone())[0] or 0

            self.logger.info(
                f"Lấy trạng thái đồng bộ: {len(logs)} bản ghi, trang {page}, tổng {total}"
            )
            return {
                "success": True,
                "page": page,
                "page_size": page_size,
                "total": total,
                "logs": logs,
            }

        except Exception as e:
            self.logger.error(f"Lỗi lấy trạng thái đồng bộ: {str(e)}", exc_info=True)
            return {"error": f"Lỗi lấy trạng thái đồng bộ: {str(e)}"}
            

    async def log_sync_action(
        self,
        table_name: str,
        record_id: str,
        action: str,
        details: Dict,
        username: str
    ) -> Dict:
        """Ghi log hành động đồng bộ vào sync_log."""
        try:
            async with asyncio.timeout(30):
                query = (
                    "INSERT INTO sync_log (id, table_name, record_id, action, timestamp, details) "
                    "VALUES (?, ?, ?, ?, ?, ?)"
                )
                params = (
                    str(uuid.uuid4()),
                    table_name,
                    record_id,
                    action,
                    int(time.time()),
                    json.dumps(details, ensure_ascii=False)
                )
                async with aiosqlite.connect(
                    Config.SQLITE_DB_PATH, timeout=30.0
                ) as conn:
                    await conn.execute(query, params)
                    await conn.commit()

                self.logger.info(
                    f"{username}: Ghi log đồng bộ cho {table_name}, action: {action}"
                )
                return {"success": "Ghi log đồng bộ thành công"}

        except asyncio.TimeoutError:
            self.logger.error(
                f"{username}: Timeout khi ghi log đồng bộ cho {table_name}"
            )
            return {"error": "Hết thời gian ghi log đồng bộ"}

        except Exception as e:
            return await self.handle_error(
                e, f"Ghi log đồng bộ cho {table_name} bởi {username}"
            )

    async def save_chat_config(self, session_token: str, username: str, model: str) -> Dict:
        """Lưu hoặc cập nhật cấu hình chat vào bảng chat_config."""
        try:
            async with asyncio.timeout(30):
                # Đảm bảo bảng chat_config tồn tại
                fields = {"username": "TEXT", "model": "TEXT"}
                result = await self.sqlite_handler.create_collection("chat_config", fields, username)
                if "error" in result and "đã tồn tại" not in result["error"].lower():
                    self.logger.error(f"{username}: Lỗi tạo bảng chat_config: {result['error']}")
                    return result

                # Lưu hoặc cập nhật cấu hình
                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                    await conn.execute(
                        "INSERT OR REPLACE INTO chat_config (id, username, model, timestamp) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            hashlib.sha256(session_token.encode()).hexdigest(),
                            username,
                            model,
                            int(time.time())
                        )
                    )
                    await conn.commit()
                    self.logger.info(f"{username}: Lưu cấu hình chat thành công, model: {model}")
                    return {"success": "Lưu cấu hình chat thành công"}

        except asyncio.TimeoutError:
            self.logger.error(f"{username}: Timeout khi lưu cấu hình chat")
            return {"error": "Hết thời gian lưu cấu hình chat"}

        except Exception as e:
            return await self.handle_error(e, f"Lưu cấu hình chat cho {username}")


    async def get_available_tables(self, username: str) -> Dict:
        """Lấy danh sách bảng có sẵn, trừ các bảng hệ thống."""
        try:
            async with asyncio.timeout(60):
                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=60.0) as conn:
                    async with conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ) as cursor:
                        tables = [
                            row[0] for row in await cursor.fetchall()
                            if row[0] not in Config.SYSTEM_TABLES
                        ]
                        self.logger.debug(f"{username}: Đã lấy danh sách bảng: {tables}")
                        return {"success": sorted(tables)}
        except asyncio.TimeoutError as e:
            self.logger.error(f"{username}: Timeout khi lấy danh sách bảng: {str(e)}")
            return {"error": f"Timeout khi lấy danh sách bảng: {str(e)}"}
        except Exception as e:
            return await self.handle_error(e, f"Lấy danh sách bảng cho {username}")

    
    
    
    async def create_records_batch(
        self,
        collection_name: str,
        records: List[Dict],
        username: str,
        progress_callback: Optional[Callable[[float], None]] = None
    ) -> Dict:
        """Tạo nhiều bản ghi trong collection, đảm bảo tất cả giá trị hợp lệ với SQLite."""
        async def create_batch_impl():
            try:
                async with asyncio.timeout(120):  # Timeout 120 giây
                    # Kiểm tra tên collection
                    if not validate_name(collection_name):
                        self.logger.error(f"{username}: Tên collection không hợp lệ: {collection_name}")
                        return {"error": "Tên collection không hợp lệ"}

                    async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=15.0) as conn:
                        await conn.execute("PRAGMA busy_timeout = 30000")

                        # Kiểm tra và tạo bảng nếu chưa tồn tại
                        async with conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                            (collection_name,)
                        ) as cursor:
                            if not await cursor.fetchone():
                                # Kiểm tra quyền tạo bảng
                                if not await self.sqlite_handler.has_permission(username, "create_table"):
                                    self.logger.error(f"{username}: Không có quyền tạo bảng {collection_name}")
                                    return {"error": f"Không có quyền tạo bảng {collection_name}"}

                                fields = {
                                    "id": "TEXT PRIMARY KEY",
                                    "content": "TEXT",
                                    "role": "TEXT",
                                    "type": "TEXT",
                                    "timestamp": "INTEGER"
                                }
                                columns = ", ".join([f'"{k}" {v}' for k, v in fields.items()])
                                await conn.execute(f'CREATE TABLE "{collection_name}" ({columns})')
                                await conn.execute(
                                    "INSERT INTO collection_schemas (id, collection_name, fields, timestamp) "
                                    "VALUES (?, ?, ?, ?)",
                                    (
                                        hashlib.sha256(collection_name.encode()).hexdigest(),
                                        collection_name,
                                        json.dumps(fields, ensure_ascii=False),
                                        int(time.time()),
                                    )
                                )
                                await conn.execute(
                                    f'CREATE INDEX IF NOT EXISTS idx_timestamp ON "{collection_name}" (timestamp)'
                                )
                                self.logger.info(f"{username}: Tạo bảng mới: {collection_name}")

                        # Lấy schema của bảng
                        async with conn.execute(
                            "SELECT fields FROM collection_schemas WHERE collection_name=?",
                            (collection_name,)
                        ) as cursor:
                            schema_row = await cursor.fetchone()
                            if not schema_row:
                                self.logger.warning(f"{username}: Không tìm thấy schema cho {collection_name}")
                                schema = {"id": "TEXT", "timestamp": "INTEGER"}
                            else:
                                schema = json.loads(schema_row[0])

                        # Lấy danh sách cột thực tế
                        async with conn.execute(f"PRAGMA table_info('{collection_name}')") as cursor:
                            columns_info = await cursor.fetchall()
                            valid_columns = {col[1] for col in columns_info}

                        # Cập nhật schema nếu có cột mới
                        new_fields = set()
                        for record in records:
                            new_fields.update(k for k in record.keys() if k not in valid_columns)
                        if new_fields:
                            for field in new_fields:
                                if not validate_name(field):
                                    self.logger.warning(f"{username}: Tên cột không hợp lệ: {field}, bỏ qua")
                                    continue
                                dtype = "TEXT"
                                await conn.execute(f'ALTER TABLE "{collection_name}" ADD COLUMN "{field}" {dtype}')
                                schema[field] = dtype
                                self.logger.debug(f"{username}: Thêm cột {field} ({dtype}) vào {collection_name}")
                            await conn.execute(
                                "INSERT OR REPLACE INTO collection_schemas (id, collection_name, fields, timestamp) "
                                "VALUES (?, ?, ?, ?)",
                                (
                                    hashlib.sha256(collection_name.encode()).hexdigest(),
                                    collection_name,
                                    json.dumps(schema, ensure_ascii=False),
                                    int(time.time()),
                                )
                            )

                        valid_records = []
                        total_records = min(len(records), 1000)  # Giới hạn 1000 bản ghi
                        if len(records) > 1000:
                            self.logger.warning(
                                f"{username}: Số lượng bản ghi vượt quá 1000, chỉ xử lý 1000 bản ghi đầu tiên trong {collection_name}"
                            )

                        for i, record in enumerate(records[:1000]):
                            record_copy = record.copy()
                            if "id" not in record_copy:
                                record_copy["id"] = str(uuid.uuid4())
                            if "created_by" in valid_columns:
                                record_copy["created_by"] = username
                            if "created_at" in valid_columns:
                                record_copy["created_at"] = int(time.time())
                            if "timestamp" in valid_columns:
                                record_copy["timestamp"] = int(time.time())

                            # Kiểm tra kích thước bản ghi
                            try:
                                record_json = json.dumps(record_copy, ensure_ascii=False)
                                if len(record_json.encode()) > 1_000_000:
                                    self.logger.warning(f"{username}: Bỏ qua bản ghi quá lớn trong {collection_name}")
                                    continue
                            except (TypeError, ValueError) as e:
                                self.logger.error(f"{username}: Không thể serialize bản ghi: {str(e)}")
                                continue

                            # Chuyển đổi giá trị dict/list thành JSON
                            for key in record_copy:
                                if not validate_name(key):
                                    self.logger.error(f"{username}: Tên cột không hợp lệ trong record: {key}")
                                    continue
                                if isinstance(record_copy[key], (dict, list)):
                                    try:
                                        record_copy[key] = json.dumps(record_copy[key], ensure_ascii=False)
                                    except (TypeError, ValueError) as e:
                                        self.logger.error(f"{username}: Không thể chuyển đổi giá trị cho cột {key}: {str(e)}")
                                        continue

                            invalid_columns = [k for k in record_copy.keys() if k not in valid_columns]
                            if invalid_columns:
                                self.logger.error(f"{username}: Bản ghi chứa cột không hợp lệ: {invalid_columns}")
                                continue
                            valid_records.append(record_copy)

                            if progress_callback and callable(progress_callback):
                                await progress_callback((i + 1) / total_records)

                        if not valid_records:
                            self.logger.warning(f"{username}: Không có bản ghi hợp lệ để tạo trong {collection_name}")
                            return {"error": "Không có bản ghi hợp lệ để tạo"}

                        # Chèn bản ghi theo batch với executemany
                        batch_size = 50
                        for i in range(0, len(valid_records), batch_size):
                            batch = valid_records[i:i + batch_size]
                            if batch:
                                columns = ", ".join(f'"{k}"' for k in batch[0].keys())
                                placeholders = ", ".join("?" for _ in batch[0])
                                values = [tuple(record.values()) for record in batch]
                                max_retries = 3
                                for attempt in range(max_retries):
                                    try:
                                        await conn.executemany(
                                            f'INSERT OR REPLACE INTO "{collection_name}" ({columns}) VALUES ({placeholders})',
                                            values
                                        )
                                        # Ghi log vào sync_log
                                        for record in batch:
                                            await conn.execute(
                                                "INSERT INTO sync_log (id, table_name, record_id, action, timestamp, details) "
                                                "VALUES (?, ?, ?, ?, ?, ?)",
                                                (
                                                    str(uuid.uuid4()),
                                                    collection_name,
                                                    record["id"],
                                                    "INSERT",
                                                    int(time.time()),
                                                    json.dumps(
                                                        {"username": username, "action": "create_records_batch"},
                                                        ensure_ascii=False
                                                    )
                                                )
                                            )
                                        await conn.commit()
                                        break
                                    except aiosqlite.OperationalError as e:
                                        if "database is locked" in str(e).lower() and attempt < max_retries - 1:
                                            self.logger.warning(f"{username}: Cơ sở dữ liệu bị khóa, thử lại lần {attempt + 1}")
                                            await asyncio.sleep(0.2 * (attempt + 1))
                                            continue
                                        raise

                        self.logger.info(f"{username}: Đã tạo {len(valid_records)} bản ghi trong {collection_name}")
                        return {
                            "success": f"Đã tạo {len(valid_records)} bản ghi trong {collection_name}",
                            "created_count": len(valid_records)
                        }

            except asyncio.TimeoutError as e:
                self.logger.error(f"{username}: Timeout khi tạo bản ghi hàng loạt trong {collection_name}: {str(e)}")
                return {"error": f"Timeout khi tạo bản ghi hàng loạt: {str(e)}"}
            except aiosqlite.OperationalError as e:
                self.logger.error(f"{username}: Lỗi cơ sở dữ liệu khi tạo bản ghi trong {collection_name}: {str(e)}")
                return {"error": f"Lỗi cơ sở dữ liệu: {str(e)}"}
            except Exception as e:
                self.logger.error(f"{username}: Lỗi tạo bản ghi hàng loạt trong {collection_name}: {str(e)}")
                return {"error": f"Lỗi tạo bản ghi hàng loạt: {str(e)}"}

        return await self.sqlite_handler.enqueue_write(create_batch_impl)

    async def cleanup_sync_log(self, days_old=7):
        """Xóa sync_log cũ hơn X ngày."""
        try:
            current_time = int(time.time())
            old_threshold = current_time - (days_old * 24 * 3600)
            async with aiosqlite.connect(Config.SQLITE_DB_PATH) as conn:
                await conn.execute("DELETE FROM sync_log WHERE timestamp < ?", (old_threshold,))
                await conn.commit()
                self.logger.info(f"Đã xóa sync_log cũ (> {days_old} ngày)")
        except Exception as e:
            self.logger.warning(f"Lỗi cleanup sync_log: {str(e)}")

    

    async def add_chat_message(
        self,
        username: str,
        session_token: str,
        content: str,
        file_url: str = None,
        role: str = "user",
        message_type: str = "text"
    ) -> Dict:
        """Lưu tin nhắn chat vào bảng chat_messages và ghi sync_log."""
        try:
            message_id = str(uuid.uuid4())
            current_time = int(time.time())

            async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                await conn.execute(
                    """
                    INSERT INTO chat_messages 
                    (id, session_token, username, content, role, type, file_url, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message_id,
                        session_token,
                        username,
                        content,
                        role,
                        message_type,
                        file_url,
                        current_time,
                    ),
                )

                await conn.execute(
                    """
                    INSERT INTO sync_log 
                    (id, table_name, record_id, action, timestamp, details)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        "chat_messages",
                        message_id,
                        "INSERT",
                        current_time,
                        json.dumps(
                            {
                                "username": username,
                                "action": "insert_chat_message",
                                "session_token": session_token,
                                "field": "file_url" if file_url else "content",
                            },
                            ensure_ascii=False,
                        ),
                    ),
                )
                await conn.commit()

            self.logger.info(f"{username}: Đã lưu tin nhắn chat với ID {message_id}")

            if file_url and file_url.endswith((".jpg", ".png", ".jpeg")):
                return {
                    "success": True,
                    "message_id": message_id,
                    "warning": (
                        "Grok miễn phí không hỗ trợ phân tích hình ảnh, "
                        "file được lưu để tham khảo."
                    ),
                }

            return {"success": True, "message_id": message_id}

        except Exception as e:
            self.logger.error(f"Lỗi thêm tin nhắn chat cho {username}: {str(e)}")
            raise DatabaseError(f"Lỗi thêm tin nhắn: {str(e)}")

    
    async def delete_chat_messages(self, username: str, session_token: str) -> List[str]:
        """Xóa lịch sử chat, file/hình ảnh liên quan, và ghi sync_log cho hành động DELETE."""
        try:
            from utils.core_common import check_disk_space
            check_disk_space()  # Kiểm tra dung lượng đĩa trước khi xóa

            record_ids = []
            file_urls = []
            async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=60.0) as conn:
                # Truy vấn id và file_url từ chat_messages
                async with conn.execute(
                    """
                    SELECT id, file_url 
                    FROM chat_messages 
                    WHERE session_token = ? AND username = ?
                    """,
                    (session_token, username),
                ) as cursor:
                    rows = await cursor.fetchall()
                    record_ids = [row[0] for row in rows]
                    file_urls = [row[1] for row in rows if row[1]]  # Lấy file_url không null

                # Xóa bản ghi trong chat_messages
                await conn.execute(
                    """
                    DELETE FROM chat_messages 
                    WHERE session_token = ? AND username = ?
                    """,
                    (session_token, username),
                )

                current_time = int(time.time())
                # Ghi log DELETE vào sync_log
                for record_id in record_ids:
                    await conn.execute(
                        """
                        INSERT INTO sync_log 
                        (id, table_name, record_id, action, timestamp, details)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid.uuid4()),
                            "chat_messages",
                            record_id,
                            "DELETE",
                            current_time,
                            json.dumps(
                                {
                                    "username": username,
                                    "action": "delete_chat_history",
                                    "session_token": session_token,
                                    "record_id": record_id,
                                },
                                ensure_ascii=False,
                            ),
                        ),
                    )

                await conn.commit()

            # Xóa file/hình ảnh vật lý trong /tmp/chat_files/
            deleted_files = []
            for file_url in file_urls:
                file_id = file_url.split("/")[-1]  # Lấy file_id từ /files/<file_id>
                file_path = os.path.join(Config.CHAT_FILE_STORAGE_PATH, file_id)
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        deleted_files.append(file_id)
                        self.logger.info(f"{username}: Đã xóa file {file_path}")
                    except Exception as e:
                        self.logger.error(f"{username}: Lỗi xóa file {file_path}: {str(e)}")
                        # Tiếp tục xử lý các file khác, không raise lỗi

            self.logger.info(
                f"{username}: Đã xóa {len(record_ids)} tin nhắn và {len(deleted_files)} file, ghi sync_log"
            )
            return record_ids

        except Exception as e:
            self.logger.error(f"Lỗi xóa lịch sử chat cho {username}: {str(e)}")
            raise DatabaseError(f"Lỗi xóa lịch sử chat: {str(e)}")
            
    
    async def upload_file(
        self,
        file_content: bytes,
        content_type: str,
        storage_path: str,
        file_id: str
    ) -> str:
        try:
            os.makedirs(storage_path, exist_ok=True)
            file_path = os.path.join(storage_path, f"{file_id}")
            with open(file_path, 'wb') as f:
                f.write(file_content)

            if not os.path.exists(file_path):
                self.logger.error(
                    f"File {file_id} không tồn tại tại {file_path} sau khi lưu"
                )
                raise RuntimeError(
                    f"File {file_id} không tồn tại tại {file_path}"
                )

            self.logger.info(
                f"Đã lưu file {file_id} vào {file_path}, exists: {os.path.exists(file_path)}"
            )

            # Trả về URL đúng dựa trên storage_path
            if storage_path == Config.AVATAR_STORAGE_PATH:
                return f"/avatars/{file_id}"
            return f"/files/{file_id}"

        except Exception as e:
            self.logger.error(
                f"Lỗi lưu file {file_id}: {str(e)}", exc_info=True
            )
            raise RuntimeError(f"Lỗi lưu file: {str(e)}")
            