from nicegui import ui, app
import re
import asyncio
import aiosqlite
import json
import time
import traceback
import hashlib
import uuid
from typing import Dict, Callable, Optional
from core import Core
from utils.logging import get_logger
from utils.core_common import check_disk_space
from config import Config
from uiapp.components.header import HeaderComponent
from uiapp.components.sidebar import SidebarComponent

logger = get_logger("Dashboard")

class DashboardLayout:
    def __init__(
        self,
        username: str,
        is_admin: bool,
        tabs: Dict[str, Dict],
        core: Core,
        on_logout: Callable,
        on_tab_select: Optional[Callable] = None
    ):
        if not hasattr(core, 'sqlite_handler') or not hasattr(core, 'firestore_handler'):
            raise ValueError("Lỗi: Đối tượng core không hợp lệ, thiếu sqlite_handler hoặc firestore_handler")
        if not isinstance(username, str) or not re.match(r'^[a-zA-Z0-9_]+$', username):
            logger.error(f"username phải là chuỗi hợp lệ, nhận được: {type(username)}")
            raise ValueError("Lỗi: Username không hợp lệ")
        self.username = username
        self.is_admin = is_admin
        self.tabs = tabs
        self.core = core
        self.on_logout = on_logout
        self.on_tab_select = on_tab_select
        self.ui_manager = None
        self.client_state = {}

    def set_ui_manager(self, ui_manager):
        self.ui_manager = ui_manager
        logger.debug(f"{self.username}: UIManager đã được gán cho DashboardLayout")

    async def render(self, client_state: Dict):
        logger.debug(f"{self.username}: render() được gọi với client_state: {client_state}")
        if not isinstance(client_state, dict):
            logger.error(f"{self.username}: client_state không phải là dictionary")
            ui.notify("Lỗi: Trạng thái phiên không hợp lệ", type="negative")
            return
        
        try:
            async with asyncio.timeout(120):
                check_disk_space()
                self.client_state = client_state.copy() if client_state else {}
                self.client_state = {k: v for k, v in self.client_state.items() if isinstance(v, (str, int, float, bool, list, dict, type(None)))}
                state_json = json.dumps(self.client_state, ensure_ascii=False)
                if len(state_json.encode()) > 1_000_000:
                    logger.error(f"{self.username}: Kích thước trạng thái vượt quá 1MB")
                    ui.notify("Lỗi: Trạng thái phiên quá lớn", type="negative")
                    return

                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                    async with conn.execute(
                        "SELECT state FROM client_states WHERE username = ?",
                        (self.username,)
                    ) as cursor:
                        row = await cursor.fetchone()
                        session_token = self.client_state.get("session_token", "")
                        state_id = hashlib.sha256(f"{self.username}_{session_token}".encode()).hexdigest()
                        self.client_state["timestamp"] = int(time.time())
                        state_json = json.dumps(self.client_state, ensure_ascii=False)

                        if not row:
                            logger.info(f"{self.username}: Không tìm thấy client_state, tạo mới")
                            self.client_state["selected_tab"] = (
                                "Chat" if "Chat" in self.ui_manager.registered_tabs
                                else list(self.ui_manager.registered_tabs.keys())[0]
                                if self.ui_manager.registered_tabs else None
                            )
                        else:
                            logger.info(f"{self.username}: Tải client_state từ SQLite")
                            current_state = json.loads(row[0])
                            self.client_state.update(current_state)

                        if self.ui_manager and self.ui_manager.registered_tabs:
                            self.tabs = {
                                name: {
                                    "name": tab["name"],
                                    "icon": tab["icon"],
                                    "update_func_name": tab["update"].__name__ if callable(tab.get("update")) else None
                                }
                                for name, tab in self.ui_manager.registered_tabs.items()
                            }
                            self.client_state["tabs"] = {
                                name: {
                                    "name": tab["name"],
                                    "icon": tab["icon"],
                                    "update_func_name": tab["update_func_name"]
                                }
                                for name, tab in self.tabs.items()
                            }
                            logger.info(f"{self.username}: Đã cập nhật client_state['tabs'] với tabs: {list(self.tabs.keys())}")
                            # Log thêm để kiểm tra update_func
                            for tab_name in self.tabs:
                                update_func = self.ui_manager.registered_tabs.get(tab_name, {}).get("update")
                                logger.debug(f"{self.username}: Tab {tab_name} có update_func: {update_func is not None}")

                        await conn.execute(
                            "INSERT OR REPLACE INTO client_states (id, username, session_token, state, timestamp) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (state_id, self.username, session_token, state_json, self.client_state["timestamp"])
                        )
                        await conn.commit()

                    if self.is_admin:
                        async with conn.execute(
                            "SELECT action, details, timestamp FROM sync_log ORDER BY timestamp DESC LIMIT 10"
                        ) as cursor:
                            status = await cursor.fetchall()
                            logger.info(f"{self.username}: Tải trạng thái đồng bộ cho admin")

                    if not self.ui_manager:
                        logger.error(f"{self.username}: UIManager chưa được gán cho DashboardLayout")
                        ui.notify("Lỗi: Cấu hình dashboard không hợp lệ", type="negative")
                        return

                    header = HeaderComponent(
                        username=self.username,
                        on_logout=self.on_logout,
                        is_admin=self.is_admin,
                        on_sync_to_sqlite=self.core.sync_to_sqlite,
                        on_sync_from_sqlite=self.core.sync_from_sqlite,
                        core=self.core,
                        client_state=self.client_state,
                        ui_manager=self.ui_manager
                    )
                    await header.render()
                    self.sidebar = SidebarComponent(
                        tabs=[{"name": tab["name"], "icon": tab["icon"]} for tab in self.tabs.values()],
                        on_select=self.handle_tab_change,
                        core=self.core,
                        client_state=self.client_state
                    )
                    await self.sidebar.render()
                    with ui.card().classes('w-full p-4'):
                        if not self.tabs:
                            ui.label("Chào mừng đến với Dashboard").classes("text-2xl font-bold")
                            ui.label(
                                "Hiện tại không có tab nào được cấu hình. Vui lòng thêm các tab trong thư mục uiapp."
                            ).classes("text-lg text-gray-500")
                            logger.info(f"{self.username}: Hiển thị nội dung mặc định vì không có tab")
                        else:
                            with ui.tabs().classes('dense w-full') as tabs:
                                for tab_name, tab_info in self.tabs.items():
                                    if len(tab_name) < 3 or not re.match(r'^[a-zA-Z0-9_]+$', tab_name):
                                        logger.error(f"{self.username}: Tên tab không hợp lệ: {tab_name}")
                                        continue
                                    if not isinstance(tab_info, dict) or "icon" not in tab_info or "name" not in tab_info:
                                        logger.error(f"{self.username}: Cấu hình tab không hợp lệ: {tab_name}")
                                        continue
                                    ui.tab(tab_name, icon=tab_info["icon"]).classes('no-caps')
                            with ui.tab_panels(
                                tabs,
                                value=self.client_state.get(
                                    "selected_tab",
                                    "Chat" if "Chat" in self.tabs else list(self.tabs.keys())[0] if self.tabs else None
                                )
                            ).classes('w-full') as tab_panels:
                                tab_panels.bind_value(self.client_state, "selected_tab")
                                for tab_name, tab_info in self.tabs.items():
                                    with ui.tab_panel(tab_name):
                                        try:
                                            render_func = self.ui_manager.registered_tabs.get(tab_name, {}).get("render")
                                            if not render_func or not asyncio.iscoroutinefunction(render_func):
                                                logger.error(
                                                    f"{self.username}: render_func của tab {tab_name} không hợp lệ "
                                                    "hoặc không phải async"
                                                )
                                                ui.notify(f"Lỗi: Không thể tải tab {tab_name}", type="negative")
                                                continue
                                            logger.debug(f"{self.username}: Rendering tab {tab_name}")
                                            await render_func(self.core, self.username, self.is_admin, self.client_state)
                                            update_func = self.ui_manager.registered_tabs.get(tab_name, {}).get("update")
                                            if update_func and callable(update_func):
                                                if asyncio.iscoroutinefunction(update_func):
                                                    await update_func(self.core, self.username, self.is_admin, self.client_state)
                                                else:
                                                    update_func(self.core, self.username, self.is_admin, self.client_state)
                                        except Exception as e:
                                            error_msg = f"Lỗi render tab {tab_name}: {str(e)}"
                                            if self.is_admin:
                                                error_msg += f"\nChi tiết: {traceback.format_exc()}"
                                            ui.notify(error_msg, type="negative")
                                            logger.error(f"{self.username}: {error_msg}", exc_info=True)
        except asyncio.TimeoutError as e:
            logger.error(f"{self.username}: Timeout khi render dashboard: {str(e)}", exc_info=True)
            ui.notify("Hết thời gian tải dashboard, vui lòng thử lại!", type="negative")
        except TypeError as e:
            logger.error(f"{self.username}: Lỗi JSON khi render dashboard: {str(e)}", exc_info=True)
            ui.notify(f"Lỗi dữ liệu phiên: {str(e)}", type="negative")
        except Exception as e:
            error_msg = f"Lỗi hiển thị dashboard: {str(e)}"
            if self.is_admin:
                error_msg += f"\nChi tiết: {traceback.format_exc()}"
            ui.notify(error_msg, type="negative")
            logger.error(f"{self.username}: {error_msg}", exc_info=True)

    async def handle_tab_change(self, tab_name):
        try:
            async with asyncio.timeout(30):
                if not tab_name or not re.match(r'^[a-zA-Z0-9_]+$', tab_name):
                    logger.error(f"{self.username}: Tên tab không hợp lệ: {tab_name}")
                    ui.notify("Lỗi: Tên tab không hợp lệ", type="negative")
                    return
                if not self.client_state or "session_token" not in self.client_state:
                    logger.error(f"{self.username}: client_state không hợp lệ hoặc thiếu session_token")
                    ui.notify("Lỗi: Trạng thái phiên không hợp lệ", type="negative")
                    return
                session_token = self.client_state.get("session_token", "")
                self.client_state["selected_tab"] = tab_name
                self.client_state["timestamp"] = int(time.time())
                clean_state = {k: v for k, v in self.client_state.items() if isinstance(v, (str, int, float, bool, list, dict, type(None)))}
                state_json = json.dumps(clean_state, ensure_ascii=False)
                if len(state_json.encode()) > 1_000_000:
                    logger.error(f"{self.username}: Kích thước trạng thái vượt quá 1MB")
                    ui.notify("Lỗi: Trạng thái phiên quá lớn", type="negative")
                    return
                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                    state_id = hashlib.sha256(f"{self.username}_{session_token}".encode()).hexdigest()
                    await conn.execute(
                        "INSERT OR REPLACE INTO client_states (id, username, session_token, state, timestamp) VALUES (?, ?, ?, ?, ?)",
                        (state_id, self.username, session_token, state_json, clean_state["timestamp"])
                    )
                    await conn.execute(
                        "INSERT INTO sync_log (id, table_name, record_id, action, timestamp, details) VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            str(uuid.uuid4()),
                            "client_states",
                            state_id,
                            "SELECT_TAB",
                            int(time.time()),
                            json.dumps({"username": self.username, "tab": tab_name})
                        )
                    )
                    await conn.commit()
                if self.on_tab_select and callable(self.on_tab_select):
                    if asyncio.iscoroutinefunction(self.on_tab_select):
                        await self.on_tab_select(tab_name)
                    else:
                        self.on_tab_select(tab_name)
                logger.info(f"{self.username}: Lưu selected_tab={tab_name} vào client_states")
        except asyncio.TimeoutError:
            ui.notify("Hết thời gian xử lý thay đổi tab", type="negative")
            logger.error(f"{self.username}: Timeout khi xử lý thay đổi tab", exc_info=True)
        except TypeError as e:
            logger.error(f"{self.username}: Lỗi JSON khi lưu selected_tab: {str(e)}", exc_info=True)
            ui.notify(f"Lỗi dữ liệu phiên: {str(e)}", type="negative")
        except Exception as e:
            error_msg = f"Lỗi xử lý thay đổi tab: {str(e)}"
            if self.is_admin:
                error_msg += f"\nChi tiết: {traceback.format_exc()}"
            ui.notify(error_msg, type="negative")
            logger.error(f"{self.username}: {error_msg}", exc_info=True)