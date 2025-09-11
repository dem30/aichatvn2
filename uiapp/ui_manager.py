# ui_manager.py
from typing import Callable, Dict, Any
from nicegui import ui
from uiapp.layouts.auth import AuthLayout
from uiapp.layouts.dashboard import DashboardLayout
from utils.logging import get_logger
from core import Core
import traceback
import re
import json
import aiosqlite
import time
import hashlib
from config import Config
from pathlib import Path
import sys
from importlib import import_module
from importlib.util import spec_from_file_location, module_from_spec

logger = get_logger("UIManager")

class UIManager:
    def __init__(self, core: Core):
        self.core = core
        self.registered_tabs: Dict[str, Any] = {}

    def register_tab(self, name: str, render_func: Callable, update_func: Callable, icon: str = "extension"):
        if len(name) < 3 or len(name) > 100 or not re.match(r'^[a-zA-Z0-9_]+$', name):
            logger.error(f"Tên tab {name} không hợp lệ, phải dài từ 3-100 ký tự và chỉ chứa a-z, A-Z, 0-9, _")
            return
        if name in self.registered_tabs:
            logger.warning(f"Tab {name} đã được đăng ký, bỏ qua")
            return
        if not callable(render_func) or not callable(update_func):
            logger.error(f"render_func hoặc update_func không hợp lệ cho tab {name}")
            return
        self.registered_tabs[name] = {
            "name": name,
            "label": name,
            "icon": icon or "extension",
            "render": render_func,
            "update": update_func
        }
        logger.info(f"Đã đăng ký tab: {name}")

    async def render_auth(self, on_login: Callable, on_register: Callable, fields: Dict[str, Any] = None):
        try:
            if not self.core:
                logger.error("Core không được khởi tạo trong UIManager")
                ui.notify("Lỗi hệ thống: Core không được khởi tạo", type="negative")
                return
            if not fields:
                logger.warning("Fields không được cung cấp cho render_auth, sử dụng mặc định")
                fields = {
                    "username": {
                        "label": "Tên người dùng",
                        "type": "text",
                        "hint": "Tối thiểu 3 ký tự, chỉ chứa chữ, số, dấu gạch dưới",
                        "validation": {
                            "required": "bool(x) or 'Trường này là bắt buộc'",
                            "min_length": "len(x or '') >= 3 or 'Tối thiểu 3 ký tự'",
                            "pattern": "re.match(r'^[a-zA-Z0-9_]+$', x or '') or 'Chỉ chứa chữ, số, dấu gạch dưới'"
                        }
                    },
                    "password": {
                        "label": "Mật khẩu",
                        "type": "password",
                        "hint": "Tối thiểu 8 ký tự",
                        "validation": {
                            "required": "bool(x) or 'Trường này là bắt buộc'",
                            "min_length": "len(x or '') >= 8 or 'Tối thiểu 8 ký tự'"
                        }
                    },
                    "confirm_password": {
                        "label": "Xác nhận mật khẩu",
                        "type": "password",
                        "validation": {
                            "required": "bool(x) or 'Trường này là bắt buộc'"
                        }
                    },
                    "bot_password": {
                        "label": "Mật khẩu bot",
                        "type": "password",
                        "validation": {
                            "required": "bool(x) or 'Mật khẩu bot là bắt buộc'",
                            "min_length": "len(x or '') >= 8 or 'Tối thiểu 8 ký tự'"
                        }
                    }
                }
            auth_layout = AuthLayout(on_login, on_register, fields, core=self.core)
            await auth_layout.render()
        except ValueError as ve:
            error_msg = f"Lỗi render auth: {str(ve)}"
            logger.error(error_msg, exc_info=True)
            ui.notify(error_msg, type="negative")
        except Exception as e:
            error_msg = f"Lỗi hệ thống khi render auth: {str(e)}"
            if await self.core.sqlite_handler.has_permission("", "admin_access"):
                error_msg += f"\nChi tiết: {traceback.format_exc()}"
            logger.error(error_msg, exc_info=True)
            ui.notify(error_msg, type="negative")

    async def render_dashboard(
        self,
        username: str,
        is_admin: bool,
        client_state: Dict[str, Any],
        on_logout: Callable,
        on_tab_select: Callable = None
    ):
        try:
            if not self.core:
                logger.error("Core không được khởi tạo trong UIManager")
                ui.notify("Lỗi hệ thống: Core không được khởi tạo", type="negative")
                return
            # Tải lại tab nếu rỗng hoặc thiếu tab Chat
            if not self.registered_tabs or "Chat" not in self.registered_tabs:
                await load_tabs(self, self.core, username, client_state)
                if not self.registered_tabs:
                    logger.warning(f"{username}: Không có tab nào được đăng ký sau khi gọi load_tabs")
                    ui.notify("Không có tab nào được tải, vui lòng kiểm tra cấu hình tab!", type="warning")
                    return
            logger.info(f"{username}: Rendering dashboard với tabs: {list(self.registered_tabs.keys())}")
            if client_state.get("session_token"):
                if not re.match(r'^[a-zA-Z0-9_-]{32,}$', client_state["session_token"]):
                    logger.error(f"session_token không hợp lệ cho {username}")
                    ui.notify("Phiên không hợp lệ", type="negative")
                    return
                client_state["registered_tabs"] = list(self.registered_tabs.keys())
                async with asyncio.timeout(30):
                    await self.core.save_client_state(client_state["session_token"], client_state)
                    await self.core.log_sync_action(
                        table_name="client_states",
                        record_id=hashlib.sha256(client_state["session_token"].encode()).hexdigest(),
                        action="SAVE_STATE",
                        details={"username": username, "action": "save_client_state", "tabs": client_state["registered_tabs"]},
                        username=username
                    )
            else:
                logger.warning(f"Không thể lưu client_state cho {username}: thiếu session_token")
            dashboard_layout = DashboardLayout(
                username=username,
                is_admin=is_admin,
                tabs={name: {"name": tab["name"], "icon": tab["icon"]} for name, tab in self.registered_tabs.items()},
                core=self.core,
                on_logout=on_logout,
                on_tab_select=on_tab_select
            )
            dashboard_layout.set_ui_manager(self)
            await dashboard_layout.render(client_state)
        except asyncio.TimeoutError as e:
            error_msg = f"Timeout khi render dashboard cho {username}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            ui.notify("Hết thời gian tải dashboard, vui lòng thử lại!", type="negative")
            raise
        except ValueError as ve:
            error_msg = f"Lỗi render dashboard cho {username}: {str(ve)}"
            logger.error(error_msg, exc_info=True)
            ui.notify(error_msg, type="negative")
            raise
        except Exception as e:
            error_msg = f"Lỗi hệ thống khi render dashboard cho {username}: {str(e)}"
            if await self.core.sqlite_handler.has_permission(username, "admin_access"):
                error_msg += f"\nChi tiết: {traceback.format_exc()}"
            logger.error(error_msg, exc_info=True)
            ui.notify(error_msg, type="negative")
            raise
            