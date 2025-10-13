

from typing import List, Dict, Callable, Optional
from nicegui import ui
from uiapp.language import get_text
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
    def __init__(self, tabs: List[Dict], on_select: Callable, active_tab: Optional[str] = None, classes: str = "w-64 bg-gray-100 p-4 h-screen", core: Optional['Core'] = None, client_state: Optional[Dict] = None, language: str = "vi"):
        self.tabs = tabs
        self.on_select = on_select
        self.active_tab = active_tab
        self.classes = classes
        self.loading = False
        self.core = core
        self.client_state = client_state or {}
        self.language = language
        self.button_elements = {}
        self.drawer = None  # Store the drawer for updates
        logger.debug(get_text(self.language, 'sidebar_init', default='SidebarComponent initialized with tabs: {tabs}, user={user}',
                             tabs=[tab['name'] for tab in tabs], user=self.client_state.get('username', 'unknown')))

    async def render(self):
        logger.debug(get_text(self.language, 'sidebar_render_start', default='Rendering SidebarComponent with tabs={tabs}, user={user}',
                             tabs=[tab['name'] for tab in self.tabs], user=self.client_state.get('username', 'unknown')))
        if not callable(self.on_select):
            ui.notify(get_text(self.language, 'invalid_on_select', default='Error: Invalid tab selection action'), type="negative")
            logger.error(get_text(self.language, 'invalid_on_select', default='Invalid tab selection action'))
            return
        try:
            # Clear previous drawer if it exists
            if self.drawer:
                self.drawer.clear()
                self.drawer.delete()
                logger.debug(f"{self.client_state.get('username', 'unknown')}: Cleared previous drawer")

            self.drawer = ui.left_drawer(fixed=False).classes(self.classes)
            ui.button(icon='menu').on('click', lambda: self.drawer.toggle()).classes('m-2')
            with self.drawer:
                ui.label(get_text(self.language, 'sidebar_title', default='Menu')).classes("text-xl font-bold mb-4")
                if self.loading:
                    ui.linear_progress(show_value=False).classes('w-full')
                if not self.tabs:
                    ui.label(get_text(self.language, 'no_tabs_configured', default='No tabs configured')).classes("text-gray-500")
                    logger.info(get_text(self.language, 'no_tabs_default', default='Displayed empty sidebar due to no tabs'))
                    return
                self.button_elements = {}  # Reset button elements
                for tab in self.tabs:
                    tab_name = tab['name']
                    display_name = get_text(self.language, f"{tab_name.lower()}_tab", default=tab_name)
                    logger.debug(get_text(self.language, 'rendering_tab_button', default='Rendering button for tab {tab_name}', tab_name=tab_name))
                    if len(tab_name) < 3 or not re.match(r'^[a-zA-Z0-9_]+$', tab_name):
                        ui.notify(get_text(self.language, 'invalid_tab_name', default='Invalid tab name: {tab_name}', tab_name=tab_name), type="negative")
                        logger.error(get_text(self.language, 'invalid_tab_name', default='Invalid tab name: {tab_name}', tab_name=tab_name))
                        continue
                    with ui.element("div").classes("mb-2"):
                        async def select_tab(t=tab_name):
                            logger.debug(get_text(self.language, 'select_tab_called', default='select_tab called for tab {tab_name} by user {user}',
                                                 tab_name=t, user=self.client_state.get('username', 'unknown')))
                            if self.loading:
                                logger.warning(get_text(self.language, 'select_tab_skipped', default='select_tab skipped due to loading=True for tab {tab_name}', tab_name=t))
                                return
                            self.loading = True
                            try:
                                async with asyncio.timeout(60):
                                    if self.core and self.client_state:
                                        if not self.client_state.get("session_token"):
                                            ui.notify(get_text(self.language, 'invalid_session', default='Error: Invalid session'), type="negative")
                                            logger.error(get_text(self.language, 'no_session_token', default='session_token does not exist'))
                                            return
                                        clean_state = {k: v for k, v in self.client_state.items() if isinstance(v, (str, int, float, bool, list, dict, type(None)))}
                                        if len(json.dumps(clean_state).encode()) > 1048576:
                                            ui.notify(get_text(self.language, 'state_too_large_error', default='Session state too large'), type="negative")
                                            logger.error(get_text(self.language, 'state_too_large', default='State size exceeds 1MB for {user}',
                                                                 user=self.client_state.get('username', '')))
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
                                        logger.debug(get_text(self.language, 'saved_client_state', default='{user}: Saved client_state to SQLite: selected_tab={tab}',
                                                             user=self.client_state.get('username', ''), tab=t))
                                    if asyncio.iscoroutinefunction(self.on_select):
                                        await self.on_select(t)
                                    else:
                                        self.on_select(t)
                                    self.active_tab = t
                                    for tab_name, button in self.button_elements.items():
                                        new_classes = "w-full text-left bg-blue-600 text-white" if tab_name == t else "w-full text-left"
                                        button.classes(remove="w-full text-left bg-blue-600 text-white", add=new_classes)
                                    ui.update()
                                    logger.info(get_text(self.language, 'tab_changed', default='{user}: Tab changed via sidebar: selected_tab={tab}, button_elements={buttons}',
                                                        user=self.client_state.get('username', ''), tab=self.client_state.get('selected_tab'), buttons=list(self.button_elements.keys())))
                            except asyncio.TimeoutError as e:
                                error_msg = get_text(self.language, 'tab_change_timeout', default='Timeout selecting tab {tab_name}: {error}', tab_name=t, error=str(e))
                                ui.notify(error_msg, type="negative")
                                logger.error(error_msg, exc_info=True)
                            except TypeError as e:
                                error_msg = get_text(self.language, 'json_tab_error', default='JSON error selecting tab {tab_name}: {error}', tab_name=t, error=str(e))
                                ui.notify(error_msg, type="negative")
                                logger.error(error_msg, exc_info=True)
                            except Exception as e:
                                error_msg = get_text(self.language, 'tab_select_error', default='Error selecting tab {tab_name}: {error}', tab_name=t, error=str(e))
                                if self.core and self.client_state and await self.core.sqlite_handler.has_permission(self.client_state.get("username", ""), "admin_access"):
                                    error_msg += get_text(self.language, 'details', default='Details') + f": {traceback.format_exc()}"
                                ui.notify(error_msg, type="negative")
                                logger.error(error_msg, exc_info=True)
                            finally:
                                self.loading = False
                                ui.update()
                        classes = "w-full text-left bg-blue-600 text-white" if tab_name == self.client_state.get("selected_tab", self.active_tab) else "w-full text-left"
                        btn = ButtonComponent(
                            label=display_name,
                            on_click=select_tab,
                            icon=tab.get("icon", "extension"),
                            classes=classes,
                            props="flat",
                            core=self.core,
                            client_state=self.client_state,
                            language=self.language  # Pass language to ButtonComponent
                        )
                        button = await btn.render()
                        if button:
                            self.button_elements[tab_name] = button
                            logger.debug(get_text(self.language, 'button_created', default='Button created for tab {tab_name}: {classes}', tab_name=tab_name, classes=classes))
        except Exception as e:
            error_msg = get_text(self.language, 'sidebar_render_error', default='Error rendering sidebar: {error}', error=str(e))
            if self.core and self.client_state and await self.core.sqlite_handler.has_permission(self.client_state.get("username", ""), "admin_access"):
                error_msg += get_text(self.language, 'details', default='Details') + f": {traceback.format_exc()}"
            ui.notify(error_msg, type="negative")
            logger.error(error_msg, exc_info=True)

    
    
    
    # uiapp/components/sidebar.py
    async def update(self):
        logger.debug(
            f"{self.client_state.get('username', 'unknown')}: Updating SidebarComponent"
        )
        try:
            if not context.client.has_socket_connection:
                logger.warning(
                    f"{self.client_state.get('username', 'unknown')}: "
                    "Cannot update SidebarComponent: client disconnected"
                )
                return

            await ui.context.client.connected()

            if not self.drawer:
                logger.warning(
                    f"{self.client_state.get('username', 'unknown')}: "
                    "Sidebar not rendered, calling render"
                )
                await self.render()
                return

            self.drawer.clear()
            self.button_elements = {}  # Reset button elements
            logger.debug(
                f"{self.client_state.get('username', 'unknown')}: "
                "Cleared drawer content"
            )

            with self.drawer:
                ui.label(
                    get_text(
                        self.language,
                        "sidebar_title",
                        default="Menu",
                    )
                ).classes("text-xl font-bold mb-4")

                if self.loading:
                    ui.linear_progress(show_value=False).classes("w-full")

                if not self.tabs:
                    ui.label(
                        get_text(
                            self.language,
                            "no_tabs_configured",
                            default="No tabs configured",
                        )
                    ).classes("text-gray-500")
                    logger.info(
                        get_text(
                            self.language,
                            "no_tabs_default",
                            default="Displayed empty sidebar due to no tabs",
                        )
                    )
                    return

                for tab in self.tabs:
                    tab_name = tab["name"]
                    display_name = get_text(
                        self.language,
                        f"{tab_name.lower()}_tab",
                        default=tab_name,
                    )

                    async def select_tab(t=tab_name):
                        logger.debug(
                            get_text(
                                self.language,
                                "select_tab_called",
                                default=(
                                    "select_tab called for tab {tab_name} "
                                    "by user {user}"
                                ),
                                tab_name=t,
                                user=self.client_state.get(
                                    "username", "unknown"
                                ),
                            )
                        )
                        if self.loading:
                            logger.warning(
                                get_text(
                                    self.language,
                                    "select_tab_skipped",
                                    default=(
                                        "select_tab skipped due to "
                                        "loading=True for tab {tab_name}"
                                    ),
                                    tab_name=t,
                                )
                            )
                            return
                        self.loading = True
                        try:
                            async with asyncio.timeout(60):
                                if self.core and self.client_state:
                                    if not self.client_state.get(
                                        "session_token"
                                    ):
                                        ui.notify(
                                            get_text(
                                                self.language,
                                                "invalid_session",
                                                default=(
                                                    "Error: Invalid session"
                                                ),
                                            ),
                                            type="negative",
                                        )
                                        logger.error(
                                            get_text(
                                                self.language,
                                                "no_session_token",
                                                default=(
                                                    "session_token does not exist"
                                                ),
                                            )
                                        )
                                        return
                                    clean_state = {
                                        k: v
                                        for k, v in self.client_state.items()
                                        if isinstance(
                                            v,
                                            (
                                                str,
                                                int,
                                                float,
                                                bool,
                                                list,
                                                dict,
                                                type(None),
                                            ),
                                        )
                                    }
                                    if len(json.dumps(clean_state).encode()) > 1048576:
                                        ui.notify(
                                            get_text(
                                                self.language,
                                                "state_too_large_error",
                                                default="Session state too large",
                                            ),
                                            type="negative",
                                        )
                                        logger.error(
                                            get_text(
                                                self.language,
                                                "state_too_large",
                                                default=(
                                                    "State size exceeds 1MB "
                                                    "for {user}"
                                                ),
                                                user=self.client_state.get(
                                                    "username", ""
                                                ),
                                            )
                                        )
                                        return
                                    self.client_state["selected_tab"] = t
                                    await self.core.save_client_state(
                                        self.client_state.get(
                                            "session_token", ""
                                        ),
                                        clean_state,
                                    )
                                    await self.core.log_sync_action(
                                        table_name="client_states",
                                        record_id=hashlib.sha256(
                                            self.client_state[
                                                "session_token"
                                            ].encode()
                                        ).hexdigest(),
                                        action="SELECT_TAB",
                                        details={
                                            "username": self.client_state.get(
                                                "username", ""
                                            ),
                                            "tab": t,
                                        },
                                        username=self.client_state.get(
                                            "username", ""
                                        ),
                                    )
                                    logger.debug(
                                        get_text(
                                            self.language,
                                            "saved_client_state",
                                            default=(
                                                "{user}: Saved client_state "
                                                "to SQLite: selected_tab={tab}"
                                            ),
                                            user=self.client_state.get(
                                                "username", ""
                                            ),
                                            tab=t,
                                        )
                                    )
                                if asyncio.iscoroutinefunction(self.on_select):
                                    await self.on_select(t)
                                else:
                                    self.on_select(t)
                                self.active_tab = t
                                for tab_name, button in (
                                    self.button_elements.items()
                                ):
                                    new_classes = (
                                        "w-full text-left bg-blue-600 text-white"
                                        if tab_name == t
                                        else "w-full text-left"
                                    )
                                    button.classes(
                                        remove=(
                                            "w-full text-left bg-blue-600 text-white"
                                        ),
                                        add=new_classes,
                                    )
                                await safe_ui_update()
                                logger.info(
                                    get_text(
                                        self.language,
                                        "tab_changed",
                                        default=(
                                            "{user}: Tab changed via sidebar: "
                                            "selected_tab={tab}, "
                                            "button_elements={buttons}"
                                        ),
                                        user=self.client_state.get(
                                            "username", ""
                                        ),
                                        tab=self.client_state.get(
                                            "selected_tab"
                                        ),
                                        buttons=list(
                                            self.button_elements.keys()
                                        ),
                                    )
                                )
                        except asyncio.TimeoutError as e:
                            error_msg = get_text(
                                self.language,
                                "tab_change_timeout",
                                default=(
                                    "Timeout selecting tab {tab_name}: {error}"
                                ),
                                tab_name=t,
                                error=str(e),
                            )
                            ui.notify(error_msg, type="negative")
                            logger.error(error_msg, exc_info=True)
                        except TypeError as e:
                            error_msg = get_text(
                                self.language,
                                "json_tab_error",
                                default=(
                                    "JSON error selecting tab {tab_name}: {error}"
                                ),
                                tab_name=t,
                                error=str(e),
                            )
                            ui.notify(error_msg, type="negative")
                            logger.error(error_msg, exc_info=True)
                        except Exception as e:
                            error_msg = get_text(
                                self.language,
                                "tab_select_error",
                                default=(
                                    "Error selecting tab {tab_name}: {error}"
                                ),
                                tab_name=t,
                                error=str(e),
                            )
                            if (
                                self.core
                                and self.client_state
                                and await self.core.sqlite_handler.has_permission(
                                    self.client_state.get("username", ""),
                                    "admin_access",
                                )
                            ):
                                error_msg += (
                                    get_text(
                                        self.language,
                                        "details",
                                        default="Details",
                                    )
                                    + f": {traceback.format_exc()}"
                                )
                            ui.notify(error_msg, type="negative")
                            logger.error(error_msg, exc_info=True)
                        finally:
                            self.loading = False
                            await safe_ui_update()

                    classes = (
                        "w-full text-left bg-blue-600 text-white"
                        if tab_name
                        == self.client_state.get("selected_tab", self.active_tab)
                        else "w-full text-left"
                    )
                    btn = ButtonComponent(
                        label=display_name,
                        on_click=select_tab,
                        icon=tab.get("icon", "extension"),
                        classes=classes,
                        props="flat",
                        core=self.core,
                        client_state=self.client_state,
                        language=self.language,
                    )
                    button = await btn.render()
                    if button:
                        self.button_elements[tab_name] = button
                        logger.debug(
                            get_text(
                                self.language,
                                "button_created",
                                default=(
                                    "Button created for tab {tab_name}: {classes}"
                                ),
                                tab_name=tab_name,
                                classes=classes,
                            )
                        )

                ui.label(
                    get_text(
                        self.language,
                        "sidebar_footer",
                        default="Dashboard",
                    )
                ).classes("text-sm text-gray-500 mt-4")

            await safe_ui_update()
            logger.info(
                f"{self.client_state.get('username', 'unknown')}: "
                "SidebarComponent updated successfully"
            )

        except Exception as e:
            error_msg = get_text(
                self.language,
                "sidebar_update_error",
                default="Error updating sidebar: {error}",
                error=str(e),
            )
            logger.error(error_msg, exc_info=True)
            ui.notify(error_msg, type="negative")
            