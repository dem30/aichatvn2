from nicegui import ui
from typing import Callable, Optional, Dict
from uiapp.components.form import FormComponent
import asyncio
import aiosqlite
import uuid
import json
import time
import hashlib
import traceback
from config import Config
from utils.logging import get_logger
import re

logger = get_logger("AuthLayout")

class AuthLayout:
    def __init__(self, on_login: Callable, on_register: Callable, fields: Dict = None, core: 'Core' = None):
        if core and (not hasattr(core, 'firestore_handler') or not hasattr(core, 'sqlite_handler')):
            raise ValueError("Invalid core object: Missing required attributes (firestore_handler, sqlite_handler)")
        
        self.on_login = on_login
        self.on_register = on_register
        self.core = core
        self.fields = fields  # Sử dụng fields từ tham số đầu vào

    async def render(self, request=None):
        logger.debug("Bắt đầu render AuthLayout")
        try:
            async with asyncio.timeout(30):
                client_state = {}  # Không tạo session mặc định
                logger.debug("Tạo ui.card")
                with ui.card().classes("w-full max-w-md mx-auto p-6 bg-white shadow-md"):
                    logger.debug("Tạo label Đăng nhập/Đăng ký")
                    ui.label("Đăng nhập/Đăng ký").classes("text-3xl text-center mb-4")
                    if request and request.query_params.get("error"):
                        logger.debug(f"Hiển thị lỗi từ query_params: {request.query_params.get('error')}")
                        ui.notify(f"Lỗi: {request.query_params.get('error')}", type="negative")
                    if self.core and not self.core.firestore_handler.firestore_available:
                        logger.debug("Hiển thị thông báo Firestore không khả dụng")
                        ui.notify("Firestore không khả dụng, ứng dụng đang chạy ở chế độ cục bộ!", type="warning", timeout=10000)
                    logger.debug("Tạo tabs")
                    tabs = ui.tabs().classes("dense")
                    with tabs:
                        ui.tab("Đăng nhập")
                        ui.tab("Đăng ký")
                    with ui.tab_panels(tabs, value="Đăng nhập").classes("w-full"):
                        with ui.tab_panel("Đăng nhập"):
                            login_fields = {k: v for k, v in self.fields.items() if k in ["username", "password", "bot_password"]}
                            logger.debug("Tạo FormComponent cho Đăng nhập")
                            login_form = FormComponent(fields=login_fields, submit_label="Đăng nhập", on_submit=self.handle_login, core=self.core, client_state=client_state)
                            await login_form.render()
                        with ui.tab_panel("Đăng ký"):
                            logger.debug("Tạo FormComponent cho Đăng ký")
                            register_form = FormComponent(fields=self.fields, submit_label="Đăng ký", on_submit=self.handle_register, core=self.core, client_state=client_state)
                            await register_form.render()
        except asyncio.TimeoutError:
            logger.error("Timeout khi tải giao diện auth", exc_info=True)
            ui.notify("Hết thời gian tải giao diện, vui lòng thử lại!", type="negative")
        except Exception as e:
            error_msg = f"Lỗi render giao diện auth: {str(e)}"
            logger.error(error_msg, exc_info=True)
            ui.notify("Lỗi hệ thống, vui lòng thử lại!", type="negative")

    async def handle_login(self, data: dict, progress_callback: Optional[Callable] = None) -> Dict:
        logger.info(f"Xử lý đăng nhập với data: {data}")
        try:
            # Kiểm tra kích thước dữ liệu
            for key, value in data.items():
                if value and len(value.encode()) > 1048576:
                    logger.warning(f"Field {key} quá dài, vượt quá 1MB")
                    ui.notify(f"Field {key} quá dài!", type="negative")
                    return {"success": False, "error": f"Field {key} quá dài"}
            if len(json.dumps(data).encode()) > 1048576:
                logger.warning("Dữ liệu đăng nhập quá lớn, vượt quá 1MB")
                ui.notify("Dữ liệu đăng nhập quá lớn!", type="negative")
                return {"success": False, "error": "Dữ liệu đăng nhập quá lớn"}
            
            async with asyncio.timeout(60):  # Timeout 1 phút
                result = await self.on_login(data, progress_callback=progress_callback)
                #if result.get("success"):
                #    ui.notify("Đăng nhập thành công!", type="positive")
                #else:
                #    ui.notify(result.get("error", "Đăng nhập thất bại"), type="negative")
                return result
        except asyncio.TimeoutError as e:
            logger.error(f"Timeout khi đăng nhập: {str(e)}", exc_info=True)
            ui.notify(f"Timeout khi đăng nhập: {str(e)}", type="negative")
            return {"success": False, "error": f"Timeout khi đăng nhập: {str(e)}"}
        except Exception as e:
            logger.error(f"Lỗi đăng nhập: {str(e)}", exc_info=True)
            ui.notify(f"Lỗi: {str(e)}", type="negative")
            return {"success": False, "error": str(e)}

    async def handle_register(self, data: dict, progress_callback: Optional[Callable] = None) -> Dict:
        logger.info(f"Xử lý đăng ký với data: {data}")
        try:
            # Kiểm tra kích thước dữ liệu
            for key, value in data.items():
                if value and len(value.encode()) > 1048576:
                    logger.warning(f"Field {key} quá dài, vượt quá 1MB")
                    ui.notify(f"Field {key} quá dài!", type="negative")
                    return {"success": False, "error": f"Field {key} quá dài"}
            if len(json.dumps(data).encode()) > 1048576:
                logger.warning("Dữ liệu đăng ký quá lớn, vượt quá 1MB")
                ui.notify("Dữ liệu đăng ký quá lớn!", type="negative")
                return {"success": False, "error": "Dữ liệu đăng ký quá lớn"}
            
            async with asyncio.timeout(60):  # Timeout 1 phút
                result = await self.on_register(data, progress_callback=progress_callback)
                if result.get("success") and self.core and self.core.firestore_handler.firestore_available:
                    username = data["username"]
                    async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                        await conn.execute(
                            "INSERT INTO sync_log "
                            "(id, table_name, record_id, action, timestamp, details) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                str(uuid.uuid4()),
                                "users",
                                hashlib.sha256(username.encode()).hexdigest(),
                                "SYNC",
                                int(time.time()),
                                json.dumps({"username": username, "action": "register"})
                            )
                        )
                        await conn.commit()
                    ui.notify("Đăng ký thành công!", type="positive")
                else:
                    ui.notify(result.get("error", "Đăng ký thất bại"), type="negative")
                return result
        except asyncio.TimeoutError as e:
            logger.error(f"Timeout khi đăng ký: {str(e)}", exc_info=True)
            ui.notify(f"Timeout khi đăng ký: {str(e)}", type="negative")
            return {"success": False, "error": f"Timeout khi đăng ký: {str(e)}"}
        except Exception as e:
            logger.error(f"Lỗi đăng ký: {str(e)}", exc_info=True)
            ui.notify(f"Lỗi: {str(e)}", type="negative")
            return {"success": False, "error": str(e)}
