import sys
import time
import asyncio
import aiosqlite
from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from nicegui import ui, app
from pydantic import BaseModel
from typing import Optional, Callable, List, Dict
from config import Config
from core import Core
from uiapp.ui_manager import UIManager
from uiapp.layouts.dashboard import DashboardLayout
from utils.logging import get_logger
from utils.core_common import validate_password_strength, check_disk_space
import re
import os
import json
import traceback
import hashlib
import uuid
from pathlib import Path  # Thêm
from importlib.util import spec_from_file_location, module_from_spec

logger = get_logger("App")
fastapi_app = FastAPI(title=Config.APP_NAME)
core = Core()
ui_manager = UIManager(core)

# Ở đầu file app.py
CHAT_COMPONENTS = {}

# Pydantic models
class LoginData(BaseModel):
    username: str
    password: str
    bot_password: Optional[str] = None

class RegisterData(BaseModel):
    username: str
    password: str
    confirm_password: str
    bot_password: Optional[str] = None

# CORS Middleware
fastapi_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

# Common utilities
async def validate_user_input(data: dict, is_register: bool = False) -> dict:
    result = {"success": True, "error": None}
    if not data.get("username") or len(data["username"]) < 3 or not re.match(r'^[a-zA-Z0-9_]+$', data["username"]):
        result["success"] = False
        result["error"] = "Tên người dùng không hợp lệ"
        return result
    if not validate_password_strength(data.get("password")):
        result["success"] = False
        result["error"] = "Mật khẩu phải dài ít nhất 8 ký tự"
        return result
    if data.get("bot_password") and not validate_password_strength(data["bot_password"]):
        result["success"] = False
        result["error"] = "Mật khẩu bot phải dài ít nhất 8 ký tự"
        return result
    if is_register and data.get("password") != data.get("confirm_password"):
        result["success"] = False
        result["error"] = "Mật khẩu và xác nhận không khớp"
        return result
    if (len(data["username"].encode()) > 1048576 or
        len(data["password"].encode()) > 1048576 or
        (data.get("bot_password") and len(data["bot_password"].encode()) > 1048576)):
        result["success"] = False
        result["error"] = "Dữ liệu đầu vào quá lớn"
        return result
    return result

def sanitize_state(state: dict) -> dict:
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
                logger.warning(f"Bỏ qua khóa {key} vì không thể tuần tự hóa JSON")
        else:
            logger.warning(f"Bỏ qua khóa {key} vì kiểu dữ liệu không hỗ trợ JSON: {type(value)}")
    return clean_state


def set_auth_cookies(response: Response, session_token: str, username: str):
    response.set_cookie("session_token", session_token, max_age=Config.SESSION_MAX_AGE, path="/", samesite="Lax", httponly=True, secure=Config.SECURE_COOKIES)
    response.set_cookie("username", username, max_age=Config.SESSION_MAX_AGE, path="/", samesite="Lax", httponly=True, secure=Config.SECURE_COOKIES)

def set_auth_cookies_js(session_token: str, username: str):
    ui.run_javascript(f"""
        document.cookie = 'session_token={session_token}; max-age={Config.SESSION_MAX_AGE}; path=/; SameSite=Lax; Secure={str(Config.SECURE_COOKIES).lower()}';
        document.cookie = 'username={username}; max-age={Config.SESSION_MAX_AGE}; path=/; SameSite=Lax; Secure={str(Config.SECURE_COOKIES).lower()}';
        window.location.href = '/dashboard';
    """)



@app.post("/update-ui")
async def update_ui(request: Request):
    try:
        data = await request.json()
        client_id = data.get("client_id")
        if not client_id:
            logger.error("Missing client_id in update-ui request")
            return JSONResponse({"error": "Missing client_id"}, status_code=400)

        chat_component = CHAT_COMPONENTS.get(client_id)
        if not chat_component:
            logger.error(f"No ChatComponent found for client_id={client_id}")
            return JSONResponse({"error": "No ChatComponent found"}, status_code=500)

        username = chat_component.client_state.get("username", "")
        async def safe_update():
            with chat_component.messages_container:
                await chat_component.update_messages()
                await chat_component.scroll_to_bottom()

        await safe_update()  # Thay ui.run_safe bằng gọi trực tiếp
        logger.info(f"{username}: UI updated successfully for client_id={client_id}")
        return JSONResponse({"success": "UI updated"})

    except Exception as e:
        logger.error(f"Error in update-ui: {str(e)}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/typing-complete/{message_id}")
async def typing_complete(message_id: str, request: Request):
    try:
        data = await request.json()
        response = data.get("response")
        if not response:
            logger.error(f"Không nhận được response từ JavaScript cho message_id={message_id}")
            return JSONResponse({"error": "Không nhận được response"}, status_code=400)

        client_id = data.get("client_id")
        chat_component = CHAT_COMPONENTS.get(client_id)
        if not chat_component:
            logger.error(f"Không tìm thấy ChatComponent cho message_id={message_id}, client_id={client_id}")
            return JSONResponse({"error": "Không tìm thấy ChatComponent"}, status_code=500)

        username = chat_component.client_state.get("username", "")
        # Cập nhật nội dung tin nhắn trong self.messages
        for msg in chat_component.messages:
            if msg["id"] == message_id:
                msg["content"] = response
                break
        else:
            logger.error(f"Không tìm thấy tin nhắn với message_id={message_id} trong self.messages")
            return JSONResponse({"error": "Không tìm thấy tin nhắn"}, status_code=400)

        logger.info(f"{username}: Hoàn tất hiệu ứng đánh máy cho message_id={message_id}")
        return JSONResponse({"success": "Cập nhật thành công"})

    except Exception as e:
        logger.error(f"Lỗi xử lý typing-complete cho message_id={message_id}: {str(e)}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)
        
async def handle_error(e: Exception, username: str, action: str, core: Core) -> dict:
    error_msg = f"Lỗi {action}: {str(e)}"
    if await core.sqlite_handler.has_permission(username, "admin_access"):
        error_msg += f"\nChi tiết: {traceback.format_exc()}"
    logger.error(error_msg, exc_info=True)
    return {"error": str(e), "status_code": 500}

AUTH_FIELDS = {
    "username": {
        "label": "Tên người dùng",
        "type": "text",
        "hint": "Tối thiểu 3 ký tự, chỉ chứa chữ, số, dấu gạch dưới",
        "validation": {
            "required": "lambda x: bool(x) or 'Trường này là bắt buộc'",
            "min_length": "lambda x: len(x or '') >= 3 or 'Tối thiểu 3 ký tự'",
            "pattern": "lambda x: re.match(r'^[a-zA-Z0-9_]+$', x or '') or 'Chỉ chứa chữ, số, dấu gạch dưới'"
        }
    },
    "password": {
        "label": "Mật khẩu",
        "type": "password",
        "hint": "Tối thiểu 8 ký tự",
        "validation": {
            "required": "lambda x: bool(x) or 'Trường này là bắt buộc'",
            "min_length": "lambda x: len(x or '') >= 8 or 'Tối thiểu 8 ký tự'"
        }
    },
    "confirm_password": {
        "label": "Xác nhận mật khẩu",
        "type": "password",
        "validation": {
            "required": "lambda x: bool(x) or 'Trường này là bắt buộc'"
        }
    },
    "bot_password": {
        "label": "Mật khẩu bot",
        "type": "password",
        "validation": {
            "required": "lambda x: bool(x) or 'Mật khẩu bot là bắt buộc'",
            "min_length": "lambda x: len(x or '') >= 8 or 'Tối thiểu 8 ký tự'"
        }
    }
}

@fastapi_app.on_event("startup")
async def startup_event():
    try:
        check_config()
        if not os.path.exists(Config.SQLITE_DB_PATH):
            logger.info(f"Tạo mới cơ sở dữ liệu SQLite tại {Config.SQLITE_DB_PATH}")
        await core.init_sqlite(max_attempts=5, retry_delay=1.0)
        await core.cleanup_invalid_client_states()
        # Khởi tạo app.storage.user["clients"]
        app.storage.user["clients"] = {}
        logger.info("Khởi tạo app.storage.user['clients'] thành công")
        ui.context.app.core = core
        logger.info("Ứng dụng đã khởi động")
    except Exception as e:
        error_result = await handle_error(e, "", "khởi động ứng dụng", core)
        logger.error(error_result["error"], exc_info=True)
        raise

async def load_tabs(ui_manager: UIManager, core: Core, username: str, client_state: dict, specific_tab: str = None):
    logger.info(f"{username}: Bắt đầu tải tabs, specific_tab={specific_tab}")
    if specific_tab or not ui_manager.registered_tabs:
        ui_dir = Path("uiapp")
        if not ui_dir.exists() or not ui_dir.is_dir():
            logger.error(f"Thư mục UI không tồn tại hoặc không phải thư mục tại {ui_dir}")
            return
        tab_files = [f for f in ui_dir.glob("tab_*.py") if f.is_file() and f.suffix == ".py"]
        if not tab_files:
            logger.warning(f"{username}: Không tìm thấy file tab_*.py trong thư mục uiapp/")
            return
        is_admin = await core.sqlite_handler.has_permission(username, "admin_access")
        sensitive_tabs = {"tab_training"}  # Chỉ tab_training yêu cầu quyền admin
        for file_path in tab_files:
            try:
                module_name = file_path.stem
                if specific_tab and module_name != f"tab_{specific_tab.lower()}":
                    continue
                logger.debug(f"{username}: Đang xử lý file {module_name}")
                if module_name in sensitive_tabs and not is_admin:
                    logger.info(f"{username}: Không có quyền truy cập tab {module_name}")
                    continue
                spec = spec_from_file_location(module_name, file_path)
                if spec is None:
                    logger.error(f"Không thể tạo spec cho module {module_name}")
                    continue
                module = module_from_spec(spec)
                sys.modules[module_name] = module
                try:
                    spec.loader.exec_module(module)
                except SyntaxError as e:
                    logger.error(f"Lỗi cú pháp trong {file_path}: {str(e)}")
                    continue
                if hasattr(module, "create_tab"):
                    create_tab_result = module.create_tab(core)
                    if asyncio.iscoroutine(create_tab_result):
                        render_func, update_func = await create_tab_result
                    else:
                        render_func, update_func = create_tab_result
                    if callable(render_func) and callable(update_func):
                        tab_name = module_name.replace("tab_", "").capitalize()
                        logger.debug(f"{username}: Tên tab được tạo: {tab_name}")
                        if not tab_name or not tab_name[0].isupper():
                            logger.error(f"Tên tab {tab_name} không hợp lệ (phải viết hoa chữ đầu)")
                            continue
                        if tab_name in ui_manager.registered_tabs:
                            logger.warning(f"Tab {tab_name} đã được đăng ký, bỏ qua {module_name}")
                            continue
                        icon = {
                            "Account": "person",
                            "Chat": "chat",
                            "Database": "database",
                            "Management": "settings",
                            "Interface": "api",
                            "Faq": "help",
                            "Training": "school"
                        }.get(tab_name, "extension")
                        ui_manager.register_tab(tab_name, render_func, update_func, icon)
                        logger.info(f"{username}: Đã đăng ký tab {tab_name} với icon {icon}")
                        client_state["registered_tabs"] = list(ui_manager.registered_tabs.keys())
                        await core.save_client_state(client_state.get("session_token", ""), client_state)
                    else:
                        logger.error(f"Hàm render_func/update_func không hợp lệ trong {module_name}")
                else:
                    logger.warning(f"Module {module_name} không có hàm create_tab")
            except Exception as e:
                logger.error(f"{username}: Lỗi tải tab {file_path}: {str(e)}", exc_info=True)
                
@ui.page("/auth")
async def auth_page(request: Request):
    async def handle_login(data: dict, progress_callback: Optional[Callable] = None):
        try:
            logger.info(f"handle_login: Thử đăng nhập với username={data['username']}")
            validation_result = await validate_user_input(data)
            if not validation_result["success"]:
                ui.notify(validation_result["error"], type="negative")
                return {"success": False, "error": validation_result["error"]}
            result = await core.authenticate_user(
                data["username"], data["password"], data.get("bot_password")
            )
            if result.get("success"):
                session_token = result["session_token"]
                state_result = await update_and_save_client_state(
                    core, session_token, data["username"], core.firestore_handler.firestore_available
                )
                if not state_result["success"]:
                    ui.notify(state_result["error"], type="negative")
                    return {"success": False, "error": state_result["error"]}
                logger.info(f"Đăng nhập thành công cho {data['username']}, session_token={session_token[:10]}...")
                set_auth_cookies_js(session_token, data["username"])
                return {"success": True, "message": "Đăng nhập thành công"}
            ui.notify(result["error"], type="negative")
            return {"success": False, "error": result["error"]}
        except Exception as e:
            error_result = await handle_error(e, data["username"], "đăng nhập", core)
            ui.notify(error_result["error"], type="negative")
            return {"success": False, "error": error_result["error"]}

    async def handle_register(data: dict, progress_callback: Optional[Callable] = None):
        try:
            logger.info(f"handle_register: Thử đăng ký với username={data['username']}")
            validation_result = await validate_user_input(data, is_register=True)
            if not validation_result["success"]:
                ui.notify(validation_result["error"], type="negative")
                return {"success": False, "error": validation_result["error"]}
            result = await core.register_user(
                data["username"], data["password"], data.get("bot_password")
            )
            if result.get("success"):
                session_token = result["session_token"]
                state_result = await update_and_save_client_state(
                    core, session_token, data["username"], core.firestore_handler.firestore_available
                )
                if not state_result["success"]:
                    ui.notify(state_result["error"], type="negative")
                    return {"success": False, "error": state_result["error"]}
                logger.info(f"Đăng ký thành công cho {data['username']}, session_token={session_token[:10]}...")
                async with aiosqlite.connect(Config.SQLITE_DB_PATH) as conn:
                    created_at = int(time.time())
                    await conn.execute(
                        "INSERT INTO sync_log (id, table_name, record_id, action, timestamp, details) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (str(uuid.uuid4()), "users", hashlib.sha256(data["username"].encode()).hexdigest(), "SYNC", created_at, json.dumps({"username": data["username"], "action": "register"}))
                    )
                    await conn.commit()
                set_auth_cookies_js(session_token, data["username"])
                return {"success": True, "message": "Đăng ký thành công"}
            ui.notify(result["error"], type="negative")
            return {"success": False, "error": result["error"]}
        except Exception as e:
            error_result = await handle_error(e, data["username"], "đăng ký", core)
            ui.notify(error_result["error"], type="negative")
            return {"success": False, "error": error_result["error"]}

    try:
        async with asyncio.timeout(30):
            logger.debug("Bắt đầu xử lý auth_page")
            session_token = request.cookies.get("session_token", "")
            username = request.cookies.get("username", "")
            if session_token and username:
                logger.debug(f"Kiểm tra session cho username={username}, session_token={session_token[:10]}...")
                client_state = await core.get_client_state(session_token, username)
                if client_state.get("authenticated") and client_state.get("timestamp", 0) >= int(time.time()) - Config.SESSION_MAX_AGE:
                    logger.info(f"Phiên hợp lệ cho {username}, chuyển hướng đến /dashboard")
                    return RedirectResponse(url="/dashboard", status_code=302)
            if not core:
                logger.error("Core không được khởi tạo trong auth_page")
                ui.notify("Lỗi hệ thống: Core không được khởi tạo", type="negative")
            if not ui_manager:
                logger.error("UIManager không được khởi tạo trong auth_page")
                ui.notify("Lỗi hệ thống: UIManager không được khởi tạo", type="negative")
            error = request.query_params.get("error")
            if error:
                ui.notify(f"Lỗi: {error}", type="negative")
            logger.debug("Gọi ui_manager.render_auth")
            return await ui_manager.render_auth(
                on_login=handle_login,
                on_register=handle_register,
                fields=AUTH_FIELDS
            )
    except asyncio.TimeoutError:
        logger.error("Timeout trong auth_page", exc_info=True)
        ui.notify("Hết thời gian tải trang, vui lòng thử lại!", type="negative")
        return await ui_manager.render_auth(
            on_login=handle_login,
            on_register=handle_register,
            fields=AUTH_FIELDS
        )
    except Exception as e:
        error_result = await handle_error(e, "", "xử lý auth_page", core)
        ui.notify(f"Lỗi hệ thống: {error_result['error']}", type="negative")
        return await ui_manager.render_auth(
            on_login=handle_login,
            on_register=handle_register,
            fields=AUTH_FIELDS
        )

async def get_session_info(request: Request) -> tuple[str, str]:
    session_token = request.cookies.get("session_token", "")
    username = request.cookies.get("username", "")
    logger.debug(f"get_session_info: session_token={session_token[:10]}..., username={username}")
    if not session_token or not username or not re.match(r'^[a-zA-Z0-9_-]{32,}$', session_token):
        logger.warning(f"Phiếu hoặc không hợp lệ session_token/username: {session_token[:10]}..., {username}")
        raise ValueError("Phiếu hoặc không hợp lệ session_token/username")
    return session_token, username

async def handle_session(request: Request, core: Core, response: Response = None) -> tuple[str, str, dict]:
    try:
        session_token, username = await get_session_info(request)
        client_state = await core.get_client_state(session_token, username)
        if not client_state.get("authenticated") or client_state.get("timestamp", 0) < int(time.time()) - Config.SESSION_MAX_AGE:
            logger.warning(f"Phiên không hợp lệ hoặc hết hạn cho {username}")
            response = await core.clear_client_state(session_token, username, log_sync=True)
            if response and isinstance(response, Response):
                response.delete_cookie("session_token", path="/", samesite="Lax", secure=Config.SECURE_COOKIES)
                response.delete_cookie("username", path="/", samesite="Lax", secure=Config.SECURE_COOKIES)
            raise ValueError("Phiên không hợp lệ hoặc hết hạn")
        if client_state.get("timestamp", 0) < int(time.time()) - (Config.SESSION_MAX_AGE * 0.8):
            client_state["timestamp"] = int(time.time())
            client_state = sanitize_state(client_state)
            if len(json.dumps(client_state).encode()) > 1048576:
                logger.error(f"Kích thước client_state vượt quá 1MB cho {username}")
                raise ValueError("Trạng thái phiên quá lớn")
            await core.save_client_state(session_token, client_state)
            if response:
                set_auth_cookies(response, session_token, username)
        return session_token, username, client_state
    except ValueError as e:
        raise e
    except Exception as e:
        error_result = await handle_error(e, "", "xử lý phiên", core)
        raise ValueError(error_result["error"])

@ui.page("/")
async def index_page(request: Request):
    logger.debug("Truy cập route /")
    try:
        session_token, username = await get_session_info(request)
        client_state = await core.get_client_state(session_token, username)
        if not client_state.get("authenticated", False):
            logger.debug("Phiên không được xác thực, chuyển hướng tới /auth")
            return RedirectResponse(url="/auth", status_code=302)
        logger.debug("Phiên hợp lệ, render DashboardLayout")
        is_admin = await core.sqlite_handler.has_permission(username, "admin_access")
        async def handle_tab_select(tab_name: str):
            try:
                client_state = await core.get_client_state(session_token, username)
                client_state["selected_tab"] = tab_name
                client_state["timestamp"] = int(time.time())
                client_state = sanitize_state(client_state)
                if len(json.dumps(client_state).encode()) > 1048576:
                    logger.error(f"Kích thước client_state vượt quá 1MB cho {username}")
                    ui.notify("Trạng thái phiên quá lớn", type="negative")
                    return
                await core.save_client_state(session_token, client_state)
                ui.update()
            except ValueError as ve:
                logger.warning(f"Lỗi chọn tab {tab_name}: {str(ve)}")
                ui.notify(f"Lỗi: {str(ve)}", type="negative")
            except Exception as e:
                error_result = await handle_error(e, username, f"chọn tab {tab_name}", core)
                ui.notify(error_result["error"], type="negative")
        return await DashboardLayout(
            username=username,
            is_admin=is_admin,
            tabs={name: {"name": tab["name"], "icon": tab["icon"]} for name, tab in ui_manager.registered_tabs.items()},
            core=core,
            on_logout=lambda: core.clear_client_state(session_token, username, log_sync=True),
            on_tab_select=handle_tab_select
        ).render(client_state)
    except Exception as e:
        error_result = await handle_error(e, "", "xử lý route /", core)
        return RedirectResponse(url="/auth?error=Lỗi+hệ+thống", status_code=302)



@fastapi_app.post("/api/logout")
async def api_logout(request: Request):
    try:
        session_token, username = await get_session_info(request)
        logger.info(f"Đăng xuất cho username={username}, session_token={session_token[:10]}...")
        response = await core.clear_client_state(session_token, username, log_sync=True)
        response.delete_cookie("session_token", path="/", samesite="Lax", secure=Config.SECURE_COOKIES)
        response.delete_cookie("username", path="/", samesite="Lax", secure=Config.SECURE_COOKIES)
        return JSONResponse({"success": "Đăng xuất thành công", "redirect": "/auth"})
    except ValueError as ve:
        logger.warning(f"Lỗi đăng xuất: {str(ve)}")
        return JSONResponse({"error": str(ve)}, status_code=400)
    except Exception as e:
        error_result = await handle_error(e, "", "đăng xuất", core)
        return JSONResponse({"error": error_result["error"]}, status_code=error_result["status_code"])

@fastapi_app.post("/api/sync")
async def api_sync(request: Request):
    try:
        session_token, username, client_state = await handle_session(request, core)
        if not await core.sqlite_handler.has_permission(username, "sync_data"):
            return JSONResponse({"error": "Chỉ admin có thể kích hoạt đồng bộ!"}, status_code=403)
        if not await check_firestore_availability(request, core):
            return JSONResponse({"error": "Firestore không khả dụng!"}, status_code=503)
        async with asyncio.timeout(60):
            async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                async with conn.execute(
                    "SELECT MAX(timestamp) FROM sync_log WHERE action IN ('sync_to_sqlite', 'sync_from_sqlite')"
                ) as cursor:
                    last_sync = await cursor.fetchone()
                    if last_sync and last_sync[0] and (int(time.time()) - last_sync[0]) < Config.SYNC_MIN_INTERVAL:
                        logger.info(f"Bỏ qua đồng bộ: lần đồng bộ trước cách đây dưới {Config.SYNC_MIN_INTERVAL} giây")
                        return JSONResponse({"success": f"Đồng bộ gần đây, chờ {Config.SYNC_MIN_INTERVAL} giây"}, status_code=429)
        check_disk_space()
        async with asyncio.timeout(300):
            async with core.firestore_lock:
                async def progress_callback(progress: float):
                    logger.info(f"Đồng bộ tiến trình: {progress*100:.1f}%")
                    ui.notify(f"Đồng bộ: {progress*100:.1f}%", type="info")
                result_to_sqlite = await core.sync_to_sqlite(username, protected_only=False, progress_callback=progress_callback)
                result_from_sqlite = await core.sync_from_sqlite(username, protected_only=False, progress_callback=progress_callback)
                combined_result = {
                    "success": f"{result_to_sqlite.get('success', '')}; {result_from_sqlite.get('success', '')}",
                    "synced_to_sqlite": result_to_sqlite.get("synced_records", 0),
                    "synced_to_firestore": result_from_sqlite.get("synced_records", 0),
                    "error": result_to_sqlite.get("error") or result_from_sqlite.get("error")
                }
                return JSONResponse(combined_result)
    except asyncio.TimeoutError as e:
        logger.error(f"Timeout khi đồng bộ: {str(e)}", exc_info=True)
        return JSONResponse({"error": f"Timeout khi đồng bộ: {str(e)}"}, status_code=504)
    except ValueError as ve:
        logger.warning(f"Lỗi đồng bộ: {str(ve)}")
        return JSONResponse({"error": str(ve)}, status_code=400)
    except Exception as e:
        error_result = await handle_error(e, username, "đồng bộ", core)
        return JSONResponse({"error": error_result["error"]}, status_code=error_result["status_code"])

async def check_firestore_availability(request: Request, core: Core) -> bool:
    logger.debug("Kiểm tra tính khả dụng của Firestore")
    if not core.firestore_handler.firestore_available:
        request.state.firestore_warning = "Firestore không khả dụng, ứng dụng chạy ở chế độ cục bộ."
        logger.info("Firestore không khả dụng, chạy với SQLite cục bộ")
        return False
    try:
        test_doc_ref = core.firestore_handler.db.collection("test").document("ping")
        await test_doc_ref.set({"timestamp": int(time.time())})
        await test_doc_ref.delete()
        logger.debug("Firestore kiểm tra thành công")
        return True
    except Exception as e:
        logger.warning(f"Lỗi kiểm tra Firestore: {str(e)}", exc_info=True)
        request.state.firestore_warning = "Firestore không khả dụng, chạy ở chế độ cục bộ."
        return False

@fastapi_app.middleware("http")
async def auth_middleware(request: Request, call_next):
    public_paths = ["/auth", "/api/login", "/api/register", "/api/logout", "/api/sync"]
    if request.url.path in public_paths or request.url.path.startswith("/_nicegui"):
        logger.debug(f"Truy cập đường dẫn công khai: {request.url.path}")
        return await call_next(request)
    try:
        session_token, username, client_state = await handle_session(request, core)
        await check_firestore_availability(request, core)
        request.state.user = sanitize_state(client_state)
        logger.info(f"Phiên hợp lệ cho {username}, session_token={session_token[:10]}...")
    except ValueError as e:
        logger.warning(f"Middleware: {str(e)}, chuyển hướng về /auth")
        response = RedirectResponse(url="/auth?error=Vui+lòng+đăng+nhập", status_code=302)
        response.delete_cookie("session_token", path="/", samesite="Lax", secure=Config.SECURE_COOKIES)
        response.delete_cookie("username", path="/", samesite="Lax", secure=Config.SECURE_COOKIES)
        return response
    except Exception as e:
        error_result = await handle_error(e, "", "xử lý middleware", core)
        response = RedirectResponse(url="/auth?error=Phiên+không+hợp+lệ", status_code=302)
        response.delete_cookie("session_token", path="/", samesite="Lax", secure=Config.SECURE_COOKIES)
        response.delete_cookie("username", path="/", samesite="Lax", secure=Config.SECURE_COOKIES)
        return response
    return await call_next(request)


@ui.page("/dashboard")
async def dashboard(request: Request):
    try:
        async with asyncio.timeout(10):  # Giới hạn dưới 3 giây
            start_time = time.time()
            session_token, username, client_state = await handle_session(request, core)
            logger.info(f"{username}: handle_session took {time.time() - start_time} seconds")

            client_state["firestore_available"] = False  # Giả sử Firestore không cần thiết
            logger.debug(f"{username}: Bỏ qua kiểm tra Firestore để tối ưu thời gian")

            is_admin = await core.sqlite_handler.has_permission(username, "admin_access")
            start_time = time.time()
            if not ui_manager.registered_tabs or "Chat" not in ui_manager.registered_tabs:
                await load_tabs(ui_manager, core, username, client_state)
            logger.info(f"{username}: load_tabs took {time.time() - start_time} seconds")

            async def handle_logout():
                try:
                    response = await core.clear_client_state(session_token, username, log_sync=True)
                    ui.run_javascript("""
                        document.cookie = 'session_token=; max-age=0; path=/; SameSite=Lax';
                        document.cookie = 'username=; max-age=0; path=/; SameSite=Lax';
                        window.location.href = '/auth';
                    """)
                    return {"success": "Đăng xuất thành công"}
                except ValueError as ve:
                    logger.warning(f"{username}: Lỗi đăng xuất: {str(ve)}")
                    ui.notify(f"Lỗi: {str(ve)}", type="negative")
                    return {"success": False, "error": str(ve)}
                except Exception as e:
                    error_result = await handle_error(e, username, "đăng xuất", core)
                    ui.notify(error_result["error"], type="negative")
                    return {"success": False, "error": error_result["error"]}

            async def handle_tab_select(tab_name: str):
                try:
                    async with asyncio.timeout(1):
                        client_state = await core.get_client_state(session_token, username)
                        client_state["selected_tab"] = tab_name
                        client_state["timestamp"] = int(time.time())
                        client_state = sanitize_state(client_state)
                        if len(json.dumps(client_state).encode()) > 1048576:
                            logger.error(f"Kích thước client_state vượt quá 1MB cho {username}")
                            ui.notify("Trạng thái phiên quá lớn", type="negative")
                            return
                        await core.save_client_state(session_token, client_state)
                        await core.log_sync_action(
                            table_name="client_states",
                            record_id=hashlib.sha256(session_token.encode()).hexdigest(),
                            action="SELECT_TAB",
                            details={"username": username, "action": "select_tab", "tab_name": tab_name},
                            username=username
                        )
                        ui.update()
                except asyncio.TimeoutError as e:
                    logger.error(f"{username}: Timeout khi chọn tab {tab_name}: {str(e)}", exc_info=True)
                    ui.notify(f"Timeout khi chọn tab: {str(e)}", type="negative")
                except ValueError as ve:
                    logger.warning(f"{username}: Lỗi chọn tab {tab_name}: {str(ve)}")
                    ui.notify(f"Lỗi: {str(ve)}", type="negative")
                except Exception as e:
                    error_result = await handle_error(e, username, f"chọn tab {tab_name}", core)
                    ui.notify(error_result["error"], type="negative")

            # Thêm client_state vào khởi tạo DashboardLayout
            dashboard_layout = DashboardLayout(
                core=core,
                username=username,
                client_state=client_state,  # Thêm client_state
                is_admin=is_admin,
                tabs={name: {"name": tab["name"], "icon": tab["icon"]} for name, tab in ui_manager.registered_tabs.items()},
                on_logout=handle_logout,
                on_tab_select=handle_tab_select
            )
            dashboard_layout.set_ui_manager(ui_manager)
            if "selected_tab" not in client_state or client_state["selected_tab"] not in ui_manager.registered_tabs:
                client_state["selected_tab"] = "Chat" if "Chat" in ui_manager.registered_tabs else list(ui_manager.registered_tabs.keys())[0] if ui_manager.registered_tabs else None
                client_state["timestamp"] = int(time.time())
                client_state = sanitize_state(client_state)
                if len(json.dumps(client_state).encode()) > 1048576:
                    logger.error(f"Kích thước client_state vượt quá 1MB cho {username}")
                    ui.notify("Trạng thái phiên quá lớn", type="negative")
                    return JSONResponse({"error": "Trạng thái phiên quá lớn"}, status_code=200)
                await core.save_client_state(session_token, client_state)
                await core.log_sync_action(
                    table_name="client_states",
                    record_id=hashlib.sha256(session_token.encode()).hexdigest(),
                    action="SET_DEFAULT_TAB",
                    details={"username": username, "action": "set_default_tab", "tab_name": client_state["selected_tab"]},
                    username=username
                )
            start_time = time.time()
            await dashboard_layout.render(client_state)
            logger.info(f"{username}: DashboardLayout.render took {time.time() - start_time} seconds")
            return  # NiceGUI xử lý phản hồi
    except ValueError as ve:
        logger.warning(f"{username}: Lỗi trong dashboard: {str(ve)}, chuyển hướng về /auth")
        response = RedirectResponse(url="/auth?error=Vui+lòng+đăng+nhập", status_code=302)
        response.delete_cookie("session_token", path="/", samesite="Lax", secure=Config.SECURE_COOKIES)
        response.delete_cookie("username", path="/", samesite="Lax", secure=Config.SECURE_COOKIES)
        return response
    except asyncio.TimeoutError:
        logger.error(f"{username}: Timeout trong dashboard", exc_info=True)
        ui.notify("Hết thời gian tải trang, vui lòng thử lại!", type="negative")
        return JSONResponse({"error": "Hết thời gian tải trang"}, status_code=200)
    except Exception as e:
        error_result = await handle_error(e, username, "xử lý dashboard", core)
        ui.notify(f"Lỗi hệ thống: {error_result['error']}", type="negative")
        return JSONResponse({"error": "Lỗi hệ thống"}, status_code=200)
        
@fastapi_app.post("/api/login")
async def api_login(data: LoginData, response: Response, request: Request):
    try:
        logger.info(f"Thử đăng nhập với username={data.username}")
        # Xóa cookie phiên cũ
        existing_session = request.cookies.get("session_token")
        if existing_session:
            logger.warning(f"Xóa phiên cũ: session_token={existing_session[:10]}..., username={request.cookies.get('username', '')}")
            await core.clear_client_state(existing_session, request.cookies.get("username", ""), log_sync=True)
            response.delete_cookie("session_token", path="/", samesite="Lax", secure=Config.SECURE_COOKIES)
            response.delete_cookie("username", path="/", samesite="Lax", secure=Config.SECURE_COOKIES)
        validation_result = await validate_user_input(data.dict())
        if not validation_result["success"]:
            logger.warning(f"Đăng nhập thất bại: {validation_result['error']}")
            return JSONResponse({"error": validation_result["error"]}, status_code=400)
        client_state = await core.get_client_state("", data.username)
        if client_state.get("login_attempts", 0) >= Config.MAX_LOGIN_ATTEMPTS:
            logger.warning(f"Quá số lần đăng nhập cho phép cho {data.username}")
            return JSONResponse({"error": "Tài khoản bị khóa do quá số lần đăng nhập"}, status_code=429)
        async with asyncio.timeout(60):
            result = await core.authenticate_user(data.username, data.password, data.bot_password)
            if result.get("success"):
                session_token = result["session_token"]
                state_result = await update_and_save_client_state(
                    core, session_token, data.username, core.firestore_handler.firestore_available
                )
                if not state_result["success"]:
                    return JSONResponse({"error": state_result["error"]}, status_code=400)
                client_state["login_attempts"] = 0
                await core.save_client_state(session_token, client_state)
                set_auth_cookies(response, session_token, data.username)
                logger.info(f"Đăng nhập thành công cho {data.username}, session_token={session_token[:10]}...")
                return JSONResponse({"success": "Đăng nhập thành công", "redirect": "/dashboard"})
            logger.warning(f"Đăng nhập thất bại cho {data.username}: {result.get('error')}")
            client_state["login_attempts"] = client_state.get("login_attempts", 0) + 1
            client_state = sanitize_state(client_state)
            if len(json.dumps(client_state).encode()) > 1048576:
                logger.error(f"Kích thước client_state vượt quá 1MB cho {data.username}")
                return JSONResponse({"error": "Trạng thái phiên quá lớn"}, status_code=400)
            await core.save_client_state("", client_state)
            return JSONResponse({"error": result.get("error", "Thông tin đăng nhập không đúng")}, status_code=401)
    except asyncio.TimeoutError as e:
        logger.error(f"Timeout khi đăng nhập: {str(e)}", exc_info=True)
        return JSONResponse({"error": f"Timeout khi đăng nhập: {str(e)}"}, status_code=504)
    except ValueError as ve:
        logger.warning(f"Lỗi đăng nhập: {str(ve)}")
        return JSONResponse({"error": str(ve)}, status_code=400)
    except Exception as e:
        error_result = await handle_error(e, data.username, "đăng nhập", core)
        return JSONResponse({"error": error_result["error"]}, status_code=error_result["status_code"])

@fastapi_app.post("/api/register")
async def api_register(data: RegisterData, response: Response, request: Request):
    try:
        logger.info(f"Thử đăng ký với username={data.username}")
        # Xóa cookie phiên cũ
        existing_session = request.cookies.get("session_token")
        if existing_session:
            logger.warning(f"Xóa phiên cũ: session_token={existing_session[:10]}..., username={request.cookies.get('username', '')}")
            await core.clear_client_state(existing_session, request.cookies.get("username", ""), log_sync=True)
            response.delete_cookie("session_token", path="/", samesite="Lax", secure=Config.SECURE_COOKIES)
            response.delete_cookie("username", path="/", samesite="Lax", secure=Config.SECURE_COOKIES)
        validation_result = await validate_user_input(data.dict(), is_register=True)
        if not validation_result["success"]:
            logger.warning(f"Đăng ký thất bại: {validation_result['error']}")
            return JSONResponse({"error": validation_result["error"]}, status_code=400)
        async with asyncio.timeout(60):
            result = await core.register_user(data.username, data.password, data.bot_password)
            if result.get("success"):
                session_token = result["session_token"]
                state_result = await update_and_save_client_state(
                    core, session_token, data.username, core.firestore_handler.firestore_available
                )
                if not state_result["success"]:
                    return JSONResponse({"error": state_result["error"]}, status_code=400)
                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                    created_at = int(time.time())
                    await conn.execute(
                        "INSERT INTO sync_log (id, table_name, record_id, action, timestamp, details) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            str(uuid.uuid4()),
                            "users",
                            hashlib.sha256(data.username.encode()).hexdigest(),
                            "SYNC",
                            created_at,
                            json.dumps({"username": data.username, "action": "register"})
                        )
                    )
                    await conn.commit()
                set_auth_cookies(response, session_token, data.username)
                logger.info(f"Đăng ký thành công cho {data.username}, session_token={session_token[:10]}...")
                return JSONResponse({"success": "Đăng ký thành công", "redirect": "/dashboard"})
            return JSONResponse({"error": result["error"]}, status_code=400)
    except asyncio.TimeoutError as e:
        logger.error(f"Timeout khi đăng ký: {str(e)}", exc_info=True)
        return JSONResponse({"error": f"Timeout khi đăng ký: {str(e)}"}, status_code=504)
    except ValueError as ve:
        logger.warning(f"Lỗi đăng ký: {str(ve)}")
        return JSONResponse({"error": str(ve)}, status_code=400)
    except Exception as e:
        error_result = await handle_error(e, data.username, "đăng ký", core)
        return JSONResponse({"error": error_result["error"]}, status_code=error_result["status_code"])


async def update_and_save_client_state(core: Core, session_token: str, username: str, firestore_available: bool) -> dict:
    
    try:
        async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
            # Kiểm tra session
            async with conn.execute(
                "SELECT username FROM sessions WHERE session_token = ?",
                (session_token,)
            ) as cursor:
                session_row = await cursor.fetchone()
                if session_row and session_row[0] != username:
                    logger.warning(f"Username mismatch: expected {username}, got {session_row[0]} in sessions")
                    username = session_row[0]

            # Lấy role từ bảng users
            async with conn.execute(
                "SELECT role FROM users WHERE username = ?",
                (username,)
            ) as cursor:
                row = await cursor.fetchone()
                role = row[0] if row else "user"

            # Lấy client_state hiện tại
            client_state = await core.get_client_state(session_token, username)
            if client_state.get("username") and client_state["username"] != username:
                logger.warning(f"Username mismatch: client_state={client_state['username']}, expected={username}")
                client_state["username"] = username

            # Lấy language từ client_state hoặc app.storage.user
            language = client_state.get("language", app.storage.user.get("language", "vi"))

            client_state.update({
                "authenticated": True,
                "session_token": session_token,
                "username": username,
                "firestore_available": firestore_available,
                "timestamp": int(time.time()),
                "role": role,
                "language": language,
                "login_attempts": client_state.get("login_attempts", 0),
                "selected_tab": client_state.get("selected_tab", "Chat"),
                "registered_tabs": client_state.get("registered_tabs", [])
            })
            sanitized_state = sanitize_state(client_state)
            if len(json.dumps(sanitized_state).encode()) > 1_048_576:
                logger.error(f"Kích thước client_state vượt quá 1MB cho {username}")
                return {"success": False, "error": "Trạng thái phiên quá lớn"}
            await core.save_client_state(session_token, sanitized_state)
            app.storage.user["language"] = sanitized_state["language"]
            logger.info(f"Đã lưu client_state với language={sanitized_state['language']} cho {username}")
            return {"success": True, "client_state": sanitized_state}
    except Exception as e:
        logger.error(f"Lỗi khi lưu client_state cho {username}: {str(e)}", exc_info=True)
        return {"success": False, "error": str(e)}
        