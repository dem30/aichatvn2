from nicegui import ui
import asyncio
import logging
import inspect
from typing import Callable, Optional, List, Dict
from utils.logging import get_logger
from utils.core_common import check_last_sync
import aiosqlite
from config import Config
from .button import ButtonComponent

logger = get_logger("HeaderComponent")

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

    async def get_available_tables(self) -> List[str]:
        try:
            async with asyncio.timeout(60):
                logger.debug(f"Đang lấy danh sách bảng cho {self.username}")
                result = await self.core.get_available_tables(self.username)
                if "error" in result:
                    logger.error(f"{self.username}: Lỗi khi lấy danh sách bảng: {result['error']}")
                    ui.notify(f"Lỗi lấy danh sách bảng: {result['error']}", type="negative")
                    return []
                tables = result.get("success", [])
                logger.debug(f"Đã lấy danh sách bảng: {tables}")
                return tables
        except asyncio.TimeoutError as e:
            logger.error(f"{self.username}: Timeout khi lấy danh sách bảng: {str(e)}", exc_info=True)
            ui.notify("Hết thời gian lấy danh sách bảng", type="negative")
            return []
        except Exception as e:
            logger.error(f"{self.username}: Lỗi khi lấy danh sách bảng: {str(e)}", exc_info=True)
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
            logger.debug(f"Danh sách bảng đồng bộ cho {self.username}: {tables}")
            return sorted(tables)
        except Exception as e:
            logger.error(
                f"{self.username}: Lỗi khi lấy danh sách bảng đồng bộ: {str(e)}",
                exc_info=True
            )
            return []

    async def update_tabs_after_sync(self, specific_collections: List[str]):
        try:
            logger.debug(f"{self.username}: Bắt đầu cập nhật tab sau đồng bộ, collections: {specific_collections}")
            tabs_to_update = []
            if specific_collections:
                if "chat_messages" in specific_collections:
                    tabs_to_update.append("Chat")
                if "qa_data" in specific_collections:
                    tabs_to_update.append("Training")
            else:
                tabs_to_update = ["Chat", "Training"]

            chat_count = 0
            qa_count = 0
            try:
                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=30.0) as conn:
                    async with conn.execute("PRAGMA table_info(chat_messages)") as cursor:
                        chat_columns = [row[1] for row in await cursor.fetchall()]
                        logger.debug(f"{self.username}: Cột trong chat_messages: {chat_columns}")
                    async with conn.execute("PRAGMA table_info(qa_data)") as cursor:
                        qa_columns = [row[1] for row in await cursor.fetchall()]
                        logger.debug(f"{self.username}: Cột trong qa_data: {qa_columns}")

                    if "username" in chat_columns:
                        async with conn.execute("SELECT COUNT(*) FROM chat_messages WHERE username = ?", (self.username,)) as cursor:
                            chat_count = (await cursor.fetchone())[0]
                    else:
                        async with conn.execute("SELECT COUNT(*) FROM chat_messages") as cursor:
                            chat_count = (await cursor.fetchone())[0]
                    logger.debug(f"{self.username}: Số tin nhắn trong chat_messages sau đồng bộ: {chat_count}")
                    if chat_count == 0:
                        logger.warning(f"{self.username}: Không có tin nhắn trong chat_messages sau đồng bộ")
                        ui.notify("Cảnh báo: Không có tin nhắn trong cơ sở dữ liệu sau đồng bộ. Kiểm tra dữ liệu Firestore.", type="warning")

                    if "created_by" in qa_columns:
                        async with conn.execute("SELECT COUNT(*) FROM qa_data WHERE created_by = ?", (self.username,)) as cursor:
                            qa_count = (await cursor.fetchone())[0]
                    else:
                        async with conn.execute("SELECT COUNT(*) FROM qa_data") as cursor:
                            qa_count = (await cursor.fetchone())[0]
                    logger.debug(f"{self.username}: Số bản ghi Q&A trong qa_data sau đồng bộ: {qa_count}")
                    if qa_count == 0:
                        logger.warning(f"{self.username}: Không có bản ghi Q&A trong qa_data sau đồng bộ")
                        ui.notify("Cảnh báo: Không có bản ghi Q&A trong cơ sở dữ liệu sau đồng bộ. Kiểm tra dữ liệu Firestore.", type="warning")
            except Exception as e:
                logger.error(f"{self.username}: Lỗi khi kiểm tra dữ liệu SQLite: {str(e)}", exc_info=True)
                ui.notify(f"Lỗi kiểm tra dữ liệu sau đồng bộ: {str(e)}", type="negative")

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
                        logger.info(f"{self.username}: Cập nhật tab {tab_name} thành công sau đồng bộ (via update_func)")
                    except Exception as e:
                        logger.error(f"{self.username}: Lỗi khi gọi update_func cho tab {tab_name}: {str(e)}", exc_info=True)
                        ui.notify(f"Lỗi cập nhật tab {tab_name}: {str(e)}", type="negative")
                        if render_func and callable(render_func) and asyncio.iscoroutinefunction(render_func):
                            logger.debug(f"{self.username}: Thử gọi render_func cho tab {tab_name}")
                            try:
                                await render_func(self.core, self.username, self.is_admin, self.client_state)
                                logger.info(f"{self.username}: Render lại tab {tab_name} thành công sau đồng bộ")
                            except Exception as e:
                                logger.error(f"{self.username}: Lỗi khi gọi render_func cho tab {tab_name}: {str(e)}", exc_info=True)
                                ui.notify(f"Lỗi render tab {tab_name}: {str(e)}", type="negative")
                else:
                    logger.warning(f"{self.username}: Không tìm thấy update_func cho tab {tab_name} trong ui_manager.registered_tabs")
                    if render_func and callable(render_func) and asyncio.iscoroutinefunction(render_func):
                        logger.debug(f"{self.username}: Thử gọi render_func cho tab {tab_name}")
                        try:
                            await render_func(self.core, self.username, self.is_admin, self.client_state)
                            logger.info(f"{self.username}: Render lại tab {tab_name} thành công sau đồng bộ")
                        except Exception as e:
                            logger.error(f"{self.username}: Lỗi khi gọi render_func cho tab {tab_name}: {str(e)}", exc_info=True)
                            ui.notify(f"Lỗi render tab {tab_name}: {str(e)}", type="negative")
                    else:
                        logger.warning(f"{self.username}: Không tìm thấy render_func hoặc không hợp lệ cho tab {tab_name}")
                        ui.notify(f"Không thể cập nhật tab {tab_name}", type="warning")
            ui.update()
        except Exception as e:
            logger.error(f"{self.username}: Lỗi khi cập nhật các tab sau đồng bộ: {str(e)}", exc_info=True)
            ui.notify(f"Lỗi cập nhật giao diện: {str(e)}", type="negative")

    async def render(self):
        try:
            async with asyncio.timeout(60):
                if not self.username:
                    logger.error("Tên người dùng không hợp lệ")
                    ui.notify("Lỗi: Tên người dùng không hợp lệ", type="negative")
                    return

                if not callable(self._on_logout):
                    logger.error("Hành động đăng xuất không hợp lệ")
                    ui.notify("Lỗi: Hành động đăng xuất không hợp lệ", type="negative")
                    return

                # Drawer menu
                drawer = ui.right_drawer(fixed=False).classes(
                    "bg-gray-100 text-gray-900 w-full sm:w-80 md:w-96 h-full"
                )
                drawer.props("overlay")

                with drawer:
                    with ui.scroll_area().classes("w-full h-full p-4 space-y-4"):
                        if self.logo:
                            ui.image(self.logo).classes("h-8 w-auto mx-auto sm:mx-0")

                        ui.label(f"Chào mừng {self.username}").classes(
                            "text-lg text-center sm:text-left"
                        )

                        # Status sync nếu admin
                        if self.is_admin and self.core:
                            status = {"records": []}
                            if self.client_state.get("show_sync_status", False):
                                status = await self.core.get_sync_status(page=1, page_size=10)
                                if "error" in status:
                                    ui.notify(
                                        f"Lỗi trạng thái đồng bộ: {status['error']}",
                                        type="negative",
                                    )

                        # Extra buttons
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
                                        logger.error(
                                            f"Lỗi khi xử lý extra button {button['label']}: {str(e)}",
                                            exc_info=True,
                                        )
                                        ui.notify(f"Lỗi: {str(e)}", type="negative")

                            btn = ButtonComponent(
                                label=button["label"],
                                on_click=wrapped_click,
                                icon=button.get("icon"),
                                classes=button.get(
                                    "classes",
                                    "bg-blue-600 text-white hover:bg-blue-700 w-full",
                                ),
                                props=button.get("props"),
                                disabled=self.syncing,
                                core=self.core,
                                client_state=self.client_state,
                            )
                            await btn.render()

                        # Admin controls
                        if self.is_admin:
                            with ui.card().classes("p-4 space-y-3 w-full"):
                                available_tables = await self.get_available_tables()

                                ui.select(
                                    available_tables,
                                    multiple=True,
                                    label="Chọn bảng để đồng bộ",
                                    value=self.selected_collections,
                                ).bind_value_to(
                                    self, "selected_collections"
                                ).props(
                                    "dense clearable"
                                ).classes(
                                    "w-full"
                                ).tooltip(
                                    "Chọn thêm bảng để đồng bộ cùng với các bảng "
                                    "được xác định bởi checkbox."
                                )

                                with ui.row().classes("items-center space-x-2"):
                                    ui.checkbox("Chỉ đồng bộ bảng bảo vệ").bind_value_to(
                                        self, "protected_only"
                                    ).props(
                                        "dense"
                                    ).tooltip(
                                        "Nếu chọn, chỉ đồng bộ bảng bảo vệ và đặc biệt. "
                                        "Có thể kết hợp với các bảng trong dropdown."
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

                        # Nút đăng xuất
                        async def logout_click():
                            try:
                                if asyncio.iscoroutinefunction(self._on_logout):
                                    await self._on_logout()
                                else:
                                    self._on_logout()
                                drawer.toggle()  # Đóng drawer sau khi đăng xuất
                            except Exception as e:
                                logger.error(f"Lỗi khi đăng xuất: {str(e)}", exc_info=True)
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

                # Header cố định
                with ui.header().classes(
                    f"{self.classes} fixed top-0 left-0 right-0 z-50 "
                    "flex justify-between items-center px-4 py-2 "
                    "sm:px-6 md:px-8 flex-nowrap min-h-[60px]"
                ):
                    if self.logo:
                        ui.image(self.logo).classes("h-8 w-auto")

                    ui.label(f"Chào mừng {self.username}").classes(
                        "text-base sm:text-lg font-semibold flex-shrink-0"
                    )

                    ui.element("div").classes("hidden sm:block flex-1")

                    # Nút kebab mở drawer
                    ui.button(icon="more_vert") \
                        .classes("text-white hover:bg-white/20 rounded p-2 flex-shrink-0") \
                        .props("flat dense") \
                        .tooltip("Mở menu") \
                        .on("click", lambda: drawer.toggle())

        except asyncio.TimeoutError as e:
            logger.error(f"Timeout khi render header: {str(e)}", exc_info=True)
            ui.notify("Hết thời gian render header", type="negative")
        except Exception as e:
            error_msg = f"Lỗi render header: {str(e)}"
            logger.error(error_msg, exc_info=True)
            ui.notify(error_msg.split("\n")[0], type="negative")

    async def on_protected_only_change(self, value: bool):
        self.protected_only = value
        ui.update()

    async def check_last_sync(self) -> Dict:
        return await check_last_sync(self.core, self.username)

    async def handle_sync_to_sqlite(self):
        self.syncing = True
        try:
            async with asyncio.timeout(300):
                sync_tables = await self.get_sync_tables()
                await self.handle_sync(self._on_sync_to_sqlite, "to_sqlite", specific_collections=sync_tables)
                await self.update_tabs_after_sync(sync_tables)
        except asyncio.TimeoutError as e:
            logger.error(f"Timeout khi đồng bộ từ Firestore: {str(e)}", exc_info=True)
            ui.notify(f"Timeout khi đồng bộ từ Firestore: {str(e)}", type="negative")
        except Exception as e:
            logger.error(f"Lỗi đồng bộ từ Firestore: {str(e)}", exc_info=True)
            ui.notify(f"Lỗi đồng bộ từ Firestore: {str(e)}", type="negative")
        finally:
            self.syncing = False
            ui.update()

    async def handle_sync_from_sqlite(self):
        self.syncing = True
        try:
            async with asyncio.timeout(300):
                sync_tables = await self.get_sync_tables()
                await self.handle_sync(self._on_sync_from_sqlite, "from_sqlite", specific_collections=sync_tables)
                await self.update_tabs_after_sync(sync_tables)
        except asyncio.TimeoutError as e:
            logger.error(f"Timeout khi đồng bộ lên Firestore: {str(e)}", exc_info=True)
            ui.notify(f"Timeout khi đồng bộ lên Firestore: {str(e)}", type="negative")
        except Exception as e:
            logger.error(f"Lỗi đồng bộ lên Firestore: {str(e)}", exc_info=True)
            ui.notify(f"Lỗi đồng bộ lên Firestore: {str(e)}", type="negative")
        finally:
            self.syncing = False
            ui.update()

    
    async def handle_sync(
        self,
        sync_func: Callable,
        direction: str,
        specific_collections: Optional[List[str]] = None,
        progress_type: str = "linear"  # Chọn loại progress bar: linear hoặc circular
    ):
        logger.debug(
            f"Starting sync for {direction}, username: {self.username}, "
            f"specific_collections: {specific_collections}, progress_type: {progress_type}"
        )
        try:
            async with asyncio.timeout(300):
                # Tạo dialog với progress bar
                with ui.dialog() as dialog, ui.card().classes("p-4"):
                    ui.label(f"Đang đồng bộ {direction.replace('_', ' ')}...").classes(
                        "text-lg font-semibold"
                    )
                    # Chọn loại progress bar
                    if progress_type == "circular":
                        progress_bar = ui.circular_progress(
                            show_value=False, size="lg"
                        ).classes("w-full")
                    else:
                        progress_bar = ui.linear_progress(show_value=False).classes("w-full")

                    # Định nghĩa progress_callback để cập nhật progress bar
                    async def progress_callback(progress: float):
                        logger.debug(f"Progress callback: {progress * 100:.1f}%")
                        progress_bar.set_value(min(max(progress, 0.0), 1.0))  # Giới hạn 0.0-1.0
                        ui.update()  # Cập nhật giao diện người dùng

                    # Kiểm tra xem sync_func có hỗ trợ progress_callback không
                    if "progress_callback" not in inspect.signature(sync_func).parameters:
                        logger.warning(f"sync_func {sync_func.__name__} không hỗ trợ progress_callback")
                        async def wrapped_sync_func(*args, **kwargs):
                            kwargs.pop("progress_callback", None)
                            return await sync_func(*args, **kwargs)
                        sync_func = wrapped_sync_func

                    # Mở dialog
                    dialog.open()

                    logger.debug(f"Calling sync_func: {sync_func.__name__}, username: {self.username}")
                    # Gọi sync_func với progress_callback
                    result = await sync_func(
                        username=self.username,
                        progress_callback=progress_callback,
                        protected_only=self.protected_only,
                        specific_collections=specific_collections
                    )
                    logger.info(f"Sync result for {direction}: {result}")

                    # Đóng dialog
                    dialog.close()

                    # Thông báo kết quả
                    if "error" in result:
                        ui.notify(f"Đồng bộ {direction} thất bại: {result['error']}", type="negative")
                    else:
                        ui.notify(
                            f"Đồng bộ {direction} thành công: {result.get('synced_records', 0)} bản ghi",
                            type="positive"
                        )
                    logger.info(f"{self.username}: Các bảng được đồng bộ: {specific_collections or 'Tất cả'}")
                    if specific_collections and "qa_data" in specific_collections:
                        logger.info(f"{self.username}: Bảng qa_data đã được đồng bộ")
                    if specific_collections and "chat_messages" in specific_collections:
                        logger.info(f"{self.username}: Bảng chat_messages đã được đồng bộ")
                    return result
        except asyncio.TimeoutError as e:
            logger.error(f"Timeout khi đồng bộ {direction}: {str(e)}", exc_info=True)
            ui.notify(f"Timeout khi đồng bộ {direction}: {str(e)}", type="negative")
            dialog.close()  # Đảm bảo đóng dialog khi timeout
            return {"error": f"Timeout: {str(e)}"}
        except Exception as e:
            logger.error(f"Lỗi đồng bộ {direction}: {str(e)}", exc_info=True)
            ui.notify(f"Lỗi đồng bộ {direction}: {str(e)}", type="negative")
            dialog.close()  # Đảm bảo đóng dialog khi lỗi
            return {"error": str(e)}
            
            