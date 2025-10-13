
from typing import Callable, Dict, Any
from nicegui import ui, app, context
from uiapp.layouts.auth import AuthLayout
from uiapp.layouts.dashboard import DashboardLayout
from uiapp.language import get_text
from utils.logging import get_logger
from core import Core
import traceback
import re
import json
import asyncio
import hashlib
from config import Config
from pathlib import Path
import sys
from importlib import import_module
from importlib.util import spec_from_file_location, module_from_spec
from utils.core_common import safe_ui_update  # Thêm import

logger = get_logger("UIManager")

class UIManager:
    def __init__(self, core: Core):
        self.core = core
        self.registered_tabs: Dict[str, Any] = {}
        self.language = "vi"  # Mặc định ngôn ngữ là "vi", sẽ được cập nhật trong render
        self.on_language_change: Callable = None  # Callback để thông báo thay đổi ngôn ngữ
        logger.info(f"Khởi tạo UIManager với ngôn ngữ mặc định: {self.language}")

    
    
    # uiapp/layouts/ui_manager.py
    
    async def set_language(
        self,
        lang: str,
        client_state: Dict[str, Any] = None,
        session_token: str = None,
    ):
        """
        Đặt ngôn ngữ hiện tại, lưu vào user storage và client_state,
        thông báo thay đổi, và bắt buộc reload trang.
        """
        try:
            # Kiểm tra ngôn ngữ hợp lệ
            if lang not in ["vi", "en"]:
                logger.error(f"Ngôn ngữ không hợp lệ: {lang}")
                if context.client.has_socket_connection:
                    with context.client:
                        ui.notify(
                            get_text(
                                self.language,
                                "invalid_language",
                                default="Invalid language: {lang}",
                                lang=lang,
                            ),
                            type="negative",
                        )
                return

            logger.debug(
                f"Before setting language: current={self.language}, new={lang}"
            )

            # Kiểm tra kết nối client
            if not context.client.has_socket_connection:
                logger.warning(
                    f"Cannot set language to {lang}: client disconnected"
                )
                return

            await ui.context.client.connected()

            # Lưu ngôn ngữ cũ để log
            old_language = self.language

            # Cập nhật ngôn ngữ
            self.language = lang
            app.storage.user["language"] = self.language
            logger.debug(f"Đã cập nhật ngôn ngữ: {self.language}")
            logger.debug(f"app.storage.user trước reload: {app.storage.user}")

            # Lưu client_state nếu có
            if client_state and session_token and self.core:
                logger.debug(
                    f"Saving language {lang} to client_state "
                    f"with session_token={session_token}"
                )
                client_state["language"] = self.language
                clean_state = {
                    k: v
                    for k, v in client_state.items()
                    if isinstance(v, (str, int, float, bool, list, dict, type(None)))
                }

                if len(json.dumps(clean_state).encode()) > 1048576:
                    logger.error(
                        f"State size exceeds 1MB for session_token={session_token}"
                    )
                    if context.client.has_socket_connection:
                        with context.client:
                            ui.notify(
                                get_text(
                                    self.language,
                                    "state_too_large_error",
                                    default="Session state too large",
                                ),
                                type="negative",
                            )
                    return

                await self.core.save_client_state(session_token, clean_state)
                await self.core.log_sync_action(
                    table_name="client_states",
                    record_id=hashlib.sha256(session_token.encode()).hexdigest(),
                    action="CHANGE_LANGUAGE",
                    details={
                        "action": "set_language",
                        "language": self.language,
                    },
                    username=client_state.get("username", "unknown"),
                )

            # Gọi callback on_language_change để thông báo cho DashboardLayout
            if self.on_language_change and callable(self.on_language_change):
                logger.debug("Gọi on_language_change callback")
                try:
                    if asyncio.iscoroutinefunction(self.on_language_change):
                        await self.on_language_change(lang)
                    else:
                        self.on_language_change(lang)
                except Exception as e:
                    logger.error(
                        f"Lỗi khi gọi on_language_change: {str(e)}",
                        exc_info=True,
                    )

            # Thông báo và reload trang
            try:
                if context.client.has_socket_connection:
                    with context.client:
                        logger.debug("Hiển thị thông báo reload")
                        ui.notify(
                            get_text(
                                self.language,
                                "language_change_reload",
                                default="Language changed to {new_lang}. Reloading page...",
                                new_lang=lang,
                            ),
                            type="positive",
                            timeout=4000,  # Hiển thị thông báo trong 4 giây
                        )
                        await asyncio.sleep(4)  # Đợi để thông báo hiển thị
                        logger.debug("Thực thi reload trang bằng JavaScript (trực tiếp)")
                        ui.run_javascript("window.location.reload();")
                else:
                    logger.warning(
                        "Không có kết nối WebSocket, thử các phương án reload dự phòng"
                    )
                    # Phương án dự phòng 1: setTimeout
                    try:
                        logger.debug("Thử reload bằng setTimeout")
                        ui.run_javascript(
                            "setTimeout(() => window.location.reload(), 1000);"
                        )
                    except Exception as e:
                        logger.error(
                            f"Lỗi khi thực thi setTimeout: {str(e)}",
                            exc_info=True,
                        )
                    # Phương án dự phòng 2: window.location.href
                    try:
                        logger.debug("Thử chuyển hướng bằng window.location.href")
                        ui.run_javascript("window.location.href = window.location.href;")
                    except Exception as e:
                        logger.error(
                            f"Lỗi khi thực thi window.location.href: {str(e)}",
                            exc_info=True,
                        )
                    # Phương án dự phòng 3: ui.navigate.to
                    try:
                        logger.debug("Thử chuyển hướng bằng ui.navigate.to")
                        ui.navigate.to(ui.get_page().url)
                    except Exception as e:
                        logger.error(
                            f"Lỗi khi thực thi ui.navigate.to: {str(e)}",
                            exc_info=True,
                        )
                    # Phương án dự phòng 4: script client-side
                    try:
                        logger.debug("Thêm script client-side để bắt buộc reload")
                        ui.run_javascript(
                            """
                            if (!window._reloadTriggered) {
                                window._reloadTriggered = true;
                                setTimeout(() => {
                                    if (window.location.href === document.location.href) {
                                        window.location.reload();
                                    }
                                }, 1000);
                            }
                            """
                        )
                    except Exception as e:
                        logger.error(
                            f"Lỗi khi thêm script client-side: {str(e)}",
                            exc_info=True,
                        )

            except Exception as e:
                logger.error(f"Lỗi khi thực thi reload: {str(e)}", exc_info=True)
                if context.client.has_socket_connection:
                    with context.client:
                        ui.notify(
                            get_text(
                                self.language,
                                "reload_error",
                                default="Failed to reload page: {error_msg}",
                                error_msg=str(e),
                            ),
                            type="negative",
                        )

            await safe_ui_update()
            logger.info(f"Đã thay đổi ngôn ngữ sang {lang} và yêu cầu reload trang")

        except Exception as e:
            logger.error(f"Lỗi khi đặt ngôn ngữ: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                with context.client:
                    ui.notify(
                        get_text(
                            self.language,
                            "language_change_error",
                            default="Error changing language: {error}",
                            error=str(e),
                        ),
                        type="negative",
                    )
                    
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
            "label": get_text(self.language, name.lower() + "_tab", default=name),
            "icon": icon if name != "Chat" else "school",  # Đảm bảo icon 'school' cho tab Chat
            "render": render_func,
            "update": update_func
        }
        logger.info(f"Đã đăng ký tab: {name}")

    async def render_auth(self, on_login: Callable, on_register: Callable, fields: Dict[str, Any] = None):
        try:
            self.language = app.storage.user.get("language", "vi")
            if not self.core:
                logger.error("Core không được khởi tạo trong UIManager")
                ui.notify(get_text(self.language, "core_not_initialized", default="System error: Core not initialized"), type="negative")
                return
            if not fields:
                logger.warning("Fields không được cung cấp cho render_auth, sử dụng mặc định")
                fields = {
                    "username": {
                        "label": get_text(self.language, "username_label", default="Username"),
                        "type": "text",
                        "hint": get_text(self.language, "username_hint", default="Minimum 3 characters, letters, numbers, underscore only"),
                        "validation": {
                            "required": f"bool(x) or '{get_text(self.language, 'required_field', default='This field is required')}'",
                            "min_length": f"len(x or '') >= 3 or '{get_text(self.language, 'username_min_length', default='Minimum 3 characters')}'",
                            "pattern": f"re.match(r'^[a-zA-Z0-9_]+$', x or '') or '{get_text(self.language, 'username_pattern', default='Only letters, numbers, and underscore allowed')}'"
                        }
                    },
                    "password": {
                        "label": get_text(self.language, "password_label", default="Password"),
                        "type": "password",
                        "hint": get_text(self.language, "password_hint", default="Minimum 8 characters"),
                        "validation": {
                            "required": f"bool(x) or '{get_text(self.language, 'required_field', default='This field is required')}'",
                            "min_length": f"len(x or '') >= 8 or '{get_text(self.language, 'password_min_length', default='Minimum 8 characters')}'"
                        }
                    },
                    "confirm_password": {
                        "label": get_text(self.language, "confirm_password_label", default="Confirm Password"),
                        "type": "password",
                        "validation": {
                            "required": f"bool(x) or '{get_text(self.language, 'required_field', default='This field is required')}'"
                        }
                    },
                    "bot_password": {
                        "label": get_text(self.language, "bot_password_label", default="Bot Password"),
                        "type": "password",
                        "validation": {
                            "required": f"bool(x) or '{get_text(self.language, 'bot_password_required', default='Bot password is required')}'",
                            "min_length": f"len(x or '') >= 8 or '{get_text(self.language, 'password_min_length', default='Minimum 8 characters')}'"
                        }
                    }
                }
            auth_layout = AuthLayout(on_login, on_register, fields, core=self.core, language=self.language)
            await auth_layout.render()
        except ValueError as ve:
            error_msg = get_text(self.language, "auth_render_error", default="Error rendering auth: {error}", error=str(ve))
            logger.error(error_msg, exc_info=True)
            ui.notify(error_msg, type="negative")
        except Exception as e:
            error_msg = get_text(self.language, "system_error", default="System error: {error}", error=str(e))
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
            # Ưu tiên ngôn ngữ từ app.storage.user, sau đó từ client_state
            self.language = app.storage.user.get("language", client_state.get("language", "vi"))
            app.storage.user["language"] = self.language  # Đồng bộ với user storage
            logger.info(f"{username}: Khởi tạo dashboard với ngôn ngữ: {self.language}")

            if not self.core:
                logger.error("Core không được khởi tạo trong UIManager")
                ui.notify(get_text(self.language, "core_not_initialized", default="System error: Core not initialized"), type="negative")
                return
            if not self.registered_tabs or "Chat" not in self.registered_tabs:
                await load_tabs(self, self.core, username, client_state)
                if not self.registered_tabs:
                    logger.warning(f"{username}: Không có tab nào được đăng ký sau khi gọi load_tabs")
                    ui.notify(get_text(self.language, "no_tabs_loaded", default="No tabs loaded, please check tab configuration!"), type="warning")
                    return
            logger.info(f"{username}: Rendering dashboard với tabs: {list(self.registered_tabs.keys())}")
            if client_state.get("session_token"):
                if not re.match(r'^[a-zA-Z0-9_-]{32,}$', client_state["session_token"]):
                    logger.error(f"session_token không hợp lệ cho {username}")
                    ui.notify(get_text(self.language, "invalid_session", default="Invalid session"), type="negative")
                    return
                client_state["registered_tabs"] = list(self.registered_tabs.keys())
                client_state["language"] = self.language
                async with asyncio.timeout(30):
                    await self.core.save_client_state(client_state["session_token"], client_state)
                    await self.core.log_sync_action(
                        table_name="client_states",
                        record_id=hashlib.sha256(client_state["session_token"].encode()).hexdigest(),
                        action="SAVE_STATE",
                        details={"username": username, "action": "save_client_state", "tabs": client_state["registered_tabs"], "language": self.language},
                        username=username
                    )
            else:
                logger.warning(f"Không thể lưu client_state cho {username}: thiếu session_token")
            dashboard_layout = DashboardLayout(
                core=self.core,
                username=username,
                client_state=client_state,
                is_admin=is_admin,
                tabs=self.registered_tabs,
                on_logout=on_logout,
                on_tab_select=on_tab_select
            )
            dashboard_layout.set_ui_manager(self)
            self.on_language_change = dashboard_layout.handle_language_change
            await dashboard_layout.render(client_state)
        except asyncio.TimeoutError as e:
            error_msg = get_text(self.language, "dashboard_timeout", default="Timeout rendering dashboard for {username}: {error}", username=username, error=str(e))
            logger.error(error_msg, exc_info=True)
            ui.notify(get_text(self.language, "dashboard_timeout_error", default="Dashboard timeout error"), type="negative")
            raise
        except ValueError as ve:
            error_msg = get_text(self.language, "dashboard_error", default="Error rendering dashboard for {username}: {error}", username=username, error=str(ve))
            logger.error(error_msg, exc_info=True)
            ui.notify(error_msg, type="negative")
            raise
        except Exception as e:
            error_msg = get_text(self.language, "system_error", default="System error: {error}", error=str(e))
            if await self.core.sqlite_handler.has_permission(username, "admin_access"):
                error_msg += f"\nChi tiết: {traceback.format_exc()}"
            logger.error(error_msg, exc_info=True)
            ui.notify(error_msg, type="negative")
            raise

async def load_tabs(ui_manager: 'UIManager', core: Core, username: str, client_state: Dict[str, Any]):
    """
    Tải các tab động từ thư mục uiapp/tabs.
    
    Args:
        ui_manager: Đối tượng UIManager để đăng ký tab
        core: Đối tượng Core để truyền vào các tab
        username: Tên người dùng hiện tại
        client_state: Trạng thái client
    """
    try:
        tabs_dir = Path(__file__).parent / "tabs"
        if not tabs_dir.exists():
            logger.warning(f"Thư mục tabs không tồn tại: {tabs_dir}")
            return

        for tab_file in tabs_dir.glob("*.py"):
            if tab_file.stem.startswith("_"):
                continue
            try:
                module_name = f"uiapp.tabs.{tab_file.stem}"
                spec = spec_from_file_location(module_name, tab_file)
                if spec is None:
                    logger.error(f"Không thể tạo spec cho module {module_name}")
                    continue
                module = module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                
                # Kiểm tra và đăng ký tab
                if hasattr(module, "register_tab"):
                    module.register_tab(ui_manager, core, username, client_state)
                    logger.info(f"Đã đăng ký tab từ {tab_file.stem}")
                else:
                    logger.warning(f"Module {tab_file.stem} không có hàm register_tab")
            except Exception as e:
                logger.error(f"Lỗi khi tải tab từ {tab_file.stem}: {str(e)}", exc_info=True)
    except Exception as e:
        logger.error(f"Lỗi khi tải các tab: {str(e)}", exc_info=True)

