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
from uiapp.language import get_text
import aiosqlite
import uuid
from config import Config
from .button import ButtonComponent
from PIL import Image
import io
from tenacity import retry, stop_after_attempt, wait_fixed
from datetime import datetime, timezone, timedelta

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
        ui_manager: Optional[object] = None,
        on_language_change: Optional[Callable] = None
    ):
        if not username:
            raise ValueError(get_text("vi", "username_empty_error", default="Username cannot be empty"))
        if core and not hasattr(core, "sqlite_handler"):
            raise ValueError(get_text("vi", "invalid_core_error", default="Invalid core: Missing sqlite_handler"))
        if client_state and not isinstance(client_state, dict):
            raise ValueError(get_text("vi", "invalid_client_state_error", default="client_state must be a dictionary"))
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
        self.language = app.storage.user.get("language", self.client_state.get("language", "vi"))
        self.on_language_change = on_language_change
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
        self.language_select = None
        self.extra_button_instances = []
        self.sync_to_button = None
        self.sync_from_button = None
        self.logout_button = None
        self.rendered = False
        self.cached_user_data = None
        logger.debug(get_text(self.language, 'header_init', default='HeaderComponent initialized for user={user}', user=self.username))

    async def handle_language_change(self, new_language: str):
        try:
            logger.debug(f"{self.username}: Xử lý thay đổi ngôn ngữ thành {new_language}")
            if new_language not in ['vi', 'en']:
                msg = get_text(self.language, 'invalid_language', default='Invalid language: {new_lang}', new_lang=new_language)
                logger.error(msg)
                ui.notify(msg, type='negative')
                return

            old_language = self.language
            self.language = new_language
            self.client_state['language'] = new_language
            app.storage.user['language'] = new_language

            if self.ui_manager:
                logger.debug(f"{self.username}: Gọi UIManager.set_language")
                await self.ui_manager.set_language(
                    new_language,
                    self.client_state,
                    self.client_state.get('session_token'),
                )

            if self.on_language_change and callable(self.on_language_change):
                logger.debug(f"{self.username}: Gọi on_language_change")
                if asyncio.iscoroutinefunction(self.on_language_change):
                    await self.on_language_change(new_language)
                else:
                    self.on_language_change(new_language)

            if self.rendered:
                logger.debug(f"{self.username}: Cập nhật giao diện header sau đổi ngôn ngữ")
                await self.update()

            await self.update_components_after_language_change()
            logger.info(f"{self.username}: Đã thay đổi ngôn ngữ từ {old_language} sang {new_language}")

        except Exception as e:
            error_msg = get_text(self.language, 'language_change_error', default='Error changing language: {error_msg}', error_msg=str(e))
            logger.error(error_msg, exc_info=True)
            ui.notify(error_msg, type='negative')

    async def update_components_after_language_change(self):
        try:
            logger.debug(f"{self.username}: Cập nhật các tab, sidebar và button sau khi thay đổi ngôn ngữ")
            if hasattr(self, "sidebar") and self.sidebar:
                logger.debug(f"{self.username}: Gọi Sidebar.update() sau đổi ngôn ngữ")
                self.sidebar.language = self.language
                await self.sidebar.update()

            if not self.ui_manager or not hasattr(self.ui_manager, "registered_tabs"):
                logger.warning(f"{self.username}: Không có ui_manager hoặc registered_tabs, bỏ qua cập nhật tabs")
            else:
                for tab_name, tab_info in self.ui_manager.registered_tabs.items():
                    update_func = tab_info.get("update")
                    render_func = tab_info.get("render")
                    try:
                        if update_func and callable(update_func):
                            if asyncio.iscoroutinefunction(update_func):
                                await update_func(self.core, self.username, self.is_admin, self.client_state)
                            else:
                                update_func(self.core, self.username, self.is_admin, self.client_state)
                            logger.info(f"{self.username}: Cập nhật tab {tab_name} thành công")
                        elif render_func and callable(render_func) and asyncio.iscoroutinefunction(render_func):
                            await render_func(self.core, self.username, self.is_admin, self.client_state)
                            logger.info(f"{self.username}: Render lại tab {tab_name} thành công")
                        else:
                            logger.warning(f"{self.username}: Không tìm thấy update_func/render_func cho tab {tab_name}")
                    except Exception as e:
                        logger.error(f"{self.username}: Lỗi khi cập nhật tab {tab_name}: {str(e)}", exc_info=True)
                        if context.client.has_socket_connection:
                            ui.notify(
                                get_text(self.language, "tab_update_error", default="Error updating tab {tab_name}: {error_msg}", tab_name=tab_name, error_msg=str(e)),
                                type="negative"
                            )

            button_instances = []
            if self.sync_to_button:
                button_instances.append(self.sync_to_button)
            if self.sync_from_button:
                button_instances.append(self.sync_from_button)
            if self.logout_button:
                button_instances.append(self.logout_button)
            button_instances.extend(self.extra_button_instances)

            for button in button_instances:
                try:
                    button.language = self.language
                    await button.update()
                    logger.info(f"{self.username}: Cập nhật ButtonComponent {button.label} thành công")
                except Exception as e:
                    logger.error(f"{self.username}: Lỗi khi cập nhật ButtonComponent {button.label}: {str(e)}", exc_info=True)
                    if context.client.has_socket_connection:
                        ui.notify(
                            get_text(self.language, "button_update_error", default="Error updating button {label}: {error}", label=button.label, error=str(e)),
                            type="negative"
                        )

            await safe_ui_update()
            logger.info(f"{self.username}: Đã cập nhật tất cả các tab, sidebar và button sau thay đổi ngôn ngữ")

        except Exception as e:
            logger.error(f"{self.username}: Lỗi khi cập nhật component sau thay đổi ngôn ngữ: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(
                    get_text(self.language, "components_update_error", default="Error updating components after language change: {error_msg}", error_msg=str(e)),
                    type="negative"
                )

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
                        ui.notify(get_text(self.language, "no_user_data", default="No user data found, using default"), type="warning")

                with self.container:
                    if self.logo:
                        self.logo_image = ui.image(self.logo).classes("h-8 w-auto")
                        logger.debug(f"{self.username}: Đã render logo: {self.logo}")
                    with ui.element("div").classes("flex items-center space-x-2"):
                        if self.client_state.get("avatar_url") and os.path.exists(os.path.join(Config.AVATAR_STORAGE_PATH, os.path.basename(self.client_state["avatar_url"]))):
                            self.avatar_image_header = ui.image(self.client_state["avatar_url"]).classes("w-8 h-8 rounded-full")
                            logger.debug(f"{self.username}: Hiển thị avatar trong header: {self.client_state['avatar_url']}")
                        else:
                            logger.debug(f"{self.username}: Không có avatar_url hoặc file không tồn tại, hiển thị chữ cái đầu")
                            self.avatar_image_header = ui.label(self.username[0].upper()).classes(
                                "w-8 h-8 rounded-full bg-gray-300 text-center flex items-center justify-center"
                            )
                        self.username_label = ui.label(get_text(self.language, "welcome_label", default="Welcome {username}", username=self.username)).classes("text-base sm:text-lg font-semibold flex-shrink-0")
                        logger.debug(f"{self.username}: Đã render username_label")
                    ui.element("div").classes("hidden sm:block flex-1")
                    self.menu_button = ui.button(icon="more_vert").classes("text-white hover:bg-white/20 rounded p-2 flex-shrink-0").props("flat dense").tooltip(get_text(self.language, "open_menu", default="Open menu")).on("click", lambda: self.right_drawer.toggle() if self.right_drawer else None)
                    logger.debug(f"{self.username}: Đã render menu_button")

                self.right_drawer = ui.right_drawer(fixed=False).classes("bg-gray-100 text-gray-900 w-full sm:w-80 md:w-96 h-full")
                self.right_drawer.props("overlay")
                with self.right_drawer:
                    with ui.scroll_area().classes("w-full h-full p-4 space-y-4"):
                        if self.logo:
                            self.drawer_logo = ui.image(self.logo).classes("h-8 w-auto mx-auto sm:mx-0")
                            logger.debug(f"{self.username}: Đã render logo trong right drawer")
                        with ui.element("div").classes("flex items-center space-x-2"):
                            if self.client_state.get("avatar_url") and os.path.exists(os.path.join(Config.AVATAR_STORAGE_PATH, os.path.basename(self.client_state["avatar_url"]))):
                                self.avatar_image_drawer = ui.image(self.client_state["avatar_url"]).classes("w-10 h-10 rounded-full")
                                logger.debug(f"{self.username}: Hiển thị avatar trong right drawer: {self.client_state['avatar_url']}")
                            else:
                                logger.debug(f"{self.username}: Không có avatar_url hoặc file không tồn tại trong right drawer, hiển thị chữ cái đầu")
                                self.avatar_image_drawer = ui.label(self.username[0].upper()).classes(
                                    "w-10 h-10 rounded-full bg-gray-300 text-center flex items-center justify-center"
                                )
                            self.drawer_username_label = ui.label(get_text(self.language, "welcome_label", default="Welcome {username}", username=self.username)).classes("text-lg text-center sm:text-left")
                            logger.debug(f"{self.username}: Đã render drawer_username_label")

                        with ui.card().classes("p-4 space-y-3 w-full"):
                            ui.label(get_text(self.language, "language_select_label", default="Select Language")).classes("text-sm font-semibold")
                            self.language_select = ui.select(
                                {"vi": get_text(self.language, "vietnamese", default="Vietnamese"), "en": get_text(self.language, "english", default="English")},
                                value=self.language,
                                on_change=lambda e: self.handle_language_change(e.value)
                            ).props("dense outlined").classes("w-full").tooltip(get_text(self.language, "language_select_tooltip", default="Choose interface language"))
                            logger.debug(f"{self.username}: Đã render language_select")

                        with ui.card().classes("p-4 space-y-3 w-full"):
                            ui.label(get_text(self.language, "update_avatar_label", default="Update Avatar")).classes("text-sm font-semibold")
                            self.upload_button = ui.upload(
                                label=get_text(self.language, "upload_image_label", default="Choose and upload image"),
                                auto_upload=True,
                                on_upload=lambda e: self.handle_upload(e)
                            ).props(f'accept={",".join(Config.AVATAR_FILE_EXTENSIONS)}').classes("mb-4 w-full")
                            logger.debug(f"{self.username}: ui.upload đã được khởi tạo với auto_upload=True")

                        if self.is_admin and self.core:
                            last_sync = self.client_state.get("last_sync", app.storage.user.get("last_sync"))
                            if last_sync is None:
                                status = await check_last_sync(self.core, self.username)
                                if "error" in status:
                                    self.sync_label = ui.label(
                                        get_text(self.language, "sync_status_error", default="Sync status error: {error}", error=status['error'])
                                    ).classes("text-sm")
                                    logger.warning(f"{self.username}: Lỗi trạng thái đồng bộ: {status['error']}")
                                else:
                                    last_sync = status.get("last_sync", get_text(self.language, "never_synced", default="Never synced"))
                                    self.client_state["last_sync"] = last_sync
                                    app.storage.user["last_sync"] = last_sync
                            self.sync_label = ui.label(
                                get_text(self.language, "last_sync_label", default="Last sync: {last_sync}", last_sync=last_sync)
                            ).classes("text-sm")
                            logger.debug(f"{self.username}: Đã render sync_label với last_sync: {last_sync}")

                        self.extra_button_instances = []
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
                                            ui.notify(get_text(self.language, "button_error", default="Error: {error}", error=str(e)), type="negative")
                            btn = ButtonComponent(
                                label=button["label"],
                                on_click=wrapped_click,
                                icon=button.get("icon"),
                                classes=button.get("classes", "bg-blue-600 text-white hover:bg-blue-700 w-full"),
                                props=button.get("props"),
                                disabled=self.syncing,
                                core=self.core,
                                client_state=self.client_state,
                                language=self.language
                            )
                            await btn.render()
                            self.extra_button_instances.append(btn)
                            logger.debug(f"{self.username}: Đã render extra button {button['label']}")

                        if self.is_admin and self.core:
                            with ui.card().classes("p-4 space-y-3 w-full"):
                                available_tables = await self.get_sync_tables()
                                self.sync_select = ui.select(
                                    available_tables,
                                    multiple=True,
                                    label=get_text(self.language, "sync_table_select_label", default="Select tables to sync"),
                                    value=self.selected_collections,
                                ).bind_value_to(self, "selected_collections").props("dense clearable").classes("w-full").tooltip(
                                    get_text(self.language, "sync_table_tooltip", default="Select additional tables to sync with protected/special tables")
                                )
                                with ui.row().classes("items-center space-x-2"):
                                    self.protected_checkbox = ui.checkbox(
                                        get_text(self.language, "protected_tables_only", default="Sync only protected tables")
                                    ).bind_value_to(self, "protected_only").props("dense").tooltip(
                                        get_text(self.language, "protected_tables_tooltip", default="If checked, sync only protected and special tables. Can combine with dropdown tables.")
                                    )
                                if callable(self._on_sync_to_sqlite):
                                    self.sync_to_button = ButtonComponent(
                                        label=get_text(self.language, "sync_from_firestore", default="Sync from Firestore"),
                                        on_click=self.handle_sync_to_sqlite,
                                        icon="sync",
                                        classes="bg-green-600 hover:bg-green-700 w-full sm:w-auto",
                                        disabled=self.syncing,
                                        tooltip=get_text(self.language, "sync_from_firestore_tooltip", default="Sync data from Firestore to SQLite"),
                                        core=self.core,
                                        client_state=self.client_state,
                                        language=self.language
                                    )
                                    await self.sync_to_button.render()
                                    logger.debug(f"{self.username}: Đã render sync_to_button")
                                if callable(self._on_sync_from_sqlite):
                                    self.sync_from_button = ButtonComponent(
                                        label=get_text(self.language, "sync_to_firestore", default="Sync to Firestore"),
                                        on_click=self.handle_sync_from_sqlite,
                                        icon="sync",
                                        classes="bg-green-600 hover:bg-green-700 w-full sm:w-auto",
                                        disabled=self.syncing,
                                        tooltip=get_text(self.language, "sync_to_firestore_tooltip", default="Sync data from SQLite to Firestore"),
                                        core=self.core,
                                        client_state=self.client_state,
                                        language=self.language
                                    )
                                    await self.sync_from_button.render()
                                    logger.debug(f"{self.username}: Đã render sync_from_button")

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
                                    ui.notify(get_text(self.language, "logout_error", default="Error: {error}", error=str(e)), type="negative")
                        self.logout_button = ButtonComponent(
                            label=get_text(self.language, "logout_button", default="Logout"),
                            on_click=logout_click,
                            icon="logout",
                            classes="bg-red-600 hover:bg-red-700 w-full",
                            tooltip=get_text(self.language, "logout_tooltip", default="Log out"),
                            core=self.core,
                            client_state=self.client_state,
                            language=self.language
                        )
                        await self.logout_button.render()
                        logger.debug(f"{self.username}: Đã render logout_button")

                        with ui.card().classes("p-4 w-full"):
                            ui.html(
                                "Developed by AIChatVN Team. Support Zalo: <a href='https://zalo.me/0944121150' target='_blank'>0944121150</a>"
                            ).classes("text-sm text-center text-gray-600")
                            logger.debug(f"{self.username}: Đã render dòng chữ attribution tĩnh trong right_drawer")


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
                    ui.notify(get_text(self.language, "header_render_error", default="Error rendering header: {error}", error=str(e)), type="negative")
                if not self.container:
                    self.container = ui.header().classes(
                        f"{self.classes} fixed top-0 left-0 right-0 z-50 flex justify-between items-center px-4 py-2 sm:px-6 md:px-8 flex-nowrap min-h-[60px]"
                    )
                    with self.container:
                        ui.label(get_text(self.language, "header_recovery", default="Header error, recovering...")).classes("text-white")
                    await safe_ui_update()
            except asyncio.TimeoutError as e:
                logger.error(f"{self.username}: Timeout khi render header: {str(e)}", exc_info=True)
                if context.client.has_socket_connection:
                    ui.notify(get_text(self.language, "header_timeout_error", default="Timeout rendering header"), type="negative")
            except Exception as e:
                logger.error(f"{self.username}: Lỗi render header: {str(e)}", exc_info=True)
                if context.client.has_socket_connection:
                    ui.notify(get_text(self.language, "header_render_error", default="Error rendering header: {error}", error=str(e)), type="negative")

    async def update(self):
        try:
            logger.debug(f"{self.username}: Bắt đầu update HeaderComponent")
            logger.debug(f"{self.username}: client_state['last_sync']={self.client_state.get('last_sync')}, app.storage.user['last_sync']={app.storage.user.get('last_sync')}")

            if not self.rendered or not self.container or not self.right_drawer:
                logger.warning(f"{self.username}: Giao diện chưa render, thử render lại")
                await self.render()
                return

            user_data = await self.get_user_data()
            self.client_state["avatar_url"] = user_data["avatar"]
            self.client_state["role"] = user_data["role"]
            self.is_admin = self.client_state["role"].lower() == "admin"
            logger.debug(f"{self.username}: Cập nhật is_admin: {self.is_admin}")

            self.container.clear()
            self.right_drawer.clear()
            logger.debug(f"{self.username}: Đã xóa nội dung cũ của container và right_drawer")

            with self.container:
                if self.logo:
                    self.logo_image = ui.image(self.logo).classes("h-8 w-auto")
                    logger.debug(f"{self.username}: Cập nhật logo: {self.logo}")

                with ui.element("div").classes("flex items-center space-x-2"):
                    avatar_url = self.client_state.get("avatar_url")
                    if avatar_url and os.path.exists(os.path.join(Config.AVATAR_STORAGE_PATH, os.path.basename(avatar_url))):
                        self.avatar_image_header = ui.image(f"{avatar_url}?t={int(time.time())}").classes("w-8 h-8 rounded-full")
                        logger.debug(f"{self.username}: Cập nhật avatar_image_header: {avatar_url}")
                    else:
                        self.avatar_image_header = ui.label(self.username[0].upper()).classes(
                            "w-8 h-8 rounded-full bg-gray-300 text-center flex items-center justify-center"
                        )
                        logger.debug(f"{self.username}: Hiển thị chữ cái đầu cho avatar_image_header")

                    self.username_label = ui.label(
                        get_text(self.language, "welcome_label", default="Welcome {username}", username=self.username)
                    ).classes("text-base sm:text-lg font-semibold flex-shrink-0")
                    logger.debug(f"{self.username}: Cập nhật username_label")

                ui.element("div").classes("hidden sm:block flex-1")
                self.menu_button = ui.button(icon="more_vert").classes(
                    "text-white hover:bg-white/20 rounded p-2 flex-shrink-0"
                ).props("flat dense").tooltip(
                    get_text(self.language, "open_menu", default="Open menu")
                ).on(
                    "click",
                    lambda: self.right_drawer.toggle() if self.right_drawer else None,
                )
                logger.debug(f"{self.username}: Cập nhật menu_button")

            with self.right_drawer:
                with ui.scroll_area().classes("w-full h-full p-4 space-y-4"):
                    if self.logo:
                        self.drawer_logo = ui.image(self.logo).classes("h-8 w-auto mx-auto sm:mx-0")
                        logger.debug(f"{self.username}: Cập nhật logo trong right drawer")

                    with ui.element("div").classes("flex items-center space-x-2"):
                        avatar_url = self.client_state.get("avatar_url")
                        if avatar_url and os.path.exists(os.path.join(Config.AVATAR_STORAGE_PATH, os.path.basename(avatar_url))):
                            self.avatar_image_drawer = ui.image(f"{avatar_url}?t={int(time.time())}").classes("w-10 h-10 rounded-full")
                            logger.debug(f"{self.username}: Cập nhật avatar_image_drawer: {avatar_url}")
                        else:
                            self.avatar_image_drawer = ui.label(self.username[0].upper()).classes(
                                "w-10 h-10 rounded-full bg-gray-300 text-center flex items-center justify-center"
                            )
                            logger.debug(f"{self.username}: Hiển thị chữ cái đầu cho avatar_image_drawer")

                        self.drawer_username_label = ui.label(
                            get_text(self.language, "welcome_label", default="Welcome {username}", username=self.username)
                        ).classes("text-lg text-center sm:text-left")
                        logger.debug(f"{self.username}: Cập nhật drawer_username_label")

                    with ui.card().classes("p-4 space-y-3 w-full"):
                        ui.label(get_text(self.language, "language_select_label", default="Select Language")).classes("text-sm font-semibold")
                        self.language_select = ui.select(
                            {"vi": get_text(self.language, "vietnamese", default="Vietnamese"), "en": get_text(self.language, "english", default="English")},
                            value=self.language,
                            on_change=lambda e: self.handle_language_change(e.value)
                        ).props("dense outlined").classes("w-full").tooltip(
                            get_text(self.language, "language_select_tooltip", default="Choose interface language")
                        )
                        logger.debug(f"{self.username}: Cập nhật language_select")

                    with ui.card().classes("p-4 space-y-3 w-full"):
                        ui.label(get_text(self.language, "update_avatar_label", default="Update Avatar")).classes("text-sm font-semibold")
                        self.upload_button = ui.upload(
                            label=get_text(self.language, "upload_image_label", default="Choose and upload image"),
                            auto_upload=True,
                            on_upload=lambda e: self.handle_upload(e)
                        ).props(f'accept={",".join(Config.AVATAR_FILE_EXTENSIONS)}').classes("mb-4 w-full")
                        logger.debug(f"{self.username}: Cập nhật upload_button")

                    if self.is_admin and self.core:
                        last_sync = self.client_state.get("last_sync", app.storage.user.get("last_sync"))
                        if last_sync is None:
                            status = await check_last_sync(self.core, self.username)
                            if "error" in status:
                                self.sync_label = ui.label(
                                    get_text(self.language, "sync_status_error", default="Sync status error: {error}", error=status['error'])
                                ).classes("text-sm")
                                logger.warning(f"{self.username}: Lỗi trạng thái đồng bộ: {status['error']}")
                            else:
                                last_sync = status.get("last_sync", get_text(self.language, "never_synced", default="Never synced"))
                                self.client_state["last_sync"] = last_sync
                                app.storage.user["last_sync"] = last_sync
                        self.sync_label = ui.label(
                            get_text(self.language, "last_sync_label", default="Last sync: {last_sync}", last_sync=last_sync)
                        ).classes("text-sm")
                        logger.debug(f"{self.username}: Cập nhật sync_label với last_sync: {last_sync}")

                        with ui.card().classes("p-4 space-y-3 w-full"):
                            available_tables = await self.get_sync_tables()
                            self.sync_select = ui.select(
                                available_tables,
                                multiple=True,
                                label=get_text(self.language, "sync_table_select_label", default="Select tables to sync"),
                                value=self.selected_collections,
                            ).bind_value_to(self, "selected_collections").props("dense clearable").classes("w-full").tooltip(
                                get_text(self.language, "sync_table_tooltip", default="Select additional tables to sync with protected/special tables")
                            )
                            logger.debug(f"{self.username}: Cập nhật sync_select")
                            with ui.row().classes("items-center space-x-2"):
                                self.protected_checkbox = ui.checkbox(
                                    get_text(self.language, "protected_tables_only", default="Sync only protected tables")
                                ).bind_value_to(self, "protected_only").props("dense").tooltip(
                                    get_text(self.language, "protected_tables_tooltip", default="If checked, sync only protected and special tables. Can combine with dropdown tables.")
                                )
                                logger.debug(f"{self.username}: Cập nhật protected_checkbox")
                            if callable(self._on_sync_to_sqlite):
                                self.sync_to_button = ButtonComponent(
                                    label=get_text(self.language, "sync_from_firestore", default="Sync from Firestore"),
                                    on_click=self.handle_sync_to_sqlite,
                                    icon="sync",
                                    classes="bg-green-600 hover:bg-green-700 w-full sm:w-auto",
                                    disabled=self.syncing,
                                    tooltip=get_text(self.language, "sync_from_firestore_tooltip", default="Sync data from Firestore to SQLite"),
                                    core=self.core,
                                    client_state=self.client_state,
                                    language=self.language
                                )
                                await self.sync_to_button.render()
                                logger.debug(f"{self.username}: Cập nhật sync_to_button")
                            if callable(self._on_sync_from_sqlite):
                                self.sync_from_button = ButtonComponent(
                                    label=get_text(self.language, "sync_to_firestore", default="Sync to Firestore"),
                                    on_click=self.handle_sync_from_sqlite,
                                    icon="sync",
                                    classes="bg-green-600 hover:bg-green-700 w-full sm:w-auto",
                                    disabled=self.syncing,
                                    tooltip=get_text(self.language, "sync_to_firestore_tooltip", default="Sync data from SQLite to Firestore"),
                                    core=self.core,
                                    client_state=self.client_state,
                                    language=self.language
                                )
                                await self.sync_from_button.render()
                                logger.debug(f"{self.username}: Cập nhật sync_from_button")

                    self.extra_button_instances = []
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
                                        ui.notify(get_text(self.language, "button_error", default="Error: {error}", error=str(e)), type="negative")
                        btn = ButtonComponent(
                            label=button["label"],
                            on_click=wrapped_click,
                            icon=button.get("icon"),
                            classes=button.get("classes", "bg-blue-600 text-white hover:bg-blue-700 w-full"),
                            props=button.get("props"),
                            disabled=self.syncing,
                            core=self.core,
                            client_state=self.client_state,
                            language=self.language
                        )
                        await btn.render()
                        self.extra_button_instances.append(btn)
                        logger.debug(f"{self.username}: Cập nhật extra button {button['label']}")

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
                                ui.notify(get_text(self.language, "logout_error", default="Error: {error}", error=str(e)), type="negative")
                    self.logout_button = ButtonComponent(
                        label=get_text(self.language, "logout_button", default="Logout"),
                        on_click=logout_click,
                        icon="logout",
                        classes="bg-red-600 hover:bg-red-700 w-full",
                        tooltip=get_text(self.language, "logout_tooltip", default="Log out"),
                        core=self.core,
                        client_state=self.client_state,
                        language=self.language
                    )
                    await self.logout_button.render()
                    logger.debug(f"{self.username}: Cập nhật logout_button")

            await safe_ui_update()
            logger.info(f"{self.username}: Update HeaderComponent thành công")

        except Exception as e:
            logger.error(f"{self.username}: Lỗi khi update HeaderComponent: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(
                    get_text(self.language, "header_update_error", default="Error updating HeaderComponent: {error}", error=str(e)),
                    type="negative"
                )

    async def get_user_data(self):
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
                    self.cached_user_data = {"avatar": "/static/default_avatar.jpg", "role": "user"}
                    return self.cached_user_data

    async def get_available_tables(self) -> List[str]:
        try:
            async with asyncio.timeout(10):
                logger.debug(f"{self.username}: Đang lấy danh sách bảng")
                result = await self.core.get_available_tables(self.username)
                if "error" in result:
                    logger.error(f"{self.username}: Lỗi khi lấy danh sách bảng: {result['error']}")
                    if context.client.has_socket_connection:
                        ui.notify(get_text(self.language, "table_list_error", default="Error fetching table list: {error}", error=result['error']), type="negative")
                    return []
                tables = result.get("success", [])
                logger.debug(f"{self.username}: Danh sách bảng: {tables}")
                return tables
        except asyncio.TimeoutError as e:
            logger.error(f"{self.username}: Timeout khi lấy danh sách bảng: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(get_text(self.language, "table_list_timeout", default="Timeout fetching table list"), type="negative")
            return []
        except Exception as e:
            logger.error(f"{self.username}: Lỗi khi lấy danh sách bảng: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(get_text(self.language, "table_list_error", default="Error fetching table list: {error}", error=str(e)), type="negative")
            return []

    async def get_sync_tables(self) -> List[str]:
        try:
            tables = await self.get_available_tables()
            if self.protected_only:
                tables = [t for t in tables if t in Config.PROTECTED_TABLES or t in Config.SPECIAL_TABLES]
            else:
                tables = [t for t in tables if t not in Config.PROTECTED_TABLES or t in Config.SPECIAL_TABLES]
            if self.selected_collections:
                tables = list(set(tables) | set(self.selected_collections))
            logger.debug(f"{self.username}: Danh sách bảng đồng bộ: {tables}")
            return sorted(tables)
        except Exception as e:
            logger.error(f"{self.username}: Lỗi khi lấy danh sách bảng đồng bộ: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(get_text(self.language, "sync_table_error", default="Error fetching sync tables: {error}", error=str(e)), type="negative")
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
                        ui.notify(get_text(self.language, "no_chat_messages", default="Warning: No chat messages in database after sync."), type="warning")

                async with conn.execute(
                    "SELECT COUNT(*) FROM qa_data WHERE created_by = ?",
                    (self.username,)
                ) as cursor:
                    qa_count = (await cursor.fetchone())[0]
                logger.debug(f"{self.username}: Số bản ghi Q&A trong qa_data: {qa_count}")
                if qa_count == 0 and "qa_data" in specific_collections:
                    logger.warning(f"{self.username}: Không có bản ghi Q&A trong qa_data sau đồng bộ")
                    if context.client.has_socket_connection:
                        ui.notify(get_text(self.language, "no_qa_data", default="Warning: No Q&A records in database after sync."), type="warning")

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
                            ui.notify(get_text(self.language, "tab_update_error", default="Error updating tab {tab_name}: {error}", tab_name=tab_name, error=str(e)), type="negative")
                        if render_func and callable(render_func) and asyncio.iscoroutinefunction(render_func):
                            logger.debug(f"{self.username}: Thử gọi render_func cho tab {tab_name}")
                            try:
                                await render_func(self.core, self.username, self.is_admin, self.client_state)
                                logger.info(f"{self.username}: Render lại tab {tab_name} thành công")
                            except Exception as e:
                                logger.error(f"{self.username}: Lỗi khi gọi render_func cho tab {tab_name}: {str(e)}", exc_info=True)
                                if context.client.has_socket_connection:
                                    ui.notify(get_text(self.language, "tab_render_error", default="Error rendering tab {tab_name}: {error}", tab_name=tab_name, error=str(e)), type="negative")
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
                                ui.notify(get_text(self.language, "tab_render_error", default="Error rendering tab {tab_name}: {error}", tab_name=tab_name, error=str(e)), type="negative")
                    else:
                        logger.warning(f"{self.username}: Không tìm thấy render_func hợp lệ cho tab {tab_name}")
                        if context.client.has_socket_connection:
                            ui.notify(get_text(self.language, "no_tab_func", default="Cannot update tab {tab_name}", tab_name=tab_name), type="warning")
            await safe_ui_update()
        except Exception as e:
            logger.error(f"{self.username}: Lỗi khi cập nhật các tab sau đồng bộ: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(get_text(self.language, "tabs_update_error", default="Error updating tabs after sync: {error}", error=str(e)), type="negative")

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
                logger.debug(f"{self.username}: Bắt đầu đồng bộ từ Firestore, bảng: {sync_tables}")
                result = await self.handle_sync(self._on_sync_to_sqlite, "to_sqlite", specific_collections=sync_tables)
                await self.update_tabs_after_sync(sync_tables)
                if "error" not in result:
                    if context.client.has_socket_connection:
                        ui.notify(
                            get_text(self.language, "sync_from_firestore_success", default="Synced from Firestore: {records} records", records=result.get('synced_records', 0)),
                            type="positive"
                        )
                    # Cập nhật thời gian đồng bộ
                    last_sync_time = datetime.now(timezone(timedelta(hours=7))).strftime("%Y-%m-%d %H:%M:%S +07:00")
                    self.client_state["last_sync"] = last_sync_time
                    app.storage.user["last_sync"] = last_sync_time
                    if self.sync_label:
                        self.sync_label.set_text(
                            get_text(self.language, "last_sync_label", default="Last sync: {last_sync}", last_sync=last_sync_time)
                        )
                        logger.debug(f"{self.username}: Cập nhật sync_label sau đồng bộ từ Firestore: {last_sync_time}")
                    # Kiểm tra qa_data sau đồng bộ
                    async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=10.0) as conn:
                        async with conn.execute(
                            "SELECT COUNT(*) FROM qa_data WHERE created_by = ?",
                            (self.username,)
                        ) as cursor:
                            qa_count = (await cursor.fetchone())[0]
                            logger.debug(f"{self.username}: Số bản ghi Q&A trong qa_data sau đồng bộ: {qa_count}")
                            if qa_count == 0 and "qa_data" in sync_tables:
                                logger.warning(f"{self.username}: Không có bản ghi Q&A trong qa_data sau đồng bộ từ Firestore")
                                if context.client.has_socket_connection:
                                    ui.notify(get_text(self.language, "no_qa_data", default="Warning: No Q&A records in database after sync."), type="warning")
                    await safe_ui_update()
        except asyncio.TimeoutError as e:
            logger.error(f"{self.username}: Timeout khi đồng bộ từ Firestore: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(get_text(self.language, "sync_from_firestore_timeout", default="Timeout syncing from Firestore: {error}", error=str(e)), type="negative")
        except Exception as e:
            logger.error(f"{self.username}: Lỗi đồng bộ từ Firestore: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(get_text(self.language, "sync_from_firestore_error", default="Error syncing from Firestore: {error}", error=str(e)), type="negative")
        finally:
            self.syncing = False
            await self.update()

    async def handle_sync_from_sqlite(self):
        self.syncing = True
        try:
            async with asyncio.timeout(300):
                sync_tables = await self.get_sync_tables()
                logger.debug(f"{self.username}: Bắt đầu đồng bộ lên Firestore, bảng: {sync_tables}")
                result = await self.handle_sync(self._on_sync_from_sqlite, "from_sqlite", specific_collections=sync_tables)
                await self.update_tabs_after_sync(sync_tables)
                if "error" not in result:
                    if context.client.has_socket_connection:
                        ui.notify(
                            get_text(self.language, "sync_to_firestore_success", default="Synced to Firestore: {records} records", records=result.get('synced_records', 0)),
                            type="positive"
                        )
                    # Cập nhật thời gian đồng bộ
                    last_sync_time = datetime.now(timezone(timedelta(hours=7))).strftime("%Y-%m-%d %H:%M:%S +07:00")
                    self.client_state["last_sync"] = last_sync_time
                    app.storage.user["last_sync"] = last_sync_time
                    if self.sync_label:
                        self.sync_label.set_text(
                            get_text(self.language, "last_sync_label", default="Last sync: {last_sync}", last_sync=last_sync_time)
                        )
                        logger.debug(f"{self.username}: Cập nhật sync_label sau đồng bộ lên Firestore: {last_sync_time}")
                    # Kiểm tra qa_data sau đồng bộ
                    async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=10.0) as conn:
                        async with conn.execute(
                            "SELECT COUNT(*) FROM qa_data WHERE created_by = ?",
                            (self.username,)
                        ) as cursor:
                            qa_count = (await cursor.fetchone())[0]
                            logger.debug(f"{self.username}: Số bản ghi Q&A trong qa_data sau đồng bộ: {qa_count}")
                            if qa_count == 0 and "qa_data" in sync_tables:
                                logger.warning(f"{self.username}: Không có bản ghi Q&A trong qa_data sau đồng bộ lên Firestore")
                                if context.client.has_socket_connection:
                                    ui.notify(get_text(self.language, "no_qa_data", default="Warning: No Q&A records in database after sync."), type="warning")
                    await safe_ui_update()
        except asyncio.TimeoutError as e:
            logger.error(f"{self.username}: Timeout khi đồng bộ lên Firestore: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(get_text(self.language, "sync_to_firestore_timeout", default="Timeout syncing to Firestore: {error}", error=str(e)), type="negative")
        except Exception as e:
            logger.error(f"{self.username}: Lỗi đồng bộ lên Firestore: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(get_text(self.language, "sync_to_firestore_error", default="Error syncing to Firestore: {error}", error=str(e)), type="negative")
        finally:
            self.syncing = False
            await self.update()

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
                    ui.label(get_text(self.language, "sync_progress_label", default="Syncing {direction}...", direction=direction.replace('_', ' '))).classes("text-lg font-semibold")
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
                ui.notify(get_text(self.language, "sync_timeout", default="Timeout syncing {direction}: {error}", direction=direction, error=str(e)), type="negative")
            return {"error": f"Timeout: {str(e)}"}
        except Exception as e:
            logger.error(f"{self.username}: Lỗi đồng bộ {direction}: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(get_text(self.language, "sync_error", default="Error syncing {direction}: {error}", direction=direction, error=str(e)), type="negative")
            return {"error": str(e)}

    async def handle_upload(self, event):
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
                    get_text(self.language, "avatar_size_error", default="File size exceeds {size} MB", size=Config.AVATAR_MAX_SIZE / (1024 * 1024))
                )

            if content_type not in Config.AVATAR_ALLOWED_FORMATS:
                raise ValueError(
                    get_text(self.language, "avatar_format_error", default="Unsupported format. Allowed: {formats}", formats=Config.AVATAR_ALLOWED_FORMATS)
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

            file_path = os.path.join(Config.AVATAR_STORAGE_PATH, user_id)
            with open(file_path, "wb") as f:
                f.write(file_content)

            if not os.path.exists(file_path):
                raise ValueError(get_text(self.language, "avatar_save_error", default="Failed to save avatar file"))

            async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=10.0) as conn:
                async with conn.execute(
                    "SELECT avatar FROM users WHERE username = ?",
                    (self.username,),
                ) as cursor:
                    existing = await cursor.fetchone()
                    if existing and existing[0] == avatar_url:
                        logger.warning(f"{self.username}: Avatar giống hệt đã tồn tại, bỏ qua")
                        if context.client.has_socket_connection:
                            ui.notify(get_text(self.language, "avatar_exists", default="This avatar already exists"), type="warning")
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

            self.client_state["avatar_url"] = f"{avatar_url}?t={int(time.time())}"
            self.cached_user_data = None
            logger.info(f"{self.username}: Upload avatar thành công, URL: {self.client_state['avatar_url']}")

            if self.rendered:
                logger.debug(f"{self.username}: Gọi update() để làm mới giao diện sau upload")
                await self.update()
            else:
                logger.debug(f"{self.username}: Giao diện chưa render, gọi render()")
                await self.render()

            if context.client.has_socket_connection:
                ui.notify(get_text(self.language, "avatar_upload_success", default="Avatar uploaded successfully"), type="positive")

        except Exception as e:
            logger.error(f"{self.username}: Lỗi upload avatar: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(get_text(self.language, "avatar_upload_error", default="Error uploading avatar: {error}", error=str(e)), type="negative")
