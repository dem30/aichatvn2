
from nicegui import ui, context
from typing import Callable, Optional, List, Dict, Union, Tuple
from utils.logging import get_logger
import asyncio
import json
import traceback
from config import Config
from .button import ButtonComponent

logger = get_logger("TableComponent")

class TableComponent:
    def __init__(self, data: Union[Callable[[Optional[float], int, int], Tuple[List[Dict], int]], List[Dict]], 
                 columns: List[Dict], title: str = "", pagination: Optional[Dict] = None, 
                 row_actions: Optional[List[Dict]] = None, sortable: bool = False, 
                 filterable: bool = False, format_callback: Dict[str, Callable] = None,
                 core: Optional['Core'] = None, client_state: Optional[Dict] = None):
        self.data = data
        self.columns = columns
        self.title = title
        self.pagination = pagination or {"rowsPerPage": 10, "total_count": 0}
        self.row_actions = row_actions or []
        self.sortable = sortable
        self.filterable = filterable
        self.format_callback = format_callback or {}
        self.loading = False
        self.container = None
        self.table = None
        self.core = core
        self.client_state = client_state
        self.username = client_state.get("username", "unknown") if client_state else "unknown"

    
    async def fetch_data(self, page: int = 1, page_size: int = 10) -> Tuple[List[Dict], int]:
        """Lấy dữ liệu từ callable hoặc trả về dữ liệu tĩnh."""
        if callable(self.data) and asyncio.iscoroutinefunction(self.data):
            if self.core and self.client_state and not await self.core.sqlite_handler.has_permission(self.username, "read_data"):
                logger.error(f"{self.username}: Không có quyền truy cập dữ liệu")
                ui.notify("Không có quyền truy cập dữ liệu", type="negative")
                return [], 0
            try:
                async with asyncio.timeout(60):
                    async def progress_callback(progress: float):
                        if context.client.has_socket_connection:
                            ui.notify(f"Tải dữ liệu: {progress*100:.1f}%", type="info")
                    data, total_count = await self.data(progress_callback=progress_callback, page=page, page_size=page_size)
                    self.pagination["total_count"] = total_count
                    self.pagination["page"] = page  # Cập nhật trang hiện tại
                    return data, total_count
            except asyncio.TimeoutError:
                logger.error(f"{self.username}: Timeout khi tải dữ liệu bảng")
                ui.notify("Timeout khi tải dữ liệu", type="negative")
                return [], 0
        else:
            data = self.data() if callable(self.data) else self.data
            total_count = len(data) if isinstance(data, list) else 0
            self.pagination["total_count"] = total_count
            self.pagination["page"] = page
            return data, total_count
    async def delete_row(self, row_data):
        """Xóa một bản ghi từ qa_data dựa trên ID."""
        try:
            record_id = row_data["id"]
            result = await self.core.delete_record("qa_data", record_id, self.username)
            if "error" in result:
                raise ValueError(result["error"])
            ui.notify(f"Xóa bản ghi {record_id} thành công", type="positive")
            await self.refresh()  # Tự động làm mới bảng sau khi xóa
        except Exception as e:
            error_msg = f"Lỗi xóa bản ghi: {str(e)}"
            if await self.core.sqlite_handler.has_permission(self.username, "admin_access"):
                error_msg += f"\nChi tiết: {traceback.format_exc()}"
            logger.error(f"{self.username}: {error_msg}", exc_info=True)
            ui.notify(error_msg, type="negative")

    
    
    async def render(self):
        """Render bảng, hiển thị thông báo nếu không có dữ liệu."""
        try:
            if self.container:
                self.container.clear()
            self.container = ui.card().classes("w-full p-4")
            with self.container:
                if self.title:
                    ui.label(self.title).classes("text-lg font-bold mb-2")
                if self.loading:
                    ui.linear_progress(show_value=False).classes("w-full")
                data, total_count = await self.fetch_data(page=1, page_size=self.pagination["rowsPerPage"])
                if not data:
                    ui.label("⚠️ Chưa có dữ liệu Q&A").classes("text-gray-500 italic")
                    return
                for row in data:
                    if len(json.dumps(row).encode()) > Config.MAX_UPLOAD_SIZE:
                        logger.error(f"{self.username}: Dữ liệu hàng quá lớn")
                        ui.notify("Dữ liệu hàng quá lớn, vượt quá 1MB!", type="negative")
                        return
                rows = [
                    {
                        **{col["name"]: self.format_callback.get(col["name"], lambda x: x)(row.get(col["field"], ""))
                           for col in self.columns},
                        "id": row.get("id")
                    }
                    for row in data
                ]
                if self.row_actions:
                    for row in rows:
                        row["actions"] = ""
                self.table = ui.table(
                    columns=self.columns + ([{"name": "actions", "label": "Hành động", "field": "actions"}] if self.row_actions else []),
                    rows=rows,
                    row_key="id",
                    pagination=self.pagination
                ).props(f"sortable={self.sortable} filterable={self.filterable}").classes("w-full")
                if self.row_actions:
                    with self.table.add_slot("body-cell-actions"):
                        with ui.element("q-td"):
                            for action in self.row_actions:
                                props = action.get("props", "dense")
                                if isinstance(props, dict):
                                    props = " ".join([f"{k}={v}" if v is not True else k for k, v in props.items()])
                                # Tạo hàm handle_action cho mỗi hành động và mỗi row_id
                                def create_action_handler(row_id, action):
                                    async def handle_action():
                                        try:
                                            row_data = next(d for d in data if d["id"] == row_id)
                                            on_click = self.delete_row if action["label"].lower() == "xóa" else action.get("on_click")
                                            await on_click(row_data)
                                            await self.refresh()
                                        except Exception as e:
                                            error_msg = f"Lỗi {action['label'].lower()}: {str(e)}"
                                            logger.error(f"{self.username}: {error_msg}", exc_info=True)
                                            ui.notify(error_msg, type="negative")
                                    return handle_action
                                # Tạo nút cho mỗi row_id
                                for row in rows:
                                    ui.button(
                                        action["label"],
                                        on_click=create_action_handler(row["id"], action),
                                        icon=action.get("icon")
                                    ).classes(action.get("classes", "bg-blue-600 text-white hover:bg-blue-700")).props(props)
        except Exception as e:
            error_msg = f"Lỗi render bảng: {str(e)}"
            if self.core and self.client_state and await self.core.sqlite_handler.has_permission(self.username, "admin_access"):
                error_msg += f"\nChi tiết: {traceback.format_exc()}"
            logger.error(f"{self.username}: {error_msg}", exc_info=True)
            ui.notify(error_msg, type="negative")
    
    
    async def refresh(self):
        """Làm mới dữ liệu bảng mà không render lại toàn bộ."""
        try:
            self.loading = True
            if self.table:
                data, total_count = await self.fetch_data(page=1, page_size=self.pagination["rowsPerPage"])
                if not data:
                    self.table.clear()
                    with self.table:
                        ui.label("⚠️ Chưa có dữ liệu Q&A").classes("text-gray-500 italic")
                    return
                rows = [
                    {
                        **{col["name"]: self.format_callback.get(col["name"], lambda x: x)(row.get(col["field"], ""))
                           for col in self.columns},
                        "id": row.get("id")
                    }
                    for row in data
                ]
                if self.row_actions:
                    for row in rows:
                        row["actions"] = ""
                self.table.rows = rows
                self.table.update()
                ui.update()
            else:
                await self.render()
        except Exception as e:
            logger.error(f"{self.username}: Lỗi làm mới bảng: {str(e)}", exc_info=True)
            ui.notify(f"Lỗi làm mới bảng: {str(e)}", type="negative")
        finally:
            self.loading = False
            if self.container:
                self.container.update()
