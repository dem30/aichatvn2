import asyncio
import datetime
import time
import uuid
import hashlib
import json
import zipfile
import os
import sqlite3
import math
import base64
import traceback
from io import BytesIO
from nicegui import ui, app, context
from config import Config
from utils.logging import get_logger
from utils.core_common import check_disk_space

logger = get_logger("TabDownload")

def create_tab(core):
    async def render_func(core, username: str, is_admin: bool, client_state: dict):
        client_id = context.client.id
        logger.info(f"{username}: Bắt đầu render Tab Download, client_id={client_id}")  # Giảm từ DEBUG

        if not hasattr(core, "sqlite_handler"):
            logger.error(f"{username}: Core không hợp lệ: Thiếu sqlite_handler")
            ui.notify("Lỗi hệ thống: Core không hợp lệ", type="negative")
            return

        if not client_state or "session_token" not in client_state:
            logger.error(f"{username}: Phiên đăng nhập không hợp lệ")
            ui.notify("Lỗi: Phiên đăng nhập không hợp lệ", type="negative")
            return

        client_storage = app.storage.client.setdefault(client_id, {})
        download_component = client_storage.get("download_component")
        if download_component and download_component.client_id != client_id:
            logger.info(f"{username}: Client ID không khớp, làm mới DownloadComponent")  # Giảm từ DEBUG
            download_component = None
            client_storage.pop("download_component", None)
            client_storage.pop("download_card_container", None)

        if download_component is None or not getattr(download_component, "rendered", False):
            async def on_download():
                client_id = context.client.id
                client_storage = app.storage.client.get(client_id, {})
                download_component = client_storage.get("download_component")
                _username = client_state.get("username", "")
                logger.info(f"{_username}: Bắt đầu tải xuống, client_id={client_id}")  # Giảm từ DEBUG
                if not download_component or not download_component.rendered or not download_component.messages_container:
                    logger.error(f"{_username}: DownloadComponent chưa sẵn sàng")
                    ui.notify("Giao diện download chưa sẵn sàng!", type="negative")
                    return
                await download_component.handle_download()

            async def on_reset():
                client_id = context.client.id
                client_storage = app.storage.client.get(client_id, {})
                download_component = client_storage.get("download_component")
                _username = client_state.get("username", "")
                if download_component and getattr(download_component, "messages_container", None):
                    try:
                        async with download_component.processing_lock:
                            download_component.messages = []
                            client_storage["download_messages"] = download_component.messages
                            await download_component.update_messages()
                            ui.update()
                            logger.info(f"{_username}: Xóa lịch sử download thành công")  # Giữ INFO
                    except Exception as e:
                        logger.error(f"{_username}: Lỗi xóa lịch sử: {str(e)}", exc_info=True)
                        ui.notify("Lỗi xóa lịch sử download", type="negative")

            download_component = DownloadComponent(
                messages=client_storage.get("download_messages", []),
                on_download=on_download,
                on_reset=on_reset,
                core=core,
                client_state=client_state
            )
            client_storage["download_component"] = download_component
            logger.info(f"{username}: Tạo mới DownloadComponent")  # Giảm từ DEBUG

        try:
            card_container = client_storage.get("download_card_container")
            if card_container:
                try:
                    card_container.clear()
                    card_container.delete()
                except Exception as e:
                    logger.warning(f"{username}: Lỗi khi xóa download_card_container cũ: {str(e)}")
            card_container = ui.card().classes("w-full p-2 sm:p-4")
            client_storage["download_card_container"] = card_container
            with card_container:
                success = await download_component.render()
                if not success:
                    raise RuntimeError("Render DownloadComponent thất bại")
            if download_component.rendered:
                await download_component.update_messages()
            ui.update()
            logger.info(f"{username}: Render tab Download thành công")  # Giữ INFO
        except Exception as e:
            error_msg = f"Lỗi render Tab Download: {str(e)}"
            if is_admin:
                error_msg += f"\nChi tiết: {traceback.format_exc()}"
            logger.error(error_msg, exc_info=True)
            ui.notify("Đã xảy ra lỗi khi tải giao diện download.", type="negative")
            if card_container:
                try:
                    card_container.delete()
                except Exception as e:
                    logger.warning(f"{username}: Lỗi khi xóa download_card_container: {str(e)}")

    async def update_func(core, username: str, is_admin: bool, client_state: dict):
        client_id = context.client.id
        logger.info(f"{username}: Cập nhật Tab Download, client_id={client_id}")  # Giảm từ DEBUG
        client_storage = app.storage.client.setdefault(client_id, {})
        download_component = client_storage.get("download_component")
        if download_component is None or not getattr(download_component, "rendered", False) or download_component.client_id != client_id:
            logger.info(f"{username}: DownloadComponent chưa sẵn sàng, gọi lại render_func")
            await render_func(core, username, is_admin, client_state)
            download_component = client_storage.get("download_component")
        if download_component and download_component.rendered:
            async with download_component.processing_lock:
                await download_component.update_messages()
                ui.update()
            logger.info(f"{username}: Cập nhật tab Download thành công")  # Giữ INFO

    return render_func, update_func

class DownloadComponent:
    def __init__(self, messages, on_download, on_reset, core, client_state):
        self.messages = messages[:getattr(Config, "CHAT_HISTORY_LIMIT", 50)] if messages else []
        self.on_download = on_download
        self.on_reset = on_reset
        self.core = core
        self.client_state = client_state or {}
        self.messages_container = None
        self.loading = False
        self.container = None
        self.rendered = False
        self.client_id = None
        self.processing_lock = asyncio.Lock()

    async def render(self):
        username = self.client_state.get("username", "")
        self.client_id = getattr(context.client, 'id', None)
        try:
            async with asyncio.timeout(15):
                check_disk_space()
                await asyncio.sleep(0.05)
                self.container = ui.element("div").classes("w-full")
                with self.container:
                    ui.label("Tải xuống dự án").classes("text-base font-semibold mb-2")
                    self.messages_container = ui.scroll_area().classes("flex-1 mb-2 h-[40vh] sm:h-[50vh]")
                    with ui.element("div").classes("w-full flex flex-col sm:flex-row gap-2"):
                        ui.button("Tải xuống ZIP", on_click=self.handle_download, icon="download").classes("bg-blue-600 text-white w-full sm:w-auto").bind_enabled_from(self, "loading", backward=lambda x: not x)
                        ui.button("Xóa lịch sử", on_click=self.reset, icon="delete").classes("bg-red-600 text-white w-full sm:w-auto")
                self.rendered = True
                client_storage = app.storage.client.setdefault(self.client_id, {})
                client_storage["download_component"] = self
                client_storage["download_messages"] = self.messages
                logger.info(f"{username}: Rendered DownloadComponent")  # Giảm từ DEBUG
                return True
        except Exception as e:
            logger.error(f"{username}: Lỗi render DownloadComponent: {str(e)}", exc_info=True)
            ui.notify(f"Lỗi tải giao diện Download: {str(e)}", type="negative")
            return False

    async def handle_download(self):
        username = self.client_state.get("username", "")
        async with self.processing_lock:
            if not self.rendered or not self.messages_container:
                logger.error(f"{username}: Giao diện chưa sẵn sàng")
                ui.notify("Giao diện Download chưa sẵn sàng!", type="negative")
                return
            self.loading = True
            with self.container:
                self.progress = ui.linear_progress().classes("w-full")
            try:
                result = await self.export_project()
                if "error" in result:
                    logger.error(f"{username}: {result['error']}")
                    self.messages.append({
                        "id": f"error_{time.time_ns()}",
                        "content": result["error"],
                        "role": "system",
                        "type": "text",
                        "timestamp": int(time.time())
                    })
                    ui.notify(result["error"], type="negative")
                    await self.update_messages()
                    return
                ui.download(result["zip_data"], "project_data.zip")
                self.messages.append({
                    "id": hashlib.sha256(f"download_{time.time_ns()}".encode()).hexdigest(),
                    "content": "Tải xuống thành công! Đã cung cấp file project_data.zip",
                    "role": "system",
                    "type": "text",
                    "timestamp": int(time.time())
                })
                ui.notify("Đã tải file ZIP!", type="positive")
                await self.update_messages()
            except Exception as e:
                logger.error(f"{username}: Lỗi tải xuống: {str(e)}", exc_info=True)
                self.messages.append({
                    "id": f"error_{time.time_ns()}",
                    "content": f"Lỗi xử lý tải xuống: {str(e)}",
                    "role": "system",
                    "type": "text",
                    "timestamp": int(time.time())
                })
                ui.notify(f"Lỗi xử lý tải xuống: {str(e)}", type="negative")
                await self.update_messages()
            finally:
                self.loading = False
                with self.container:
                    if hasattr(self, 'progress') and self.progress is not None and hasattr(self.progress, 'parent_slot') and self.progress in self.progress.parent_slot.children:
                        self.progress.delete()
                        self.progress = None
                ui.update()

    async def export_project(self):
        username = self.client_state.get("username", "")
        try:
            check_disk_space()
            json_data = await self.export_sqlite_to_json()
            if "error" in json_data:
                logger.error(f"{username}: Cannot create zip: {json_data['error']}")
                return {"error": json_data["error"]}

            exclude_patterns = [
                ".env", ".git/", ".gitignore", "__pycache__/", ".pyc", ".log",
                "node_modules/", "venv/", ".DS_Store", "app.db"
            ]

            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            project_files = {}
            for root, dirs, files in os.walk(project_root):
                dirs[:] = [d for d in dirs if not any(ex in d for ex in exclude_patterns)]
                for file in files:
                    if any(ex in file for ex in exclude_patterns):
                        continue
                    file_path = os.path.join(root, file)
                    if os.path.getsize(file_path) > 1_000_000_000:
                        logger.warning(f"{username}: Skipped large file: {file_path}")
                        continue
                    with open(file_path, "rb") as f:
                        project_files[os.path.relpath(file_path, project_root)] = f.read()
                    logger.info(f"{username}: Added project file to zip")  # Giảm từ DEBUG

            zip_buffer = BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zip_file:
                if isinstance(json_data, str):
                    zip_file.writestr("data/sqlite_export.json", json_data.encode("utf-8"))
                    logger.info(f"{username}: Added SQLite JSON")  # Giảm từ DEBUG
                else:
                    logger.warning(f"{username}: Skipped JSON due to error")

                sqlite_db_path = Config.SQLITE_DB_PATH
                if os.path.exists(sqlite_db_path):
                    if os.path.getsize(sqlite_db_path) > 1_000_000_000:
                        logger.warning(f"{username}: Skipped large SQLite database: {sqlite_db_path}")
                    else:
                        zip_file.write(sqlite_db_path, "data/app.db")
                        logger.info(f"{username}: Added SQLite database")  # Giảm từ DEBUG

                for rel_path, content in project_files.items():
                    zip_file.writestr(rel_path, content)
                    logger.info(f"{username}: Added project file to zip")  # Giảm từ DEBUG

            zip_data = zip_buffer.getvalue()
            zip_buffer.close()
            logger.info(f"{username}: Exported project to zip, size: {len(zip_data)} bytes")
            return {"zip_data": zip_data}
        except Exception as e:
            logger.error(f"{username}: Error exporting project to zip: {str(e)}", exc_info=True)
            return {"error": f"Lỗi xuất dự án: {str(e)}"}

    def serialize_value(self, value):
        if isinstance(value, (datetime.datetime, datetime.date)):
            return value.isoformat()
        elif isinstance(value, bytes):
            return base64.b64encode(value).decode('utf-8')
        elif isinstance(value, float):
            if math.isnan(value) or math.isinf(value):
                return None
        return value

    async def export_sqlite_to_json(self):
        username = self.client_state.get("username", "")
        try:
            sqlite_db_path = Config.SQLITE_DB_PATH
            with sqlite3.connect(sqlite_db_path, timeout=20) as conn:
                c = conn.cursor()
                c.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [row[0] for row in c.fetchall() if not row[0].endswith('_fts') and not row[0].startswith('sqlite_')]
                if not tables:
                    logger.warning(f"{username}: No tables found in SQLite database")
                    return '{"tables": [], "data": {}}'

                data = {}
                for table in tables:
                    try:
                        c.execute(f"PRAGMA table_info({table})")
                        columns = [col[1] for col in c.fetchall()]
                        if table == 'qa_data':
                            c.execute(f"SELECT * FROM {table} ORDER BY timestamp DESC LIMIT 1000")
                        else:
                            c.execute(f"SELECT * FROM {table}")
                        rows = c.fetchall()
                        table_data = []
                        for row in rows:
                            row_dict = {columns[i]: self.serialize_value(value) for i, value in enumerate(row)}
                            table_data.append(row_dict)
                        data[table] = table_data
                        logger.info(f"{username}: Exported table {table} with {len(rows)} rows")  # Giảm từ DEBUG
                    except Exception as e:
                        logger.warning(f"{username}: Failed to export table {table}: {str(e)}")
                        data[table] = []

                try:
                    json_str = json.dumps({"tables": tables, "data": data}, indent=2, ensure_ascii=False)
                    logger.info(f"{username}: JSON dump success, size: {len(json_str)} chars")
                    conn.commit()
                    return json_str
                except Exception as json_e:
                    logger.error(f"{username}: JSON dump failed: {str(json_e)}", exc_info=True)
                    return {"error": f"Lỗi JSON: {str(json_e)}"}
        except Exception as e:
            logger.error(f"{username}: Error exporting SQLite to JSON: {str(e)}", exc_info=True)
            return {"error": f"Lỗi xuất SQLite sang JSON: {str(e)}"}

    async def reset(self):
        await self.on_reset()

    async def scroll_to_bottom(self):
        username = self.client_state.get("username", "")
        try:
            if self.messages_container:
                await asyncio.sleep(0.05)
                self.messages_container.scroll_to(percent=1.0)
                # Bỏ log scroll
        except Exception as e:
            logger.warning(f"{username}: Lỗi cuộn: {e}")

    async def update_messages(self):
        username = self.client_state.get("username", "")
        if not self.rendered or not self.messages_container:
            logger.warning(f"{username}: Giao diện chưa sẵn sàng")
            return False
        try:
            async with asyncio.timeout(1):
                self.messages_container.clear()
                with self.messages_container:
                    if not self.messages:
                        ui.label("Chưa có hoạt động tải xuống").classes("text-gray-500 text-center py-2")
                    for msg in self.messages:
                        if len(msg["content"].encode()) > 500_000:
                            logger.warning(f"{username}: Tin nhắn quá lớn: {msg['content'][:100]}...")
                            continue
                        role = "Hệ thống"
                        classes = "bg-gray-100 mr-4 ml-2 self-start"
                        with ui.element("div").classes(f"p-1 sm:p-2 mb-1 rounded {classes} max-w-[80%]"):
                            ui.markdown(f"**{role}**: {msg['content']}").classes("text-sm")
                            ui.label(f"({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(msg.get('timestamp', 0)))})").classes("text-xs text-gray-500")
                ui.update()
                if self.messages:  # Chỉ log khi có tin nhắn
                    logger.info(f"{username}: Cập nhật tin nhắn, số tin nhắn: {len(self.messages)}")
                return True
        except Exception as e:
            logger.error(f"{username}: Lỗi cập nhật tin nhắn: {str(e)}", exc_info=True)
            ui.notify("Lỗi cập nhật tin nhắn", type="negative")
            return False

@app.on_disconnect
async def handle_disconnect():
    client_id = context.client.id
    logger.info(f"Client disconnected, client_id={client_id}")  # Giảm từ DEBUG
    if client_id in app.storage.client:
        app.storage.client.pop(client_id, None)
