from nicegui import ui
from typing import Callable, Optional, Dict
from utils.logging import get_logger
import asyncio
import traceback

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
        client_state: Optional[Dict] = None
    ):
        if core and not hasattr(core, "sqlite_handler"):
            raise ValueError("Invalid core object: Missing sqlite_handler")
        if client_state and not isinstance(client_state, dict):
            raise ValueError("client_state must be a dictionary")

        self.label = label
        self.on_click = on_click
        self.classes = classes
        self.icon = icon
        self.props = props
        self.disabled = disabled
        self.tooltip = tooltip
        self.loading = False
        self.core = core
        self.client_state = client_state or {}

    async def render(self):
        if not callable(self.on_click):
            logger.error(f"Hành động nút không hợp lệ cho nút {self.label}")
            return None  # Sửa: return None nếu invalid
        if self.tooltip:
            if not isinstance(self.tooltip, str):
                logger.error(f"Tooltip không phải chuỗi cho nút {self.label}")
                return None
            if len(self.tooltip.encode()) > 1048576:
                logger.error(f"Tooltip quá dài cho nút {self.label}")
                return None
        try:
            async def handle_click():
                if self.loading or self.disabled:
                    logger.debug(f"Nút {self.label} đang bị vô hiệu hóa hoặc đang tải")
                    return
                self.loading = True
                progress = ui.linear_progress(show_value=False).classes("w-full")
                try:
                    logger.debug(f"Gọi hành động cho nút {self.label}, is_coroutine: {asyncio.iscoroutinefunction(self.on_click)}")
                    if asyncio.iscoroutinefunction(self.on_click):
                        await self.on_click()
                    else:
                        self.on_click()
                except Exception as e:
                    error_msg = f"Lỗi hành động nút {self.label}: {str(e)}"
                    if self.core and self.client_state and self.core.sqlite_handler:  # Sửa: await nếu cần, nhưng giả sử sync
                        if await self.core.sqlite_handler.has_permission(  # Giữ await nếu async
                            self.client_state.get("username", ""), "admin_access"
                        ):
                            error_msg += f"\nChi tiết: {traceback.format_exc()}"
                    logger.error(f"Lỗi hành động nút {self.label}: {str(e)}", exc_info=True)
                    ui.notify(error_msg, type="negative")  # Thêm notify nếu chưa có
                finally:
                    self.loading = False
                    progress.delete()
                    ui.update()  # Thêm: Force update sau finally để sync disabled/loading

            # Sửa classes: Thêm ! cho override Quasar (nếu là active classes)
            # Giả sử classes từ Sidebar đã có !, nhưng nếu không, force ở đây nếu cần
            button = ui.button(self.label, on_click=handle_click).classes(self.classes)
            if self.icon:
                button.props(f'icon={self.icon}')  # Sửa: icon với dấu nháy đơn
            if self.props:
                button.props(self.props)
            if self.tooltip:
                button.tooltip(self.tooltip)
            button.bind_enabled_from(self, "disabled", backward=lambda x: not (x or self.loading))
            
            return button  # SỬA QUAN TRỌNG: Return button để Sidebar có thể lưu và update
        except Exception as e:
            error_msg = f"Lỗi render nút {self.label}: {str(e)}"
            if self.core and self.client_state and self.core.sqlite_handler:
                if await self.core.sqlite_handler.has_permission(
                    self.client_state.get("username", ""), "admin_access"
                ):
                    error_msg += f"\nChi tiết: {traceback.format_exc()}"
            ui.notify(error_msg, type="negative")  # Thêm notify
            logger.error(f"Lỗi render nút {self.label}: {str(e)}", exc_info=True)
            return None