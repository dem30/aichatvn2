from nicegui import ui
from typing import List, Dict, Callable, Optional
from utils.logging import get_logger
import asyncio
import re
import traceback
from .button import ButtonComponent
from config import Config
import aiosqlite
import uuid
import json
import time
import hashlib

logger = get_logger("SidebarComponent")

class SidebarComponent:
    def __init__(
        self,
        tabs: List[Dict],
        on_select: Callable,
        active_tab: Optional[str] = None,
        classes: str = "w-64 bg-gray-100 p-4 h-screen",
        core: Optional['Core'] = None,
        client_state: Optional[Dict] = None
    ):
        self.tabs = tabs
        self.on_select = on_select
        self.active_tab = active_tab
        self.classes = classes
        self.loading = False
        self.core = core
        self.client_state = client_state or {}
        self.button_elements = {}  # Lưu trữ các ui.button theo tên tab

    async def render(self):
        logger.debug(f"Rendering SidebarComponent with tabs={self.tabs}, user={self.client_state.get('username', 'unknown')}")
        if not callable(self.on_select):
            ui.notify("Lỗi: Hành động chọn tab không hợp lệ", type="negative")
            logger.error("Hành động chọn tab không hợp lệ")
            return
        try:
            # Tạo drawer, không sử dụng value để tránh lỗi
            drawer = ui.left_drawer(fixed=False).classes(self.classes)
            ui.button(icon='menu').on('click', lambda: drawer.toggle()).classes('m-2')
            with drawer:
                ui.label("Menu").classes("text-xl font-bold mb-4")
                if self.loading:
                    ui.linear_progress(show_value=False).classes('w-full')
                if not self.tabs:
                    ui.label("Không có tab nào được cấu hình").classes("text-gray-500")
                    logger.info("Hiển thị sidebar rỗng vì không có tab")
                    return
                for tab in self.tabs:
                    logger.debug(f"Rendering button for tab {tab['name']}")
                    if len(tab["name"]) < 3 or not re.match(r'^[a-zA-Z0-9_]+$', tab["name"]):
                        ui.notify(f"Tên tab '{tab['name']}' không hợp lệ", type="negative")
                        logger.error(f"Tên tab '{tab['name']}' không hợp lệ")
                        continue
                    with ui.element("div").classes("mb-2"):
                        async def select_tab(t=tab["name"]):
                            logger.debug(f"select_tab called for tab {t} by user {self.client_state.get('username', 'unknown')}")
                            if self.loading:
                                logger.warning(f"select_tab skipped due to loading=True for tab {t}")
                                return
                            self.loading = True
                            try:
                                async with asyncio.timeout(60):
                                    if self.core and self.client_state:
                                        if not self.client_state.get("session_token"):
                                            ui.notify("Lỗi: Phiên không hợp lệ", type="negative")
                                            logger.error(f"session_token không tồn tại")
                                            return
                                        # Làm sạch client_state trước khi kiểm tra kích thước
                                        clean_state = {k: v for k, v in self.client_state.items() if isinstance(v, (str, int, float, bool, list, dict, type(None)))}
                                        if len(json.dumps(clean_state).encode()) > 1048576:
                                            ui.notify("Trạng thái phiên quá lớn", type="negative")
                                            logger.error(f"Kích thước client_state vượt quá 1MB cho {self.client_state.get('username', '')}")
                                            return
                                        self.client_state["selected_tab"] = t
                                        await self.core.save_client_state(self.client_state.get("session_token", ""), clean_state)
                                        await self.core.log_sync_action(
                                            table_name="client_states",
                                            record_id=hashlib.sha256(self.client_state["session_token"].encode()).hexdigest(),
                                            action="SELECT_TAB",
                                            details={"username": self.client_state.get("username", ""), "tab": t},
                                            username=self.client_state.get("username", "")
                                        )
                                        logger.debug(f"{self.client_state.get('username', '')}: Saved client_state to SQLite: selected_tab={t}")
                                    if asyncio.iscoroutinefunction(self.on_select):
                                        await self.on_select(t)
                                    else:
                                        self.on_select(t)
                                    self.active_tab = t
                                    # Cập nhật lớp CSS cho các nút
                                    for tab_name, button in self.button_elements.items():
                                        new_classes = "w-full text-left bg-blue-600 text-white" if tab_name == t else "w-full text-left"
                                        button.classes(remove="w-full text-left bg-blue-600 text-white", add=new_classes)
                                    ui.update()
                                    logger.info(f"{self.client_state.get('username', '')}: Tab changed via sidebar: selected_tab={self.client_state.get('selected_tab')}, button_elements={list(self.button_elements.keys())}")
                            except asyncio.TimeoutError as e:
                                error_msg = f"Timeout khi chọn tab {t}: {str(e)}"
                                ui.notify(error_msg, type="negative")
                                logger.error(error_msg, exc_info=True)
                            except TypeError as e:
                                error_msg = f"Lỗi JSON khi chọn tab {t}: {str(e)}"
                                ui.notify(error_msg, type="negative")
                                logger.error(error_msg, exc_info=True)
                            except Exception as e:
                                error_msg = f"Lỗi chọn tab {t}: {str(e)}"
                                if self.core and self.client_state and await self.core.sqlite_handler.has_permission(self.client_state.get("username", ""), "admin_access"):
                                    error_msg += f"\nChi tiết: {traceback.format_exc()}"
                                ui.notify(error_msg, type="negative")
                                logger.error(f"Lỗi chọn tab {t}: {str(e)}", exc_info=True)
                            finally:
                                self.loading = False
                                ui.update()
                        classes = "w-full text-left bg-blue-600 text-white" if tab["name"] == self.client_state.get("selected_tab", self.active_tab) else "w-full text-left"
                        btn = ButtonComponent(
                            label=tab["name"],
                            on_click=select_tab,
                            icon=tab.get("icon", "extension"),
                            classes=classes,
                            props="flat",
                            core=self.core,
                            client_state=self.client_state
                        )
                        button = await btn.render()
                        if button:
                            self.button_elements[tab["name"]] = button
                            logger.debug(f"Button created for tab {tab['name']}: {classes}")
        except Exception as e:
            error_msg = f"Lỗi render sidebar: {str(e)}"
            if self.core and self.client_state and await self.core.sqlite_handler.has_permission(self.client_state.get("username", ""), "admin_access"):
                error_msg += f"\nChi tiết: {traceback.format_exc()}"
            ui.notify(error_msg, type="negative")
            logger.error(f"Lỗi render sidebar: {str(e)}", exc_info=True)