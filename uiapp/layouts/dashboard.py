
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
from uiapp.language import get_text

logger = get_logger("Dashboard")

class DashboardLayout:
    def __init__(
        self,
        core: Core,
        username: str,
        client_state: Dict,
        is_admin: bool,
        tabs: Dict[str, Dict],
        on_logout: Callable,
        on_tab_select: Optional[Callable] = None
    ):
        if not hasattr(core, 'sqlite_handler') or not hasattr(core, 'firestore_handler'):
            raise ValueError(get_text(client_state.get("language", "vi"), "invalid_core_error", default="Error: Invalid core object, missing sqlite_handler or firestore_handler"))
        if not isinstance(username, str) or not re.match(r'^[a-zA-Z0-9_]+$', username):
            logger.error(get_text(client_state.get("language", "vi"), 'invalid_username_log', default='username must be a valid string, received: {type}', type=str(type(username))))
            raise ValueError(get_text(client_state.get("language", "vi"), "invalid_username_error", default="Error: Invalid username"))
        self.core = core
        self.username = username
        self.is_admin = is_admin
        self.tabs = tabs
        self.on_logout = on_logout
        self.on_tab_select = on_tab_select
        self.client_state = client_state
        self.language = client_state.get("language", "vi")
        self.ui_manager = None
        self.header = None
        self.sidebar = None
        logger.debug(get_text(self.language, 'dashboard_init', default='DashboardLayout initialized for user={user}', user=self.username))

    def set_ui_manager(self, ui_manager):
        self.ui_manager = ui_manager
        logger.debug(get_text(self.language, 'ui_manager_set', default='UIManager assigned to DashboardLayout'))

    
    
    
    
    # uiapp/layouts/dashboard.py
    async def handle_language_change(self, new_language: str):
        logger.debug(
            get_text(
                self.language,
                "dashboard_language_change",
                default="Changing language to {new_lang} for user {user}",
                new_lang=new_language,
                user=self.username,
            )
        )
        try:
            if not context.client.has_socket_connection:
                logger.warning(
                    f"Cannot change language to {new_language}: client disconnected"
                )
                return

            await ui.context.client.connected()

            if new_language not in ["vi", "en"]:
                error_msg = get_text(
                    self.language,
                    "invalid_language",
                    default="Invalid language: {new_lang}",
                    new_lang=new_language,
                )
                logger.error(error_msg)
                ui.notify(error_msg, type="negative")
                return

            self.language = new_language
            self.client_state["language"] = new_language
            app.storage.user["language"] = new_language

            # Cập nhật ngôn ngữ trong UIManager
            if self.ui_manager:
                await self.ui_manager.set_language(
                    new_language,
                    self.client_state,
                    self.client_state.get("session_token", ""),
                )

            # Cập nhật header
            if self.header:
                await self.header.handle_language_change(new_language)

            # Cập nhật sidebar
            if self.sidebar:
                self.sidebar.language = new_language
                await self.sidebar.update()

            # Cập nhật các tab
            if self.header:
                await self.header.update_components_after_language_change()
            else:
                logger.warning(
                    f"{self.username}: Không có header, tự cập nhật các tab"
                )
                for tab_name, tab_info in self.ui_manager.registered_tabs.items():
                    update_func = tab_info.get("update")
                    render_func = tab_info.get("render")
                    if update_func and callable(update_func):
                        logger.debug(f"Gọi update_func cho tab {tab_name}")
                        try:
                            if asyncio.iscoroutinefunction(update_func):
                                await update_func(
                                    self.core,
                                    self.username,
                                    self.is_admin,
                                    self.client_state,
                                )
                            else:
                                update_func(
                                    self.core,
                                    self.username,
                                    self.is_admin,
                                    self.client_state,
                                )
                            logger.info(f"Cập nhật tab {tab_name} thành công")
                        except Exception as e:
                            logger.error(
                                f"Lỗi khi gọi update_func cho tab {tab_name}: {str(e)}",
                                exc_info=True,
                            )
                            if context.client.has_socket_connection:
                                ui.notify(
                                    get_text(
                                        self.language,
                                        "tab_update_error",
                                        default="Error updating tab {tab_name}: {error}",
                                        tab_name=tab_name,
                                        error=str(e),
                                    ),
                                    type="negative",
                                )
                            if (
                                render_func
                                and callable(render_func)
                                and asyncio.iscoroutinefunction(render_func)
                            ):
                                logger.debug(
                                    f"Thử gọi render_func cho tab {tab_name}"
                                )
                                try:
                                    await render_func(
                                        self.core,
                                        self.username,
                                        self.is_admin,
                                        self.client_state,
                                    )
                                    logger.info(
                                        f"Render lại tab {tab_name} thành công"
                                    )
                                except Exception as e:
                                    logger.error(
                                        f"Lỗi khi gọi render_func cho tab {tab_name}: {str(e)}",
                                        exc_info=True,
                                    )
                                    if context.client.has_socket_connection:
                                        ui.notify(
                                            get_text(
                                                self.language,
                                                "tab_render_error",
                                                default="Error rendering tab {tab_name}: {error}",
                                                tab_name=tab_name,
                                                error=str(e),
                                            ),
                                            type="negative",
                                        )

            logger.info(
                get_text(
                    new_language,
                    "dashboard_language_changed",
                    default="Language changed to {new_lang} for user {user}",
                    new_lang=new_language,
                    user=self.username,
                )
            )

            await safe_ui_update()

        except Exception as e:
            error_msg = get_text(
                self.language,
                "dashboard_language_change_error",
                default="Error changing language: {error}",
                error=str(e),
            )
            logger.error(error_msg, exc_info=True)
            ui.notify(error_msg, type="negative")
            
    async def render(self, client_state: Dict):
        logger.debug(get_text(self.language, 'render_called', default='render() called with client_state: {state}', state=client_state))
        if not isinstance(client_state, dict):
            logger.error(get_text(self.language, 'invalid_client_state', default='client_state is not a dictionary'))
            ui.notify(get_text(self.language, "invalid_client_state_error", default="Error: Invalid session state"), type="negative")
            return
        
        try:
            async with asyncio.timeout(120):
                check_disk_space()
                self.client_state = client_state.copy() if client_state else {}
                self.client_state["language"] = self.language
                self.client_state = {k: v for k, v in self.client_state.items() if isinstance(v, (str, int, float, bool, list, dict, type(None)))}
                state_json = json.dumps(self.client_state, ensure_ascii=False)
                if len(state_json.encode()) > 1_000_000:
                    logger.error(get_text(self.language, 'state_too_large', default='State size exceeds 1MB'))
                    ui.notify(get_text(self.language, "state_too_large_error", default="Error: Session state too large"), type="negative")
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
                            logger.info(get_text(self.language, 'no_client_state', default='No client_state found, creating new'))
                            self.client_state["selected_tab"] = (
                                get_text(self.language, "chat_tab", default="Chat") if get_text(self.language, "chat_tab", default="Chat") in self.tabs
                                else list(self.tabs.keys())[0]
                                if self.tabs else None
                            )
                        else:
                            logger.info(get_text(self.language, 'load_client_state', default='Loaded client_state from SQLite'))
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
                            logger.info(get_text(self.language, 'updated_tabs', default='Updated client_state tabs with: {tabs}', tabs=list(self.tabs.keys())))
                            for tab_name in self.tabs:
                                update_func = self.ui_manager.registered_tabs.get(tab_name, {}).get("update")
                                logger.debug(get_text(self.language, 'tab_update_func', default='Tab {tab_name} has update_func: {has_func}', tab_name=tab_name, has_func=update_func is not None))

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
                            logger.info(get_text(self.language, 'load_sync_status', default='Loaded sync status for admin'))

                    if not self.ui_manager:
                        logger.error(get_text(self.language, 'no_ui_manager', default='UIManager not assigned to DashboardLayout'))
                        ui.notify(get_text(self.language, "invalid_dashboard_config", default="Error: Invalid dashboard configuration"), type="negative")
                        return

                    self.header = HeaderComponent(
                        username=self.username,
                        on_logout=self.on_logout,
                        is_admin=self.is_admin,
                        on_sync_to_sqlite=self.core.sync_to_sqlite,
                        on_sync_from_sqlite=self.core.sync_from_sqlite,
                        core=self.core,
                        client_state=self.client_state,
                        ui_manager=self.ui_manager
                    )
                    await self.header.render()
                    self.sidebar = SidebarComponent(
                        tabs=[{"name": tab["name"], "icon": tab["icon"]} for tab in self.tabs.values()],
                        on_select=self.handle_tab_change,
                        core=self.core,
                        client_state=self.client_state,
                        language=self.language
                    )
                    await self.sidebar.render()
                    with ui.card().classes('w-full p-4'):
                        if not self.tabs:
                            ui.label(get_text(self.language, "welcome_dashboard", default="Welcome to Dashboard")).classes("text-2xl font-bold")
                            ui.label(
                                get_text(self.language, "no_tabs_configured", default="No tabs configured. Please add tabs in the uiapp directory.")
                            ).classes("text-lg text-gray-500")
                            logger.info(get_text(self.language, 'no_tabs_default', default='Displayed default content due to no tabs'))
                        else:
                            with ui.tabs().classes('dense w-full') as tabs:
                                for tab_name, tab_info in self.tabs.items():
                                    if len(tab_name) < 3 or not re.match(r'^[a-zA-Z0-9_]+$', tab_name):
                                        logger.error(get_text(self.language, 'invalid_tab_name', default='Invalid tab name: {tab_name}', tab_name=tab_name))
                                        continue
                                    if not isinstance(tab_info, dict) or "icon" not in tab_info or "name" not in tab_info:
                                        logger.error(get_text(self.language, 'invalid_tab_config', default='Invalid tab configuration: {tab_name}', tab_name=tab_name))
                                        continue
                                    ui.tab(tab_name, icon=tab_info["icon"]).classes('no-caps')
                            with ui.tab_panels(
                                tabs,
                                value=self.client_state.get(
                                    "selected_tab",
                                    get_text(self.language, "chat_tab", default="Chat") if get_text(self.language, "chat_tab", default="Chat") in self.tabs else list(self.tabs.keys())[0] if self.tabs else None
                                )
                            ).classes('w-full') as tab_panels:
                                tab_panels.bind_value(self.client_state, "selected_tab")
                                for tab_name, tab_info in self.tabs.items():
                                    with ui.tab_panel(tab_name):
                                        try:
                                            render_func = self.ui_manager.registered_tabs.get(tab_name, {}).get("render")
                                            if not render_func or not asyncio.iscoroutinefunction(render_func):
                                                logger.error(
                                                    get_text(self.language, 'invalid_render_func', default='render_func for tab {tab_name} is invalid or not async', tab_name=tab_name)
                                                )
                                                ui.notify(get_text(self.language, "load_tab_error", default="Error: Cannot load tab {tab_name}", tab_name=tab_name), type="negative")
                                                continue
                                            logger.debug(get_text(self.language, 'rendering_tab', default='Rendering tab {tab_name}', tab_name=tab_name))
                                            await render_func(self.core, self.username, self.is_admin, self.client_state)
                                            update_func = self.ui_manager.registered_tabs.get(tab_name, {}).get("update")
                                            if update_func and callable(update_func):
                                                if asyncio.iscoroutinefunction(update_func):
                                                    await update_func(self.core, self.username, self.is_admin, self.client_state)
                                                else:
                                                    update_func(self.core, self.username, self.is_admin, self.client_state)
                                        except Exception as e:
                                            error_msg = get_text(self.language, "render_tab_error", default="Error rendering tab {tab_name}: {error}", tab_name=tab_name, error=str(e))
                                            if self.is_admin:
                                                error_msg += get_text(self.language, 'details', default='Details') + f": {traceback.format_exc()}"
                                            ui.notify(error_msg, type="negative")
                                            logger.error(f"{self.username}: {error_msg}", exc_info=True)
        except asyncio.TimeoutError as e:
            logger.error(get_text(self.language, 'dashboard_timeout', default='Timeout rendering dashboard: {error}', error=str(e)), exc_info=True)
            ui.notify(get_text(self.language, "dashboard_timeout_error", default="Timeout loading dashboard, please try again!"), type="negative")
        except TypeError as e:
            logger.error(get_text(self.language, 'json_error', default='JSON error rendering dashboard: {error}', error=str(e)), exc_info=True)
            ui.notify(get_text(self.language, "session_data_error", default="Session data error: {error}", error=str(e)), type="negative")
        except Exception as e:
            error_msg = get_text(self.language, "display_dashboard_error", default="Error displaying dashboard: {error}", error=str(e))
            if self.is_admin:
                error_msg += get_text(self.language, 'details', default='Details') + f": {traceback.format_exc()}"
            ui.notify(error_msg, type="negative")
            logger.error(f"{self.username}: {error_msg}", exc_info=True)

    async def handle_tab_change(self, tab_name):
        try:
            async with asyncio.timeout(30):
                if not tab_name or not re.match(r'^[a-zA-Z0-9_]+$', tab_name):
                    logger.error(get_text(self.language, 'invalid_tab_name', default='Invalid tab name: {tab_name}', tab_name=tab_name))
                    ui.notify(get_text(self.language, "invalid_tab_name_error", default="Error: Invalid tab name"), type="negative")
                    return
                if not self.client_state or "session_token" not in self.client_state:
                    logger.error(get_text(self.language, 'invalid_client_state_session', default='client_state is invalid or missing session_token'))
                    ui.notify(get_text(self.language, "invalid_session_state_error", default="Error: Invalid session state"), type="negative")
                    return
                session_token = self.client_state.get("session_token", "")
                if not re.match(r'^[a-zA-Z0-9_-]{32,}$', session_token):
                    logger.error(get_text(self.language, 'invalid_session_token', default='Invalid session_token: {token}', token=session_token))
                    ui.notify(get_text(self.language, "invalid_session", default="Error: Invalid session"), type="negative")
                    return
                self.client_state["selected_tab"] = tab_name
                self.client_state["timestamp"] = int(time.time())
                clean_state = {k: v for k, v in self.client_state.items() if isinstance(v, (str, int, float, bool, list, dict, type(None)))}
                state_json = json.dumps(clean_state, ensure_ascii=False)
                if len(state_json.encode()) > 1_000_000:
                    logger.error(get_text(self.language, 'state_too_large', default='State size exceeds 1MB'))
                    ui.notify(get_text(self.language, "state_too_large_error", default="Error: Session state too large"), type="negative")
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
                logger.info(get_text(self.language, 'saved_selected_tab', default='Saved selected_tab={tab_name} to client_states', tab_name=tab_name))
        except asyncio.TimeoutError:
            ui.notify(get_text(self.language, "tab_change_timeout", default="Timeout processing tab change"), type="negative")
            logger.error(get_text(self.language, 'tab_change_timeout_log', default='Timeout processing tab change'), exc_info=True)
        except TypeError as e:
            logger.error(get_text(self.language, 'json_tab_error', default='JSON error saving selected_tab: {error}', error=str(e)), exc_info=True)
            ui.notify(get_text(self.language, "session_data_error", default="Session data error: {error}", error=str(e)), type="negative")
        except Exception as e:
            error_msg = get_text(self.language, "tab_change_error", default="Error processing tab change: {error}", error=str(e))
            if self.is_admin:
                error_msg += get_text(self.language, 'details', default='Details') + f": {traceback.format_exc()}"
            ui.notify(error_msg, type="negative")
            logger.error(f"{self.username}: {error_msg}", exc_info=True)
