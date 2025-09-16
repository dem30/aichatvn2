
from nicegui import ui, context, app
import mimetypes
import os
import json
import time
import asyncio
import logging
import inspect
from typing import Callable, Optional, List, Dict
from utils.logging import get_logger
from utils.core_common import check_last_sync
import aiosqlite
import uuid
from config import Config
from .button import ButtonComponent
from PIL import Image
import io
from tenacity import retry, stop_after_attempt, wait_fixed

logger = get_logger("HeaderComponent")

@retry(stop=stop_after_attempt(2), wait=wait_fixed(1.0))
async def safe_ui_update():
    logger.debug("Thử gọi safe_ui_update")
    try:
        if context.client.has_socket_connection:
            ui.update()
            logger.debug("Cập nhật giao diện thành công")
        else:
            logger.warning("Không thể cập nhật giao diện vì client đã disconnect")
    except Exception as e:
        logger.error(f"Lỗi khi cập nhật giao diện: {str(e)}", exc_info=True)

class HeaderComponent:
    def __init__(
        self,
        username: str,
        on_logout: Callable,
        is_admin: bool = False,
        on_sync_to_sqlite: Optional[Callable] = None,
        on_sync_from_sqlite: Optional[Callable] = None,
        classes: str = "bg-blue-600 text-white p-4 flex justify-between items-center",
        logo: Optional[str] = None,
        extra_buttons: List[Dict] = None,
        core: Optional["Core"] = None,
        client_state: Optional[Dict] = None,
        ui_manager: Optional[object] = None
    ):
        if not username:
            raise ValueError("Username không được để trống")
        if core and not hasattr(core, "sqlite_handler"):
            raise ValueError("Core không hợp lệ: Thiếu sqlite_handler")
        if client_state and not isinstance(client_state, dict):
            raise ValueError("client_state phải là dictionary")
        if client_state:
            client_state = {k: v for k, v in client_state.items() if not callable(v)}
        self.username = username
        self._on_logout = on_logout
        self.is_admin = is_admin
        self._on_sync_to_sqlite = on_sync_to_sqlite
        self._on_sync_from_sqlite = on_sync_from_sqlite
        self.classes = classes
        self.logo = logo
        self.extra_buttons = extra_buttons or []
        self.syncing = False
        self.core = core
        self.client_state = client_state or {}
        self.ui_manager = ui_manager
        self.protected_only = False
        self.selected_collections = []
        self.avatar_image_drawer = None
        self.avatar_image_header = None
        self.container = None
        self.right_drawer = None
        self.logo_image = None
        self.username_label = None
        self.drawer_username_label = None
        self.drawer_logo = None
        self.menu_button = None
        self.upload_button = None
        self.sync_label = None
        self.sync_select = None
        self.protected_checkbox = None
        self.rendered = False
        self.cached_user_data = None
        logger.debug(f"{self.username}: Khởi tạo HeaderComponent")

    async def get_user_data(self):
        """Lấy và cache dữ liệu người dùng từ SQLite"""
        if self.cached_user_data:
            logger.debug(f"{self.username}: Sử dụng dữ liệu người dùng từ cache")
            return self.cached_user_data

        async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=10.0) as conn:
            async with conn.execute(
                "SELECT avatar, role FROM users WHERE username = ?",
                (self.username,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    self.cached_user_data = {"avatar": row[0], "role": row[1]}
                    logger.debug(f"{self.username}: Đã lấy dữ liệu người dùng từ DB: avatar={row[0]}, role={row[1]}")
                    return self.cached_user_data
                else:
                    logger.warning(f"{self.username}: Không tìm thấy thông tin người dùng trong DB")
                    self.cached_user_data = {"avatar": None, "role": "user"}
                    return self.cached_user_data

    async def get_available_tables(self) -> List[str]:
        try:
            async with asyncio.timeout(10):
                logger.debug(f"{self.username}: Đang lấy danh sách bảng")
                result = await self.core.get_available_tables(self.username)
                if "error" in result:
                    logger.error(f"{self.username}: Lỗi khi lấy danh sách bảng: {result['error']}")
                    if context.client.has_socket_connection:
                        ui.notify(f"Lỗi lấy danh sách bảng: {result['error']}", type="negative")
                    return []
                tables = result.get("success", [])
                logger.debug(f"{self.username}: Danh sách bảng: {tables}")
                return tables
        except asyncio.TimeoutError as e:
            logger.error(f"{self.username}: Timeout khi lấy danh sách bảng: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify("Hết thời gian lấy danh sách bảng", type="negative")
            return []
        except Exception as e:
            logger.error(f"{self.username}: Lỗi khi lấy danh sách bảng: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(f"Lỗi lấy danh sách bảng: {str(e)}", type="negative")
            return []

    async def get_sync_tables(self) -> List[str]:
        try:
            tables = await self.get_available_tables()
            if self.protected_only:
                tables = [
                    t for t in tables
                    if t in Config.PROTECTED_TABLES or t in Config.SPECIAL_TABLES
                ]
            else:
                tables = [
                    t for t in tables
                    if t not in Config.PROTECTED_TABLES or t in Config.SPECIAL_TABLES
                ]
            if self.selected_collections:
                tables = list(set(tables) | set(self.selected_collections))
            logger.debug(f"{self.username}: Danh sách bảng đồng bộ: {tables}")
            return sorted(tables)
        except Exception as e:
            logger.error(f"{self.username}: Lỗi khi lấy danh sách bảng đồng bộ: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(f"Lỗi lấy danh sách bảng đồng bộ: {str(e)}", type="negative")
            return []

    async def update_tabs_after_sync(self, specific_collections: List[str]):
        try:
            logger.debug(f"{self.username}: Cập nhật tab sau đồng bộ, collections: {specific_collections}")
            tabs_to_update = []
            if specific_collections:
                if "chat_messages" in specific_collections:
                    tabs_to_update.append("Chat")
                if "qa_data" in specific_collections:
                    tabs_to_update.append("Training")
            else:
                tabs_to_update = ["Chat", "Training"]

            async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=10.0) as conn:
                async with conn.execute(
                    "SELECT COUNT(*) FROM chat_messages WHERE username = ?",
                    (self.username,)
                ) as cursor:
                    chat_count = (await cursor.fetchone())[0]
                logger.debug(f"{self.username}: Số tin nhắn trong chat_messages: {chat_count}")
                if chat_count == 0 and "chat_messages" in specific_collections:
                    logger.warning(f"{self.username}: Không có tin nhắn trong chat_messages sau đồng bộ")
                    if context.client.has_socket_connection:
                        ui.notify("Cảnh báo: Không có tin nhắn trong cơ sở dữ liệu sau đồng bộ.", type="warning")

                async with conn.execute(
                    "SELECT COUNT(*) FROM qa_data WHERE created_by = ?",
                    (self.username,)
                ) as cursor:
                    qa_count = (await cursor.fetchone())[0]
                logger.debug(f"{self.username}: Số bản ghi Q&A trong qa_data: {qa_count}")
                if qa_count == 0 and "qa_data" in specific_collections:
                    logger.warning(f"{self.username}: Không có bản ghi Q&A trong qa_data sau đồng bộ")
                    if context.client.has_socket_connection:
                        ui.notify("Cảnh báo: Không có bản ghi Q&A trong cơ sở dữ liệu sau đồng bộ.", type="warning")

            for tab_name in tabs_to_update:
                tab_info = self.ui_manager.registered_tabs.get(tab_name, {}) if self.ui_manager else {}
                update_func = tab_info.get("update")
                render_func = tab_info.get("render")

                if update_func and callable(update_func):
                    logger.debug(f"{self.username}: Gọi update_func cho tab {tab_name}")
                    try:
                        if asyncio.iscoroutinefunction(update_func):
                            await update_func(self.core, self.username, self.is_admin, self.client_state)
                        else:
                            update_func(self.core, self.username, self.is_admin, self.client_state)
                        logger.info(f"{self.username}: Cập nhật tab {tab_name} thành công")
                    except Exception as e:
                        logger.error(f"{self.username}: Lỗi khi gọi update_func cho tab {tab_name}: {str(e)}", exc_info=True)
                        if context.client.has_socket_connection:
                            ui.notify(f"Lỗi cập nhật tab {tab_name}: {str(e)}", type="negative")
                        if render_func and callable(render_func) and asyncio.iscoroutinefunction(render_func):
                            logger.debug(f"{self.username}: Thử gọi render_func cho tab {tab_name}")
                            try:
                                await render_func(self.core, self.username, self.is_admin, self.client_state)
                                logger.info(f"{self.username}: Render lại tab {tab_name} thành công")
                            except Exception as e:
                                logger.error(f"{self.username}: Lỗi khi gọi render_func cho tab {tab_name}: {str(e)}", exc_info=True)
                                if context.client.has_socket_connection:
                                    ui.notify(f"Lỗi render tab {tab_name}: {str(e)}", type="negative")
                else:
                    logger.warning(f"{self.username}: Không tìm thấy update_func cho tab {tab_name}")
                    if render_func and callable(render_func) and asyncio.iscoroutinefunction(render_func):
                        logger.debug(f"{self.username}: Thử gọi render_func cho tab {tab_name}")
                        try:
                            await render_func(self.core, self.username, self.is_admin, self.client_state)
                            logger.info(f"{self.username}: Render lại tab {tab_name} thành công")
                        except Exception as e:
                            logger.error(f"{self.username}: Lỗi khi gọi render_func cho tab {tab_name}: {str(e)}", exc_info=True)
                            if context.client.has_socket_connection:
                                ui.notify(f"Lỗi render tab {tab_name}: {str(e)}", type="negative")
                    else:
                        logger.warning(f"{self.username}: Không tìm thấy render_func hợp lệ cho tab {tab_name}")
                        if context.client.has_socket_connection:
                            ui.notify(f"Không thể cập nhật tab {tab_name}", type="warning")
            await safe_ui_update()
        except Exception as e:
            logger.error(f"{self.username}: Lỗi khi cập nhật các tab sau đồng bộ: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(f"Lỗi cập nhật giao diện: {str(e)}", type="negative")

    async def render(self):
        async with asyncio.timeout(10):
            try:
                old_container = self.container
                old_right_drawer = self.right_drawer

                self.container = ui.header().classes(
                    f"{self.classes} fixed top-0 left-0 right-0 z-50 flex justify-between items-center px-4 py-2 sm:px-6 md:px-8 flex-nowrap min-h-[60px]"
                )
                logger.debug(f"{self.username}: Đã khởi tạo header container")

                user_data = await self.get_user_data()
                self.client_state["avatar_url"] = user_data["avatar"]
                self.client_state["role"] = user_data["role"]
                self.is_admin = self.client_state["role"].lower() == "admin"
                logger.debug(f"{self.username}: Đã lấy thông tin người dùng: avatar={self.client_state['avatar_url']}, role={self.client_state['role']}, is_admin={self.is_admin}")

                if not user_data["avatar"] and not user_data["role"]:
                    logger.warning(f"{self.username}: Không tìm thấy thông tin người dùng, sử dụng giá trị mặc định")
                    if context.client.has_socket_connection:
                        ui.notify("Không tìm thấy thông tin người dùng, hiển thị mặc định", type="warning")

                with self.container:
                    if self.logo:
                        self.logo_image = ui.image(self.logo).classes("h-8 w-auto")
                        logger.debug(f"{self.username}: Đã render logo: {self.logo}")
                    with ui.element("div").classes("flex items-center space-x-2"):
                        if self.client_state.get("avatar_url"):
                            self.avatar_image_header = ui.image(self.client_state["avatar_url"]).classes("w-8 h-8 rounded-full")
                            logger.debug(f"{self.username}: Hiển thị avatar trong header: {self.client_state['avatar_url']}")
                        else:
                            logger.debug(f"{self.username}: Không có avatar_url, hiển thị chữ cái đầu")
                            self.avatar_image_header = ui.label(self.username[0].upper()).classes(
                                "w-8 h-8 rounded-full bg-gray-300 text-center flex items-center justify-center"
                            )
                        self.username_label = ui.label(f"Chào mừng {self.username}").classes("text-base sm:text-lg font-semibold flex-shrink-0")
                        logger.debug(f"{self.username}: Đã render username_label: Chào mừng {self.username}")
                    ui.element("div").classes("hidden sm:block flex-1")
                    self.menu_button = ui.button(icon="more_vert").classes("text-white hover:bg-white/20 rounded p-2 flex-shrink-0").props("flat dense").tooltip("Mở menu").on("click", lambda: self.right_drawer.toggle() if self.right_drawer else None)
                    logger.debug(f"{self.username}: Đã render menu_button")

                self.right_drawer = ui.right_drawer(fixed=False).classes("bg-gray-100 text-gray-900 w-full sm:w-80 md:w-96 h-full")
                self.right_drawer.props("overlay")
                with self.right_drawer:
                    with ui.scroll_area().classes("w-full h-full p-4 space-y-4"):
                        if self.logo:
                            self.drawer_logo = ui.image(self.logo).classes("h-8 w-auto mx-auto sm:mx-0")
                            logger.debug(f"{self.username}: Đã render logo trong right drawer")
                        with ui.element("div").classes("flex items-center space-x-2"):
                            if self.client_state.get("avatar_url"):
                                self.avatar_image_drawer = ui.image(self.client_state["avatar_url"]).classes("w-10 h-10 rounded-full")
                                logger.debug(f"{self.username}: Hiển thị avatar trong right drawer: {self.client_state['avatar_url']}")
                            else:
                                logger.debug(f"{self.username}: Không có avatar_url trong right drawer, hiển thị chữ cái đầu")
                                self.avatar_image_drawer = ui.label(self.username[0].upper()).classes(
                                    "w-10 h-10 rounded-full bg-gray-300 text-center flex items-center justify-center"
                                )
                            self.drawer_username_label = ui.label(f"Chào mừng {self.username}").classes("text-lg text-center sm:text-left")
                            logger.debug(f"{self.username}: Đã render drawer_username_label: Chào mừng {self.username}")

                        with ui.card().classes("p-4 space-y-3 w-full"):
                            ui.label("Cập nhật avatar").classes("text-sm font-semibold")
                            self.upload_button = ui.upload(
                                label="Chọn và tải lên hình ảnh",
                                auto_upload=True,
                                on_upload=lambda e: self.handle_upload(e)
                            ).props(f'accept={",".join(Config.AVATAR_FILE_EXTENSIONS)}').classes("mb-4 w-full")
                            logger.debug(f"{self.username}: ui.upload đã được khởi tạo với auto_upload=True")

                        if self.is_admin and self.core:
                            status = await self.check_last_sync()
                            if "error" in status:
                                if context.client.has_socket_connection:
                                    ui.notify(f"Lỗi trạng thái đồng bộ: {status['error']}", type="negative")
                            else:
                                self.sync_label = ui.label(f"Lần đồng bộ cuối: {status.get('last_sync', 'Chưa đồng bộ')}").classes("text-sm")
                                logger.debug(f"{self.username}: Đã render sync_label: {self.sync_label.text}")

                        for button in self.extra_buttons:
                            on_click = button.get("on_click")
                            async def wrapped_click():
                                if on_click and callable(on_click):
                                    try:
                                        if asyncio.iscoroutinefunction(on_click):
                                            await on_click()
                                        else:
                                            on_click()
                                    except Exception as e:
                                        logger.error(f"Lỗi khi xử lý extra button {button['label']}: {str(e)}", exc_info=True)
                                        if context.client.has_socket_connection:
                                            ui.notify(f"Lỗi: {str(e)}", type="negative")
                            btn = ButtonComponent(
                                label=button["label"],
                                on_click=wrapped_click,
                                icon=button.get("icon"),
                                classes=button.get("classes", "bg-blue-600 text-white hover:bg-blue-700 w-full"),
                                props=button.get("props"),
                                disabled=self.syncing,
                                core=self.core,
                                client_state=self.client_state,
                            )
                            await btn.render()

                        if self.is_admin:
                            with ui.card().classes("p-4 space-y-3 w-full"):
                                available_tables = await self.get_sync_tables()
                                self.sync_select = ui.select(
                                    available_tables,
                                    multiple=True,
                                    label="Chọn bảng để đồng bộ",
                                    value=self.selected_collections,
                                ).bind_value_to(self, "selected_collections").props("dense clearable").classes("w-full").tooltip(
                                    "Chọn thêm bảng để đồng bộ cùng với các bảng được xác định bởi checkbox."
                                )
                                with ui.row().classes("items-center space-x-2"):
                                    self.protected_checkbox = ui.checkbox("Chỉ đồng bộ bảng bảo vệ").bind_value_to(self, "protected_only").props("dense").tooltip(
                                        "Nếu chọn, chỉ đồng bộ bảng bảo vệ và đặc biệt. Có thể kết hợp với các bảng trong dropdown."
                                    )
                                if callable(self._on_sync_to_sqlite):
                                    btn_sync_to = ButtonComponent(
                                        label="Đồng bộ từ Firestore",
                                        on_click=self.handle_sync_to_sqlite,
                                        icon="sync",
                                        classes="bg-green-600 hover:bg-green-700 w-full sm:w-auto",
                                        disabled=self.syncing,
                                        tooltip="Đồng bộ dữ liệu từ Firestore về SQLite",
                                        core=self.core,
                                        client_state=self.client_state,
                                    )
                                    await btn_sync_to.render()
                                if callable(self._on_sync_from_sqlite):
                                    btn_sync_from = ButtonComponent(
                                        label="Đồng bộ lên Firestore",
                                        on_click=self.handle_sync_from_sqlite,
                                        icon="sync",
                                        classes="bg-green-600 hover:bg-green-700 w-full sm:w-auto",
                                        disabled=self.syncing,
                                        tooltip="Đồng bộ dữ liệu từ SQLite lên Firestore",
                                        core=self.core,
                                        client_state=self.client_state,
                                    )
                                    await btn_sync_from.render()

                        async def logout_click():
                            try:
                                if asyncio.iscoroutinefunction(self._on_logout):
                                    await self._on_logout()
                                else:
                                    self._on_logout()
                                if self.right_drawer:
                                    self.right_drawer.toggle()
                            except Exception as e:
                                logger.error(f"{self.username}: Lỗi khi đăng xuất: {str(e)}", exc_info=True)
                                if context.client.has_socket_connection:
                                    ui.notify(f"Lỗi: {str(e)}", type="negative")
                        btn_logout = ButtonComponent(
                            label="Thoát",
                            on_click=logout_click,
                            icon="logout",
                            classes="bg-red-600 hover:bg-red-700 w-full",
                            tooltip="Đăng xuất",
                            core=self.core,
                            client_state=self.client_state,
                        )
                        await btn_logout.render()

                if old_container:
                    old_container.clear()
                    old_container.delete()
                    logger.debug(f"{self.username}: Đã xóa old_container")
                if old_right_drawer:
                    old_right_drawer.clear()
                    old_right_drawer.delete()
                    logger.debug(f"{self.username}: Đã xóa old_right_drawer")

                self.rendered = True
                logger.info(f"{self.username}: Đã render giao diện HeaderComponent")
                await safe_ui_update()

            except RuntimeError as e:
                logger.error(f"{self.username}: Lỗi render header (RuntimeError): {str(e)}", exc_info=True)
                if context.client.has_socket_connection:
                    ui.notify(f"Lỗi render header: {str(e)}", type="negative")
                if not self.container:
                    self.container = ui.header().classes(
                        f"{self.classes} fixed top-0 left-0 right-0 z-50 flex justify-between items-center px-4 py-2 sm:px-6 md:px-8 flex-nowrap min-h-[60px]"
                    )
                    with self.container:
                        ui.label("Header lỗi, đang khôi phục...").classes("text-white")
                    await safe_ui_update()
            except asyncio.TimeoutError as e:
                logger.error(f"{self.username}: Timeout khi render header: {str(e)}", exc_info=True)
                if context.client.has_socket_connection:
                    ui.notify("Hết thời gian render header", type="negative")
            except Exception as e:
                logger.error(f"{self.username}: Lỗi render header: {str(e)}", exc_info=True)
                if context.client.has_socket_connection:
                    ui.notify(f"Lỗi render header: {str(e)}", type="negative")

    async def check_last_sync(self) -> Dict:
        try:
            return await check_last_sync(self.core, self.username)
        except Exception as e:
            logger.error(f"{self.username}: Lỗi kiểm tra trạng thái đồng bộ: {str(e)}", exc_info=True)
            return {"error": str(e)}

    async def handle_sync_to_sqlite(self):
        self.syncing = True
        try:
            async with asyncio.timeout(300):
                sync_tables = await self.get_sync_tables()
                result = await self.handle_sync(self._on_sync_to_sqlite, "to_sqlite", specific_collections=sync_tables)
                await self.update_tabs_after_sync(sync_tables)
                if "error" not in result:
                    if context.client.has_socket_connection:
                        ui.notify(f"Đồng bộ từ Firestore thành công: {result.get('synced_records', 0)} bản ghi", type="positive")
        except asyncio.TimeoutError as e:
            logger.error(f"{self.username}: Timeout khi đồng bộ từ Firestore: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(f"Timeout khi đồng bộ từ Firestore: {str(e)}", type="negative")
        except Exception as e:
            logger.error(f"{self.username}: Lỗi đồng bộ từ Firestore: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(f"Lỗi đồng bộ từ Firestore: {str(e)}", type="negative")
        finally:
            self.syncing = False
            await safe_ui_update()

    async def handle_sync_from_sqlite(self):
        self.syncing = True
        try:
            async with asyncio.timeout(300):
                sync_tables = await self.get_sync_tables()
                result = await self.handle_sync(self._on_sync_from_sqlite, "from_sqlite", specific_collections=sync_tables)
                await self.update_tabs_after_sync(sync_tables)
                if "error" not in result:
                    if context.client.has_socket_connection:
                        ui.notify(f"Đồng bộ lên Firestore thành công: {result.get('synced_records', 0)} bản ghi", type="positive")
        except asyncio.TimeoutError as e:
            logger.error(f"{self.username}: Timeout khi đồng bộ lên Firestore: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(f"Timeout khi đồng bộ lên Firestore: {str(e)}", type="negative")
        except Exception as e:
            logger.error(f"{self.username}: Lỗi đồng bộ lên Firestore: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(f"Lỗi đồng bộ lên Firestore: {str(e)}", type="negative")
        finally:
            self.syncing = False
            await safe_ui_update()

    async def handle_sync(
        self,
        sync_func: Callable,
        direction: str,
        specific_collections: Optional[List[str]] = None,
        progress_type: str = "linear"
    ):
        logger.debug(f"{self.username}: Bắt đầu đồng bộ {direction}, collections: {specific_collections}")
        try:
            async with asyncio.timeout(300):
                with ui.dialog() as dialog, ui.card().classes("p-4"):
                    ui.label(f"Đang đồng bộ {direction.replace('_', ' ')}...").classes("text-lg font-semibold")
                    progress_bar = ui.linear_progress(show_value=False).classes("w-full")

                    async def progress_callback(progress: float):
                        logger.debug(f"{self.username}: Tiến độ đồng bộ: {progress * 100:.1f}%")
                        progress_bar.set_value(min(max(progress, 0.0), 1.0))
                        await safe_ui_update()

                    if "progress_callback" not in inspect.signature(sync_func).parameters:
                        logger.warning(f"{self.username}: sync_func {sync_func.__name__} không hỗ trợ progress_callback")
                        async def wrapped_sync_func(*args, **kwargs):
                            kwargs.pop("progress_callback", None)
                            return await sync_func(*args, **kwargs)
                        sync_func = wrapped_sync_func

                    dialog.open()
                    result = await sync_func(
                        username=self.username,
                        progress_callback=progress_callback,
                        protected_only=self.protected_only,
                        specific_collections=specific_collections
                    )
                    dialog.close()

                    logger.info(f"{self.username}: Kết quả đồng bộ {direction}: {result}")
                    return result
        except asyncio.TimeoutError as e:
            logger.error(f"{self.username}: Timeout khi đồng bộ {direction}: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(f"Timeout khi đồng bộ {direction}: {str(e)}", type="negative")
            return {"error": f"Timeout: {str(e)}"}
        except Exception as e:
            logger.error(f"{self.username}: Lỗi đồng bộ {direction}: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(f"Lỗi đồng bộ {direction}: {str(e)}", type="negative")
            return {"error": str(e)}

    
    async def handle_upload(self, event):
        """Xử lý upload avatar và cập nhật lại UI."""
        try:
            file = event.content
            filename = event.name
            content_type, _ = mimetypes.guess_type(filename)
            if not content_type:
                content_type = "application/octet-stream"

            file.seek(0, os.SEEK_END)
            size = file.tell()
            file.seek(0)
            if size > Config.AVATAR_MAX_SIZE:
                raise ValueError(
                    f"Kích thước file vượt quá {Config.AVATAR_MAX_SIZE / (1024 * 1024)} MB"
                )

            if content_type not in Config.AVATAR_ALLOWED_FORMATS:
                raise ValueError(
                    f"Định dạng không được hỗ trợ. Cho phép: {Config.AVATAR_ALLOWED_FORMATS}"
                )

            file_content = file.read()
            if content_type.startswith("image/"):
                img = Image.open(io.BytesIO(file_content))
                img = img.resize((200, 200), Image.LANCZOS)
                output = io.BytesIO()
                img.save(output, format="JPEG", quality=85)
                file_content = output.getvalue()

            user_id = str(uuid.uuid4()) + ".jpg"
            avatar_url = await self.core.upload_file(
                file_content,
                content_type,
                Config.AVATAR_STORAGE_PATH,
                user_id,
            )

            # Ghi file xuống đĩa
            file_path = os.path.join(Config.AVATAR_STORAGE_PATH, user_id)
            with open(file_path, "wb") as f:
                f.write(file_content)

            async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=10.0) as conn:
                async with conn.execute(
                    "SELECT avatar FROM users WHERE username = ?",
                    (self.username,),
                ) as cursor:
                    existing = await cursor.fetchone()
                    if existing and existing[0] == avatar_url:
                        logger.warning(
                            f"{self.username}: Avatar giống hệt đã tồn tại, bỏ qua"
                        )
                        if context.client.has_socket_connection:
                            ui.notify("Avatar này đã tồn tại", type="warning")
                        return

                await conn.execute(
                    """
                    UPDATE users
                    SET avatar = ?, timestamp = ?
                    WHERE username = ?
                    """,
                    (avatar_url, int(time.time()), self.username),
                )
                await conn.execute(
                    """
                    INSERT INTO sync_log
                        (id, table_name, record_id, action, timestamp, details)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        "users",
                        user_id,
                        "UPDATE",
                        int(time.time()),
                        json.dumps(
                            {
                                "username": self.username,
                                "action": "update_avatar",
                                "field": "avatar",
                                "filename": filename,
                                "size": size,
                            },
                            ensure_ascii=False,
                        ),
                    ),
                )
                await conn.commit()

            # Cập nhật client_state và xóa cache
            self.client_state["avatar_url"] = f"{avatar_url}?t={int(time.time())}"
            self.cached_user_data = None
            logger.info(
                f"{self.username}: Upload avatar thành công, URL: "
                f"{self.client_state['avatar_url']}"
            )

            # Cập nhật giao diện ngay lập tức
            if self.rendered:
                logger.debug(
                    f"{self.username}: Gọi update() để làm mới giao diện sau upload"
                )
                await self.update()
            else:
                logger.debug(
                    f"{self.username}: Giao diện chưa render, gọi render()"
                )
                await self.render()

            if context.client.has_socket_connection:
                ui.notify("Upload avatar thành công", type="positive")

        except Exception as e:
            logger.error(
                f"{self.username}: Lỗi upload avatar: {str(e)}",
                exc_info=True,
            )
            if context.client.has_socket_connection:
                ui.notify(f"Lỗi upload avatar: {str(e)}", type="negative")

    async def update(self):
        try:
            logger.debug(f"{self.username}: Bắt đầu update HeaderComponent")

            if not self.rendered or not self.container or not self.right_drawer:
                logger.warning(
                    f"{self.username}: Giao diện chưa render, thử render lại"
                )
                await self.render()
                return

            user_data = await self.get_user_data()
            self.client_state["avatar_url"] = user_data["avatar"]
            self.client_state["role"] = user_data["role"]
            self.is_admin = self.client_state["role"].lower() == "admin"

            # Cập nhật username label
            if self.username_label:
                self.username_label.set_text(f"Chào mừng {self.username}")
                logger.debug(
                    f"{self.username}: Cập nhật username_label: "
                    f"Chào mừng {self.username}"
                )
            if self.drawer_username_label:
                self.drawer_username_label.set_text(f"Chào mừng {self.username}")
                logger.debug(
                    f"{self.username}: Cập nhật drawer_username_label: "
                    f"Chào mừng {self.username}"
                )

            # Cập nhật avatar
            avatar_url = self.client_state.get("avatar_url")
            if avatar_url:
                avatar_url = f"{avatar_url}?t={int(time.time())}"

                # Xử lý avatar_image_header
                if isinstance(self.avatar_image_header, ui.image):
                    self.avatar_image_header.set_source(avatar_url)
                    logger.debug(
                        f"{self.username}: Cập nhật avatar_image_header: {avatar_url}"
                    )
                else:
                    logger.debug(
                        f"{self.username}: avatar_image_header không phải ui.image, tạo mới"
                    )
                    if self.avatar_image_header:
                        self.avatar_image_header.delete()
                    with self.container:
                        with ui.element("div").classes("flex items-center space-x-2"):
                            self.avatar_image_header = ui.image(avatar_url).classes(
                                "w-8 h-8 rounded-full"
                            )
                        logger.debug(
                            f"{self.username}: Tạo mới avatar_image_header: {avatar_url}"
                        )

                # Xử lý avatar_image_drawer
                if isinstance(self.avatar_image_drawer, ui.image):
                    self.avatar_image_drawer.set_source(avatar_url)
                    logger.debug(
                        f"{self.username}: Cập nhật avatar_image_drawer: {avatar_url}"
                    )
                else:
                    logger.debug(
                        f"{self.username}: avatar_image_drawer không phải ui.image, tạo mới"
                    )
                    if self.avatar_image_drawer:
                        self.avatar_image_drawer.delete()
                    with self.right_drawer:
                        with ui.element("div").classes("flex items-center space-x-2"):
                            self.avatar_image_drawer = ui.image(avatar_url).classes(
                                "w-10 h-10 rounded-full"
                            )
                        logger.debug(
                            f"{self.username}: Tạo mới avatar_image_drawer: {avatar_url}"
                        )
            else:
                # Hiển thị chữ cái đầu nếu không có avatar
                initial = self.username[0].upper()
                if self.avatar_image_header:
                    self.avatar_image_header.delete()
                with self.container:
                    with ui.element("div").classes("flex items-center space-x-2"):
                        self.avatar_image_header = ui.label(initial).classes(
                            "w-8 h-8 rounded-full bg-gray-300 text-center "
                            "flex items-center justify-center"
                        )
                    logger.debug(
                        f"{self.username}: Hiển thị chữ cái đầu cho avatar_image_header: {initial}"
                    )

                if self.avatar_image_drawer:
                    self.avatar_image_drawer.delete()
                with self.right_drawer:
                    with ui.element("div").classes("flex items-center space-x-2"):
                        self.avatar_image_drawer = ui.label(initial).classes(
                            "w-10 h-10 rounded-full bg-gray-300 text-center "
                            "flex items-center justify-center"
                        )
                    logger.debug(
                        f"{self.username}: Hiển thị chữ cái đầu cho avatar_image_drawer: {initial}"
                    )

            # Cập nhật sync_label nếu tồn tại
            if self.is_admin and self.sync_label:
                status = await self.check_last_sync()
                if "error" in status:
                    self.sync_label.set_text("Lỗi trạng thái đồng bộ")
                    logger.warning(
                        f"{self.username}: Lỗi trạng thái đồng bộ: {status['error']}"
                    )
                else:
                    self.sync_label.set_text(
                        f"Lần đồng bộ cuối: {status.get('last_sync', 'Chưa đồng bộ')}"
                    )
                    logger.debug(
                        f"{self.username}: Cập nhật sync_label: {self.sync_label.text}"
                    )

            await safe_ui_update()
            logger.info(f"{self.username}: Update HeaderComponent thành công")

        except Exception as e:
            logger.error(
                f"{self.username}: Lỗi khi update HeaderComponent: {str(e)}",
                exc_info=True,
            )
            if context.client.has_socket_connection:
                ui.notify(
                    f"Lỗi update HeaderComponent: {str(e)}",
                    type="negative",
                )
                
