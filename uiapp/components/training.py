import asyncio
import hashlib
import json
import time
import uuid
from Levenshtein import ratio
import csv
from io import StringIO
from typing import Callable, Dict, List, Optional, Tuple
import aiosqlite
from nicegui import ui, context, app
from tenacity import retry, stop_after_attempt, wait_fixed
from config import Config
from core import Core
from uiapp.components.form import FormComponent
from utils.logging import get_logger
from utils.core_common import check_disk_space, sanitize_field_name

logger = get_logger("TrainingComponent")

QA_HISTORY_LIMIT = getattr(Config, "QA_HISTORY_LIMIT", 50)

# Hàm retry cho ui.update
@retry(stop=stop_after_attempt(3), wait=wait_fixed(0.5))
async def safe_ui_update():
    logger.debug("Thử gọi safe_ui_update")
    ui.update()

class TrainingComponent:
    def __init__(self, core: Core, client_state: Dict, classes: str):
        self.core = core
        self.client_state = client_state or {}
        self.classes = classes
        self.container = None
        self.qa_list_container = None
        self.rendered = False
        self.client_id = None
        self.username = client_state.get("username", "")
        self.db_lock = asyncio.Semaphore(1)
        self.state_lock = asyncio.Semaphore(1)
        self.log_lock = asyncio.Semaphore(1)
        self.processing_lock = asyncio.Lock()
        logger.info(f"{self.username}: Khởi tạo TrainingComponent")
        
        # Khởi tạo FormComponent
        self.qa_form = FormComponent(
            fields={
                "id": {"label": "ID", "type": "text", "props": "type=hidden", "value": ""},
                "question": {
                    "label": "Câu hỏi",
                    "type": "text",
                    "validation": {"required": "lambda x: bool(x) or 'Bắt buộc'"},
                    "placeholder": "Nhập câu hỏi..."
                },
                "answer": {
                    "label": "Câu trả lời",
                    "type": "textarea",
                    "validation": {"required": "lambda x: bool(x) or 'Bắt buộc'"},
                    "placeholder": "Nhập câu trả lời..."
                },
                "category": {
                    "label": "Danh mục",
                    "type": "select",
                    "options": ["chat", "support", "other"],
                    "value": "chat",
                    "validation": {"required": "lambda x: bool(x) or 'Bắt buộc'"}
                }
            },
            on_submit=self.handle_qa_submit,
            submit_label="Lưu Q&A",
            core=core,
            client_state=client_state
        )
        self.search_input = None
        self.json_input = None
        asyncio.create_task(self.enable_wal_mode())

    async def render(self):
        async with self.processing_lock:
            if self.rendered and self.client_id == context.client.id:
                logger.info(f"{self.username}: Giao diện đã render, chỉ cập nhật")
                await self.update()
                return

            self.client_id = context.client.id
            if self.container:
                self.container.clear()
                self.container.delete()
            self.container = ui.card().classes(self.classes)
            with self.container:
                ui.label("Quản lý Q&A").classes("text-lg font-semibold mb-4")
                
                self.search_input = ui.input(
                    label="Tìm kiếm Q&A",
                    placeholder="Nhập từ khóa để tìm kiếm trong câu hỏi hoặc câu trả lời..."
                ).classes("mb-4 w-full")
                self.search_input.on("change", self.handle_search)
                
                await self.qa_form.render()
                
                self.qa_list_container = ui.element("div").classes("w-full")
                await self.update_qa_records()
                
                self.json_input = ui.textarea(
                    "Nhập JSON Q&A",
                    placeholder='[{"question": "Câu hỏi", "answer": "Trả lời", "category": "chat"}]'
                ).classes("mb-4 w-full")
                ui.button("Nhập Q&A từ JSON", on_click=self.handle_json_submit).classes("bg-blue-600 text-white hover:bg-blue-700 mb-4 w-full")
                
                ui.upload(on_upload=self.handle_file_upload).props("accept=.json,.csv").classes("mb-4 w-full")
                
                with ui.row().classes("w-full"):
                    ui.button("Xuất Q&A sang JSON", on_click=self.handle_export_qa).classes("bg-green-600 text-white hover:bg-green-700 mr-2 w-full")
                    ui.button("Xóa toàn bộ Q&A", on_click=self.on_reset).classes("bg-red-600 text-white hover:bg-red-700 w-full")

            self.rendered = True
            logger.info(f"{self.username}: Đã render giao diện TrainingComponent")

    
    async def fetch_qa_data(
        self,
        progress_callback: Optional[Callable[[float], None]] = None,
        page: int = 1,
        page_size: int = 10
    ) -> Tuple[List[Dict], int]:
        try:
            async with aiosqlite.connect(Config.SQLITE_DB_PATH) as conn:
                search_value = self.search_input.value.strip() if self.search_input else ""
                logger.debug(f"{self.username}: fetch_qa_data với search_value='{search_value}', page={page}, page_size={page_size}")
                
                if search_value:
                    clean_search = search_value.rstrip('?.!;,').strip()
                    logger.debug(f"{self.username}: Original search '{search_value}', cleaned '{clean_search}'")
                    
                    try:
                        fts_query = f'({clean_search}* OR "{clean_search}" OR {clean_search})'
                        logger.debug(f"{self.username}: FTS query: {fts_query}")
                        
                        query_count = """
                            SELECT COUNT(*) FROM qa_fts 
                            WHERE qa_fts MATCH ? AND rowid IN (
                                SELECT rowid FROM qa_data WHERE created_by = ?
                            )
                        """
                        cursor_count = await conn.execute(query_count, (fts_query, self.username))
                        total_matches = (await cursor_count.fetchone())[0]
                        logger.debug(f"{self.username}: FTS count: {total_matches}")

                        if total_matches == 0:
                            logger.info(f"{self.username}: Không tìm thấy kết quả FTS cho '{search_value}' (cleaned: '{clean_search}')")
                            return [], 0

                        query = """
                            SELECT qa_data.id, qa_data.question, qa_data.answer, qa_data.category, 
                                   qa_data.created_by, qa_data.created_at, qa_data.timestamp
                            FROM qa_fts JOIN qa_data ON qa_fts.rowid = qa_data.rowid
                            WHERE qa_fts MATCH ? AND qa_data.created_by = ?
                            ORDER BY rank, qa_data.timestamp DESC
                            LIMIT ? OFFSET ?
                        """
                        params = [fts_query, self.username, page_size, (page - 1) * page_size]
                        cursor = await conn.execute(query, params)
                        rows = await cursor.fetchall()
                        
                        data = [
                            {
                                "id": row[0],
                                "question": row[1],
                                "answer": row[2],
                                "category": row[3],
                                "created_by": row[4],
                                "created_at": row[5],
                                "timestamp": row[6],
                                "score": 0
                            } for row in rows
                        ]
                        
                        logger.info(f"{self.username}: FTS search '{search_value}' (cleaned '{clean_search}'): {total_matches} matches, trang {page} ({len(data)} items)")
                        
                        if total_matches > 1000:
                            logger.warning(f"{self.username}: Nhiều kết quả FTS ({total_matches}), hiển thị top {len(data)}")
                            if context.client.has_socket_connection:
                                ui.notify(f"Tìm thấy nhiều kết quả ({total_matches}), chỉ hiển thị trang {page}", type="info")
                        
                        if not data:
                            if context.client.has_socket_connection:
                                ui.notify("Không tìm thấy Q&A phù hợp", type="info")
                        
                        return data, total_matches
                    
                    except Exception as fts_error:
                        logger.warning(f"{self.username}: FTS5 error: {str(fts_error)}. Fallback sang fuzzy in-memory (giới hạn 1000 records)")
                        if context.client.has_socket_connection:
                            ui.notify("Sử dụng tìm kiếm cơ bản (FTS chưa sẵn sàng)", type="warning")
                        
                        async with conn.execute(
                            "SELECT id, question, answer, category, created_by, created_at, timestamp "
                            "FROM qa_data WHERE created_by = ? ORDER BY timestamp DESC LIMIT 1000",
                            (self.username,)
                        ) as cursor:
                            all_rows = await cursor.fetchall()
                        
                        if not all_rows:
                            logger.info(f"{self.username}: Không có dữ liệu Q&A")
                            return [], 0
                        
                        threshold = getattr(Config, "TRAINING_SEARCH_THRESHOLD", 0.7)
                        search_lower = search_value.lower()
                        
                        matches = []
                        for row in all_rows:
                            q_lower = row[1].lower() if row[1] else ""
                            a_lower = row[2].lower() if row[2] else ""
                            max_ratio = max(
                                ratio(search_lower, q_lower),
                                ratio(search_lower, a_lower)
                            )
                            if max_ratio >= threshold:
                                matches.append({
                                    "id": row[0],
                                    "question": row[1],
                                    "answer": row[2],
                                    "category": row[3],
                                    "created_by": row[4],
                                    "created_at": row[5],
                                    "timestamp": row[6],
                                    "score": round(max_ratio * 100, 1)
                                })
                        
                        matches.sort(key=lambda x: (x["score"], x["timestamp"]), reverse=True)
                        total_matches = len(matches)
                        start_idx = (page - 1) * page_size
                        end_idx = start_idx + page_size
                        data = matches[start_idx:end_idx]
                        
                        logger.info(f"{self.username}: Fallback fuzzy search '{search_value}' (trong 1000 records): {total_matches} matches, trang {page} ({len(data)} items)")
                        
                        if len(all_rows) == 1000:
                            logger.warning(f"{self.username}: Fallback chỉ search trong 1000 records gần nhất")
                        
                        return data, total_matches
                
                else:
                    query_count = "SELECT COUNT(*) FROM qa_data WHERE created_by = ?"
                    cursor_count = await conn.execute(query_count, (self.username,))
                    total_matches = (await cursor_count.fetchone())[0]
                    logger.debug(f"{self.username}: Non-search count: {total_matches}")

                    query = """
                        SELECT id, question, answer, category, created_by, created_at, timestamp 
                        FROM qa_data WHERE created_by = ?
                        ORDER BY timestamp DESC LIMIT ? OFFSET ?
                    """
                    params = [self.username, page_size, (page - 1) * page_size]
                    cursor = await conn.execute(query, params)
                    rows = await cursor.fetchall()
                    data = [
                        {
                            "id": row[0],
                            "question": row[1],
                            "answer": row[2],
                            "category": row[3],
                            "created_by": row[4],
                            "created_at": row[5],
                            "timestamp": row[6]
                        } for row in rows
                    ]
                    
                    logger.info(f"{self.username}: Tải {len(data)} bản ghi Q&A không search, tổng: {total_matches}")
                    return data, total_matches
                
                if progress_callback:
                    await progress_callback(1.0)
        
        except Exception as e:
            logger.error(f"{self.username}: Lỗi lấy dữ liệu Q&A: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(f"Lỗi lấy dữ liệu Q&A: {str(e)}", type="negative")
            return [], 0

    async def update_qa_records(self):
        try:
            data, total_count = await self.fetch_qa_data(page=1, page_size=QA_HISTORY_LIMIT)
            logger.debug(f"{self.username}: Trước khi cập nhật, số bản ghi Q&A: {len(data)}, tổng: {total_count}")
            
            if not self.container or not hasattr(self.container, 'parent_slot'):
                logger.warning(f"{self.username}: container không tồn tại hoặc không hợp lệ, tạo mới")
                self.container = ui.card().classes(self.classes)
            
            if not self.qa_list_container or not hasattr(self.qa_list_container, 'parent_slot'):
                logger.warning(f"{self.username}: qa_list_container không tồn tại hoặc không hợp lệ, tạo mới")
                self.qa_list_container = ui.element("div").classes("w-full")
                with self.container:
                    self.qa_list_container.move(target=self.container)
                logger.debug(f"{self.username}: Đã tạo và gắn qa_list_container vào container")
            
            self.qa_list_container.clear()
            with self.qa_list_container:
                if not data:
                    ui.label("⚠️ Chưa có dữ liệu Q&A").classes("text-gray-500 italic")
                else:
                    for row in data:
                        with ui.card().classes("w-full mb-2 p-4"):
                            ui.label(f"Câu hỏi: {row['question']}").classes("font-bold")
                            ui.label(f"Trả lời: {row['answer']}")
                            ui.label(f"Danh mục: {row['category']}")
                            ui.label(f"Người tạo: {row['created_by']}")
                            ui.label(f"Thời gian tạo: {row['created_at']}")
                            with ui.row():
                                ui.button("Sửa", on_click=lambda r=row: self.handle_edit(r)).classes("bg-blue-600 text-white hover:bg-blue-700 mr-2")
                                ui.button("Xóa", on_click=lambda r=row: self.delete_row(r)).classes("bg-red-600 text-white hover:bg-red-700")
            
            await safe_ui_update()
            logger.info(f"{self.username}: Loaded {len(data)} Q&A records from DB, tổng: {total_count}")
            logger.debug(f"{self.username}: qa_list_container parent: {getattr(self.qa_list_container, 'parent_slot', 'None')}")
            
            if context.client.has_socket_connection:
                ui.notify(f"Đã tải {len(data)} bản ghi Q&A", type="positive")
        except Exception as e:
            logger.error(f"{self.username}: Lỗi tải Q&A: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(f"Lỗi tải Q&A: {str(e)}", type="negative")

    async def update(self):
        try:
            self.client_id = getattr(context.client, 'id', None)
            logger.debug(f"{self.username}: Bắt đầu update TrainingComponent, client_id={self.client_id}, current context.client.id={context.client.id}")
            
            if not self.rendered:
                logger.warning(f"{self.username}: Giao diện chưa render, thử render lại")
                success = await self.render()
                if not success:
                    logger.error(f"{self.username}: Render lại thất bại")
                    if context.client.has_socket_connection:
                        ui.notify("Lỗi: Giao diện training chưa sẵn sàng", type="negative")
                    return False

            if self.search_input:
                self.search_input.value = ""
                logger.debug(f"{self.username}: Đặt lại search_input để làm mới toàn bộ Q&A")
            else:
                logger.warning(f"{self.username}: search_input không tồn tại, tạo mới nếu cần")
                self.search_input = ui.input(
                    label="Tìm kiếm Q&A",
                    placeholder="Nhập từ khóa để tìm kiếm trong câu hỏi hoặc câu trả lời..."
                ).classes("mb-4 w-full")
                self.search_input.on("change", self.handle_search)

            await self.update_qa_records()
            await safe_ui_update()
            logger.info(f"{self.username}: Cập nhật giao diện training thành công")
            
            if context.client.has_socket_connection:
                ui.notify("Đã cập nhật dữ liệu Q&A", type="positive")
            return True
        except Exception as e:
            logger.error(f"{self.username}: Lỗi cập nhật giao diện: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify("Lỗi cập nhật giao diện training", type="negative")
            return False
            
    async def enable_wal_mode(self):
        try:
            async with aiosqlite.connect(Config.SQLITE_DB_PATH) as conn:
                await conn.execute("PRAGMA journal_mode=WAL")
                await conn.commit()
                logger.info(f"{self.username}: Đã bật chế độ WAL cho SQLite")
        except Exception as e:
            logger.error(f"{self.username}: Lỗi bật WAL: {str(e)}", exc_info=True)

    async def refresh_session_token(self):
        try:
            if context.client.has_socket_connection:
                new_token = await self.core.refresh_session_token(self.username)
                if new_token and isinstance(new_token, str):
                    async with self.state_lock:
                        self.client_state["session_token"] = new_token
                    logger.info(f"{self.username}: Đã làm mới session_token")
                    return True
                else:
                    logger.warning(f"{self.username}: Không thể làm mới session_token")
                    return False
            else:
                logger.warning(f"{self.username}: Không làm mới session_token vì client đã disconnect")
                return False
        except Exception as e:
            logger.error(f"{self.username}: Lỗi làm mới session_token: {str(e)}", exc_info=True)
            return False

    async def cleanup_client_storage(self):
        try:
            if self.container and context.client.has_socket_connection:
                self.container.clear()
                logger.info(f"{self.username}: Đã dọn dẹp container")
            else:
                logger.warning(f"{self.username}: Bỏ qua cleanup vì client đã disconnect hoặc container không tồn tại")
        except Exception as e:
            logger.warning(f"{self.username}: Bỏ qua cleanup vì client đã disconnect: {str(e)}")

    def _handle_result_error(self, result, action: str) -> bool:
        if isinstance(result, str):
            logger.error(f"{self.username}: Lỗi {action}: {result}")
            if context.client.has_socket_connection:
                ui.notify(f"Lỗi {action}: {result}", type="negative")
            return True
        if isinstance(result, dict) and "error" in result:
            logger.error(f"{self.username}: Lỗi {action}: {result['error']}")
            if context.client.has_socket_connection:
                ui.notify(f"Lỗi {action}: {result['error']}", type="negative")
            return True
        return False

    async def save_state_and_config(self) -> bool:
        async with self.state_lock:
            try:
                if not self.client_state.get("session_token"):
                    if not await self.refresh_session_token():
                        if context.client.has_socket_connection:
                            ui.notify("Lỗi: Không thể làm mới session_token", type="negative")
                        return False
                check_disk_space()
                self.client_state["timestamp"] = int(time.time())
                self.client_state.pop("qa_records", None)
                state_json = json.dumps(self.client_state, ensure_ascii=False)
                if len(state_json.encode()) > Config.MAX_UPLOAD_SIZE:
                    logger.error(f"{self.username}: Trạng thái quá lớn")
                    if context.client.has_socket_connection:
                        ui.notify("Lỗi: Trạng thái phiên quá lớn", type="negative")
                    return False
                await self.core.save_client_state(self.client_state["session_token"], self.client_state)
                state_id = hashlib.sha256(
                    f"{self.username}_{self.client_state['session_token']}".encode()
                ).hexdigest()
                async with self.log_lock:
                    await self.core.log_sync_action(
                        table_name="client_states",
                        record_id=state_id,
                        action="SAVE_TRAINING_STATE",
                        details={"username": self.username, "action": "save_training_state"},
                        username=self.username
                    )
                logger.info(f"{self.username}: Lưu trạng thái thành công")
                return True
            except Exception as e:
                logger.error(f"{self.username}: Lỗi lưu trạng thái: {str(e)}", exc_info=True)
                if context.client.has_socket_connection:
                    ui.notify("Lỗi lưu trạng thái", type="negative")
                return False

    
    async def handle_edit(self, row: Dict):
        try:
            if not self.client_state.get("session_token"):
                if not await self.refresh_session_token():
                    if context.client.has_socket_connection:
                        ui.notify("Lỗi: Không thể làm mới session_token", type="negative")
                    return
            session_token = self.client_state.get("session_token", "")
            for field_name in ["id", "question", "answer", "category"]:
                storage_key = f"{session_token}_{field_name}" if session_token else field_name
                app.storage.user[storage_key] = row[field_name]
            if self.qa_form:
                await self.qa_form.set_data(row)
            if context.client.has_socket_connection:
                ui.notify("Đã điền dữ liệu để chỉnh sửa Q&A", type="positive")
        except Exception as e:
            logger.error(f"{self.username}: Lỗi điền dữ liệu chỉnh sửa Q&A: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(f"Lỗi điền dữ liệu chỉnh sửa Q&A: {str(e)}", type="negative")

    async def delete_row(self, row_data):
        async with self.db_lock:
            try:
                record_id = row_data["id"]
                result = await self.core.delete_record("qa_data", record_id, self.username)
                if self._handle_result_error(result, "xóa Q&A"):
                    return
                async with self.log_lock:
                    await self.core.log_sync_action(
                        table_name="qa_data",
                        record_id=record_id,
                        action="DELETE",
                        details={"username": self.username, "action": "delete_record"},
                        username=self.username
                    )
                await self.update_qa_records()
                if context.client.has_socket_connection:
                    ui.notify(f"Đã xóa Q&A {record_id}", type="positive")
            except Exception as e:
                logger.error(f"{self.username}: Lỗi xóa Q&A: {str(e)}", exc_info=True)
                if context.client.has_socket_connection:
                    ui.notify(f"Lỗi xóa Q&A: {str(e)}", type="negative")

    async def _get_search_record_ids(self, search_query: str) -> List[str]:
        """Helper: Lấy record_ids dùng FTS (giống search) hoặc fallback LIKE, với clean query."""
        if not search_query:
            # No search: All records
            async with aiosqlite.connect(Config.SQLITE_DB_PATH) as conn:
                async with conn.execute("SELECT id FROM qa_data WHERE created_by = ?", (self.username,)) as cursor:
                    return [row[0] for row in await cursor.fetchall()]
        
        # Clean query giống fetch_qa_data
        clean_search = search_query.rstrip('?.!;,').strip()
        try:
            # Ưu tiên FTS (giống search)
            fts_query = f'({clean_search}* OR "{clean_search}" OR {clean_search})'
            query = """
                SELECT qa_data.id FROM qa_fts JOIN qa_data ON qa_fts.rowid = qa_data.rowid
                WHERE qa_fts MATCH ? AND qa_data.created_by = ?
            """
            async with aiosqlite.connect(Config.SQLITE_DB_PATH) as conn:
                async with conn.execute(query, (fts_query, self.username)) as cursor:
                    record_ids = [row[0] for row in await cursor.fetchall()]
                    logger.debug(f"{self.username}: FTS delete query '{search_query}' (clean '{clean_search}'): {len(record_ids)} IDs: {record_ids[:5]}...")
                    return record_ids
        except Exception as fts_error:
            logger.warning(f"{self.username}: FTS error in delete: {str(fts_error)}. Fallback LIKE")
            # Fallback LIKE (nhưng clean)
            async with aiosqlite.connect(Config.SQLITE_DB_PATH) as conn:
                async with conn.execute(
                    "SELECT id FROM qa_data WHERE (question LIKE ? OR answer LIKE ?) AND created_by = ?",
                    (f"%{clean_search}%", f"%{clean_search}%", self.username)  # Use clean_search
                ) as cursor:
                    record_ids = [row[0] for row in await cursor.fetchall()]
                    logger.debug(f"{self.username}: LIKE delete fallback '{search_query}': {len(record_ids)} IDs")
                    return record_ids

    async def on_reset(self):
        async with self.db_lock:
            try:
                if not self.client_state.get("session_token"):
                    if not await self.refresh_session_token():
                        if context.client.has_socket_connection:
                            ui.notify("Lỗi: Phiên đăng nhập không hợp lệ", type="negative")
                        logger.error(f"{self.username}: Phiên đăng nhập không hợp lệ")
                        return

                search_query = (getattr(self.search_input, "value", "") or "").strip()
                record_ids = await self._get_search_record_ids(search_query)  # Fix: Dùng FTS/LIKE consistent với search

                if not record_ids:
                    if context.client.has_socket_connection:
                        ui.notify("Không có Q&A nào để xóa", type="info")
                    logger.info(f"{self.username}: Không có Q&A nào để xóa")
                    return

                logger.debug(f"{self.username}: Record IDs to delete: {record_ids}")  # Debug log

                if context.client.has_socket_connection:
                    with ui.dialog() as dialog, ui.card():
                        ui.label(
                            f"Bạn có chắc muốn xóa {len(record_ids)} Q&A"
                            f"{' khớp với tìm kiếm' if search_query else ''}?"
                        )
                        with ui.row():
                            ui.button("OK", on_click=lambda: dialog.submit(True))
                            ui.button("Hủy", on_click=lambda: dialog.submit(False))
                    confirm = await dialog
                    logger.debug(f"{self.username}: Giá trị confirm từ dialog: {confirm}")
                    if not confirm:
                        ui.notify("Hủy xóa", type="info")
                        logger.info(f"{self.username}: Hủy xóa Q&A")
                        return

                current_time = int(time.time())
                async with aiosqlite.connect(Config.SQLITE_DB_PATH) as conn:  # Move inside for batch
                    for record_id in record_ids:
                        await conn.execute(
                            """
                            INSERT INTO sync_log (id, table_name, record_id, action, timestamp, details)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                str(uuid.uuid4()),
                                "qa_data",
                                record_id,
                                "DELETE",
                                current_time,
                                json.dumps(
                                    {"username": self.username, "action": "delete_record"},
                                    ensure_ascii=False
                                )
                            )
                        )

                    # Delete bằng IDs (an toàn hơn, tránh LIKE/FTS error ở delete)
                    placeholders = ','.join('?' for _ in record_ids)
                    await conn.execute(f"DELETE FROM qa_data WHERE id IN ({placeholders}) AND created_by = ?", (*record_ids, self.username))

                    await conn.commit()

                await self.update_qa_records()
                async with self.log_lock:
                    await self.core.log_sync_action(
                        table_name="qa_data",
                        record_id=f"batch_delete_{len(record_ids)}_records",
                        action="DELETE",
                        details={
                            "username": self.username,
                            "action": "delete_all_qa" if not search_query else "delete_search_results",
                            "count": len(record_ids),
                            "search_query": search_query if search_query else None
                        },
                        username=self.username
                    )

                if context.client.has_socket_connection:
                    message = f"Đã xóa {len(record_ids)} Q&A" + (" khớp với tìm kiếm" if search_query else "")
                    ui.notify(message, type="positive")
                logger.info(
                    f"{self.username}: Đã xóa {len(record_ids)} Q&A "
                    f"{'khớp với tìm kiếm' if search_query else 'toàn bộ (của user)'} và ghi log DELETE"
                )
            except Exception as e:
                logger.error(f"{self.username}: Lỗi xóa Q&A: {str(e)}", exc_info=True)
                if context.client.has_socket_connection:
                    ui.notify(f"Lỗi xóa Q&A: {str(e)}", type="negative")

    
    
    async def handle_export_qa(self):
        try:
            all_data = []
            page = 1
            while True:
                result = await self.core.read_records("qa_data", self.username, page=page, page_size=500)
                if self._handle_result_error(result, "xuất Q&A"):
                    return
                data = result.get("results", [])
                if not isinstance(data, list):
                    logger.error(f"{self.username}: Kết quả từ read_records không phải danh sách: {data}")
                    if context.client.has_socket_connection:
                        ui.notify("Lỗi: Dữ liệu Q&A không hợp lệ", type="negative")
                    return
                if not data:
                    break
                all_data.extend(data)
                if len(data) < 500:
                    break
                page += 1
            if not all_data:
                if context.client.has_socket_connection:
                    ui.notify("Không có dữ liệu Q&A để xuất", type="warning")
                return
            json_data = json.dumps(all_data, ensure_ascii=False, indent=2)
            filename = f"qa_data_{int(time.time())}.json"
            if context.client.has_socket_connection:
                ui.download(json_data.encode("utf-8"), filename)
            async with self.log_lock:
                await self.core.log_sync_action(
                    table_name="qa_data",
                    record_id="export",
                    action="EXPORT_QA",
                    details={"username": self.username, "action": "export_qa", "count": len(all_data)},
                    username=self.username
                )
            if context.client.has_socket_connection:
                ui.notify(f"Đã xuất {len(all_data)} Q&A", type="positive")
        except Exception as e:
            logger.error(f"{self.username}: Lỗi xuất Q&A: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(f"Lỗi xuất Q&A: {str(e)}", type="negative")

    async def handle_search(self):
        try:
            query = (getattr(self.search_input, "value", "") or "").strip()
            if not query:
                await self.update_qa_records()
                return
            data, total_count = await self.fetch_qa_data(page=1, page_size=QA_HISTORY_LIMIT)
            self.qa_list_container.clear()
            with self.qa_list_container:
                if not data:
                    ui.label("⚠️ Không tìm thấy Q&A phù hợp").classes("text-gray-500 italic")
                else:
                    for row in data:
                        with ui.card().classes("w-full mb-2 p-4"):
                            ui.label(f"Câu hỏi: {row['question']}").classes("font-bold")
                            ui.label(f"Trả lời: {row['answer']}")
                            ui.label(f"Danh mục: {row['category']}")
                            ui.label(f"Người tạo: {row['created_by']}")
                            ui.label(f"Thời gian tạo: {row['created_at']}")
                            with ui.row():
                                ui.button("Sửa", on_click=lambda r=row: self.handle_edit(r)).classes("bg-blue-600 text-white hover:bg-blue-700 mr-2")
                                ui.button("Xóa", on_click=lambda r=row: self.delete_row(r)).classes("bg-red-600 text-white hover:bg-red-700")
            if context.client.has_socket_connection:
                ui.update()
                ui.notify(f"Tìm thấy {len(data)} kết quả", type="positive")
        except Exception as e:
            logger.error(f"{self.username}: Lỗi tìm kiếm Q&A: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(f"Lỗi tìm kiếm Q&A: {str(e)}", type="negative")

    async def handle_json_submit(self):
        async with self.db_lock:
            try:
                qa_list = json.loads(self.json_input.value)
                if not isinstance(qa_list, list):
                    logger.error(f"{self.username}: JSON không phải danh sách")
                    if context.client.has_socket_connection:
                        ui.notify("JSON phải là danh sách", type="negative")
                    return
                valid_qa_list = await self.process_qa_list(qa_list, "JSON input")
                if not valid_qa_list:
                    logger.error(f"{self.username}: Không có bản ghi Q&A hợp lệ")
                    if context.client.has_socket_connection:
                        ui.notify("Không có bản ghi Q&A hợp lệ", type="negative")
                    return
                async def progress_callback(progress: float):
                    async with self.state_lock:
                        self.client_state["import_progress"] = progress
                with ui.linear_progress(show_value=False).classes("w-full") as progress:
                    result = await self.core.create_records_batch(
                        "qa_data",
                        valid_qa_list,
                        self.username,
                        progress_callback=progress_callback
                    )
                    if context.client.has_socket_connection:
                        progress.delete()
                if self._handle_result_error(result, "nhập Q&A từ JSON"):
                    return
                async with self.log_lock:
                    await self.core.log_sync_action(
                        table_name="qa_data",
                        record_id="batch",
                        action="CREATE_QA_BATCH",
                        details={
                            "username": self.username,
                            "action": "create_qa_batch",
                            "count": len(valid_qa_list)
                        },
                        username=self.username
                    )
                await self.update_qa_records()
                if context.client.has_socket_connection:
                    ui.notify(f"Đã nhập {len(valid_qa_list)} Q&A", type="positive")
            except json.JSONDecodeError as e:
                logger.error(f"{self.username}: JSON không hợp lệ: {str(e)}")
                if context.client.has_socket_connection:
                    ui.notify(f"JSON không hợp lệ: {str(e)}", type="negative")
            except Exception as e:
                logger.error(f"{self.username}: Lỗi nhập JSON Q&A: {str(e)}", exc_info=True)
                if context.client.has_socket_connection:
                    ui.notify(f"Lỗi nhập JSON Q&A: {str(e)}", type="negative")

    async def handle_file_upload(self, e):
        async with self.db_lock:
            try:
                content = e.content.read()
                if len(content) > Config.MAX_UPLOAD_SIZE:
                    if context.client.has_socket_connection:
                        ui.notify("File quá lớn", type="negative")
                    return
                if e.name.endswith('.json'):
                    qa_list = json.loads(content.decode())
                elif e.name.endswith('.csv'):
                    csv_content = StringIO(content.decode())
                    reader = csv.DictReader(csv_content)
                    qa_list = [
                        {
                            "question": row.get("question", "").strip(),
                            "answer": row.get("answer", "").strip(),
                            "category": row.get("category", "chat").strip()
                        }
                        for row in reader if row.get("question") and row.get("answer")
                    ]
                else:
                    if context.client.has_socket_connection:
                        ui.notify("Chỉ chấp nhận JSON hoặc CSV", type="negative")
                    return
                valid_qa_list = await self.process_qa_list(qa_list, f"file {e.name}")
                if not valid_qa_list:
                    if context.client.has_socket_connection:
                        ui.notify("Không có bản ghi Q&A hợp lệ", type="negative")
                    return
                async def progress_callback(progress: float):
                    async with self.state_lock:
                        self.client_state["import_progress"] = progress
                with ui.linear_progress(show_value=False).classes("w-full") as progress:
                    result = await self.core.create_records_batch(
                        "qa_data",
                        valid_qa_list,
                        self.username,
                        progress_callback=progress_callback
                    )
                    if context.client.has_socket_connection:
                        progress.delete()
                if self._handle_result_error(result, "nhập Q&A từ file"):
                    return
                async with self.log_lock:
                    await self.core.log_sync_action(
                        table_name="qa_data",
                        record_id="batch",
                        action="CREATE_QA_BATCH_FILE",
                        details={
                            "username": self.username,
                            "action": "create_qa_batch_file",
                            "count": len(valid_qa_list)
                        },
                        username=self.username
                    )
                await self.update_qa_records()
                if context.client.has_socket_connection:
                    ui.notify(f"Đã nhập {len(valid_qa_list)} Q&A từ file", type="positive")
            except Exception as e:
                logger.error(f"{self.username}: Lỗi nhập Q&A từ file: {str(e)}", exc_info=True)
                if context.client.has_socket_connection:
                    ui.notify(f"Lỗi nhập Q&A từ file: {str(e)}", type="negative")

    async def handle_qa_submit(self, data: Dict):
        async with self.db_lock:
            try:
                data["category"] = sanitize_field_name(data.get("category", "chat"))
                data["created_by"] = self.username
                data["created_at"] = int(time.time())
                data["timestamp"] = int(time.time())

                # Generate ID nếu missing hoặc rỗng
                if "id" not in data or not data["id"]:
                    data["id"] = str(uuid.uuid4())

                if len(json.dumps(data).encode()) > Config.MAX_UPLOAD_SIZE:
                    if context.client.has_socket_connection:
                        ui.notify("Dữ liệu Q&A quá lớn", type="negative")
                    return {"success": False, "error": "Dữ liệu Q&A quá lớn"}

                # Fix: Check duplicate trước insert/update (tránh lặp từ chat hoặc manual)
                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=60.0) as conn:
                    async with conn.execute(
                        'SELECT id FROM "qa_data" WHERE question = ? AND answer = ? AND created_by = ?',
                        (data["question"], data["answer"], self.username)
                    ) as cursor:
                        existing = await cursor.fetchone()
                        if existing:
                            logger.warning(f"{self.username}: Duplicate Q&A detected (existing ID: {existing[0]}), skipping insert")
                            if context.client.has_socket_connection:
                                ui.notify("Q&A này đã tồn tại, bỏ qua để tránh duplicate", type="warning")
                            return {"success": False, "error": "Q&A đã tồn tại"}

                record_id = data["id"]
                is_update = False
                if record_id:
                    # Check tồn tại với filter created_by
                    async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=60.0) as conn:
                        async with conn.execute(
                            'SELECT id FROM "qa_data" WHERE id = ? AND created_by = ?',
                            (record_id, self.username)
                        ) as cursor:
                            if await cursor.fetchone():
                                # Tồn tại → Update
                                result = await self.core.update_record("qa_data", record_id, data, self.username)
                                is_update = True
                                action = "UPDATE_QA"
                            else:
                                logger.warning(f"{self.username}: ID {record_id} không tồn tại hoặc không thuộc user, fallback về create mới")
                                # Fallback: Regenerate ID mới để create (và đã check duplicate ở trên)
                                data["id"] = str(uuid.uuid4())
                                result = await self.core.create_record("qa_data", data, self.username)
                                action = "CREATE_QA"
                else:
                    # ID rỗng → Tạo mới (đã check duplicate)
                    result = await self.core.create_record("qa_data", data, self.username)
                    action = "CREATE_QA"

                if isinstance(result, dict) and "error" in result:
                    self._handle_result_error(result, "lưu Q&A")
                    return {"success": False, "error": result["error"]}

                # Ghi log
                final_id = result.get("id", str(uuid.uuid4())) if isinstance(result, dict) else str(uuid.uuid4())
                async with self.log_lock:
                    await self.core.log_sync_action(
                        table_name="qa_data",
                        record_id=final_id,
                        action=action,
                        details={"username": self.username, "action": action.lower(), "is_update": is_update},
                        username=self.username
                    )
                await self.update_qa_records()
                if context.client.has_socket_connection:
                    ui.notify("Đã lưu Q&A thành công" + (" (cập nhật)" if is_update else " (mới)"), type="positive")
                return {"success": True, "message": "Đã lưu Q&A"}
            except Exception as e:
                logger.error(f"{self.username}: Lỗi lưu Q&A: {str(e)}", exc_info=True)
                if context.client.has_socket_connection:
                    ui.notify(f"Lỗi lưu Q&A: {str(e)}", type="negative")
                return {"success": False, "error": str(e)}

    async def process_qa_list(self, qa_list: List[Dict], source: str) -> List[Dict]:
        valid_qa_list = []
        current_time = int(time.time())
        async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=60.0) as conn:  # Check duplicates batch
            for qa in qa_list:
                if "question" not in qa or "answer" not in qa:
                    logger.error(f"{self.username}: Q&A thiếu question hoặc answer từ {source}")
                    continue
                qa["category"] = sanitize_field_name(qa.get("category", "chat"))
                if "created_by" not in qa:
                    qa["created_by"] = self.username
                # Force convert created_at và timestamp thành int
                try:
                    if "created_at" not in qa or not qa["created_at"]:
                        qa["created_at"] = current_time
                    else:
                        qa["created_at"] = int(float(qa["created_at"]))
                except (ValueError, TypeError):
                    logger.warning(f"{self.username}: Invalid created_at '{qa.get('created_at', 'None')}' từ {source}, dùng default {current_time}")
                    qa["created_at"] = current_time
                
                try:
                    if "timestamp" not in qa or not qa["timestamp"]:
                        qa["timestamp"] = current_time
                    else:
                        qa["timestamp"] = int(float(qa["timestamp"]))
                except (ValueError, TypeError):
                    logger.warning(f"{self.username}: Invalid timestamp '{qa.get('timestamp', 'None')}' từ {source}, dùng default {current_time}")
                    qa["timestamp"] = current_time
                
                if len(json.dumps(qa).encode()) > Config.MAX_UPLOAD_SIZE:
                    logger.error(f"{self.username}: Q&A {qa['question']} vượt quá {Config.MAX_UPLOAD_SIZE} bytes từ {source}")
                    continue
                
                # Generate ID nếu missing hoặc rỗng
                if "id" not in qa or not qa["id"]:
                    qa["id"] = str(uuid.uuid4())
                
                # Fix: Check duplicate (question + answer + created_by)
                try:
                    async with conn.execute(
                        'SELECT id FROM "qa_data" WHERE question = ? AND answer = ? AND created_by = ?',
                        (qa["question"], qa["answer"], self.username)
                    ) as cursor:
                        if await cursor.fetchone():
                            logger.warning(f"{self.username}: Skip duplicate Q&A '{qa['question'][:50]}...' từ {source}")
                            continue
                except Exception as dup_error:
                    logger.warning(f"{self.username}: Error check duplicate for '{qa['question'][:50]}...': {str(dup_error)}, proceed anyway")
                
                valid_qa_list.append(qa)
        return valid_qa_list
