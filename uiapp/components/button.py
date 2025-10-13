from nicegui import ui, context, app
from typing import Callable, Optional, Dict
from uiapp.language import get_text
from utils.logging import get_logger
import asyncio
import traceback
from tenacity import retry, stop_after_attempt, wait_fixed
import time

logger = get_logger("ButtonComponent")

class ButtonComponent:
    def __init__(
        self,
        label: str,
        on_click: Callable,
        classes: str = "bg-blue-600 text-white hover:bg-blue-700",
        icon: Optional[str] = None,
        props: Optional[str] = None,
        disabled: bool = False,
        tooltip: Optional[str] = None,
        core: Optional["Core"] = None,
        client_state: Optional[Dict] = None,
        language: str = None
    ):
        """
        Khởi tạo ButtonComponent.

        Args:
            label: Nhãn của button (key cho get_text hoặc mặc định)
            on_click: Hàm xử lý sự kiện click
            classes: CSS classes cho button
            icon: Icon cho button
            props: NiceGUI props cho button
            disabled: Trạng thái vô hiệu hóa
            tooltip: Tooltip cho button (key cho get_text hoặc mặc định)
            core: Đối tượng Core
            client_state: Trạng thái client
            language: Ngôn ngữ hiện tại, ưu tiên từ client_state
        """
        self.client_state = client_state or {}
        self.language = self.client_state.get("language", app.storage.user.get("language", "vi"))
        if language and language in ["vi", "en"]:  # Chỉ cho phép ghi đè nếu hợp lệ
            self.language = language
            logger.warning(f"Ngôn ngữ được ghi đè thành {language}, client_state['language']={self.client_state.get('language')}")
        if core and not hasattr(core, "sqlite_handler"):
            raise ValueError(get_text(self.language, "invalid_core_error", default="Invalid core: Missing sqlite_handler"))
        if client_state and not isinstance(client_state, dict):
            raise ValueError(get_text(self.language, "invalid_client_state_error", default="client_state must be a dictionary"))

        self.label = label
        self.on_click = on_click
        self.classes = classes
        self.icon = icon
        self.props = props
        self.disabled = disabled
        self.tooltip = tooltip
        self.loading = False
        self.core = core
        self.button = None
        self.rendered = False
        logger.debug(f"ButtonComponent initialized with label={self.label}, language={self.language}, client_state['language']={self.client_state.get('language')}")

    def delete(self):
        """
        Xóa nút khỏi giao diện.
        """
        try:
            if self.rendered and self.button:
                self.button.delete()
                logger.debug(f"ButtonComponent {self.label}: Deleted button from UI")
                self.rendered = False
                self.button = None
            else:
                logger.warning(f"ButtonComponent {self.label}: Cannot delete, button not rendered or already deleted")
        except Exception as e:
            logger.error(f"ButtonComponent {self.label}: Error deleting button: {str(e)}", exc_info=True)
            raise
            
    async def render(self):
        """
        Render button với nhãn và tooltip theo ngôn ngữ.
        """
        try:
            if not callable(self.on_click):
                logger.error(f"Hành động nút không hợp lệ cho nút {self.label}")
                self.on_click = lambda: None  # Fallback để tránh lỗi

            if self.tooltip and (not isinstance(self.tooltip, str) or len(self.tooltip.encode()) > 1048576):
                logger.warning(f"Tooltip không hợp lệ hoặc quá dài cho nút {self.label}, bỏ qua tooltip")
                self.tooltip = None

            async def handle_click():
                start_time = time.time()
                logger.debug(f"Nút {self.label}: Bắt đầu xử lý click, loading={self.loading}, disabled={self.disabled}")
                if self.loading or self.disabled:
                    logger.debug(f"Nút {self.label}: Bị vô hiệu hóa hoặc đang tải, bỏ qua")
                    return
                self.loading = True
                self.button.set_enabled(False)  # Vô hiệu hóa nút ngay lập tức
                try:
                    logger.debug(f"Gọi hành động cho nút {self.label}, is_coroutine={asyncio.iscoroutinefunction(self.on_click)}")
                    if asyncio.iscoroutinefunction(self.on_click):
                        await self.on_click()
                    else:
                        await self.on_click()
                    logger.debug(f"Nút {self.label}: Hoàn tất hành động, thời gian: {time.time() - start_time:.2f}s")
                except Exception as e:
                    error_msg = get_text(self.language, "button_error", default="Error in button {label}: {error}", label=self.label, error=str(e))
                    if self.core and self.client_state and self.core.sqlite_handler:
                        if await self.core.sqlite_handler.has_permission(self.client_state.get("username", ""), "admin_access"):
                            error_msg += f"\nChi tiết: {traceback.format_exc()}"
                    logger.error(f"Lỗi hành động nút {self.label}: {str(e)}", exc_info=True)
                    if context.client.has_socket_connection:
                        ui.notify(error_msg, type="negative")
                finally:
                    self.loading = False
                    self.button.set_enabled(not self.disabled)  # Khôi phục trạng thái
                    await self._safe_ui_update()
                    logger.debug(f"Nút {self.label}: Hoàn tất xử lý click, loading={self.loading}, thời gian tổng: {time.time() - start_time:.2f}s")

            self.button = ui.button(
                text=get_text(self.language, self.label, default=self.label),
                on_click=handle_click
            ).classes(self.classes)
            if self.icon:
                self.button.props(f"icon={self.icon}")
            if self.props:
                self.button.props(self.props)
            if self.tooltip:
                self.button.tooltip(get_text(self.language, self.tooltip, default=self.tooltip))
            self.button.bind_enabled_from(self, "disabled", backward=lambda x: not (x or self.loading))
            self.rendered = True
            logger.debug(f"Rendered ButtonComponent with label={self.label}")
            return self.button
        except Exception as e:
            error_msg = get_text(self.language, "button_render_error", default="Error rendering button {label}: {error}", label=self.label, error=str(e))
            if self.core and self.client_state and self.core.sqlite_handler:
                if await self.core.sqlite_handler.has_permission(self.client_state.get("username", ""), "admin_access"):
                    error_msg += f"\nChi tiết: {traceback.format_exc()}"
            logger.error(f"Lỗi render nút {self.label}: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(error_msg, type="negative")
            self.button = ui.button(
                text=get_text(self.language, "button_error_fallback", default="Error"),
                on_click=lambda: None
            ).classes("bg-gray-600 text-white")
            self.rendered = True
            return self.button

    
    @retry(stop=stop_after_attempt(3), wait=wait_fixed(0.5))
    async def _safe_ui_update(self):
        """
        Cập nhật UI an toàn với kiểm tra WebSocket và cơ chế retry.
        """
        try:
            if context.client.has_socket_connection:
                await ui.context.client.connected()  # Đảm bảo client kết nối
                await asyncio.sleep(0.1)  # Thêm thời gian chờ nhỏ để nhóm cập nhật
                ui.update()
                logger.debug(
                    f"UI updated successfully for ButtonComponent {self.label}"
                )
            else:
                logger.warning(
                    f"Cannot update UI for ButtonComponent {self.label}: "
                    "client disconnected"
                )
        except Exception as e:
            logger.error(
                f"Error in safe_ui_update for ButtonComponent {self.label}: {str(e)}",
                exc_info=True
            )
            raise

    async def update(self):
        """
        Cập nhật nhãn, tooltip, classes và icon của button khi ngôn ngữ thay đổi.
        """
        try:
            if not context.client.has_socket_connection:
                logger.warning(
                    f"Cannot update ButtonComponent {self.label}: client disconnected"
                )
                return

            await ui.context.client.connected()

            if not self.rendered or not self.button:
                logger.warning(
                    f"ButtonComponent not rendered or button is None for {self.label}, "
                    "re-rendering"
                )
                await self.render()
                return

            # Cập nhật nhãn
            new_label = get_text(self.language, self.label, default=self.label)
            self.button.set_text(new_label)

            # Cập nhật tooltip
            if self.tooltip:
                new_tooltip = get_text(
                    self.language, self.tooltip, default=self.tooltip
                )
                self.button.tooltip(new_tooltip)

            # Cập nhật classes
            self.button.classes(remove=self.classes, add=self.classes)

            # Cập nhật icon
            if self.icon:
                self.button.props(f"icon={self.icon}")
            else:
                self.button.props(remove="icon")

            # Cập nhật trạng thái enabled
            self.button.bind_enabled_from(
                self,
                "disabled",
                backward=lambda x: not (x or self.loading)
            )

            logger.debug(
                f"Updated ButtonComponent with label={new_label}, "
                f"language={self.language}, classes={self.classes}, "
                f"icon={self.icon}, enabled={not (self.disabled or self.loading)}"
            )

            await self._safe_ui_update()

        except Exception as e:
            logger.error(
                f"Error updating ButtonComponent {self.label}: {str(e)}",
                exc_info=True
            )
            if context.client.has_socket_connection:
                ui.notify(
                    get_text(
                        self.language,
                        "button_update_error",
                        default="Error updating button {label}: {error}",
                        label=self.label,
                        error=str(e)
                    ),
                    type="negative"
                )
                