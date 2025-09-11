from nicegui import ui, context, app
from typing import List, Dict, Callable, Optional
from utils.logging import get_logger
from utils.core_common import validate_name, check_disk_space
import asyncio
import time
import aiosqlite
import json
import hashlib
from config import Config
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from Levenshtein import ratio
from jsonschema import validate, ValidationError as JSONSchemaValidationError
import uuid
    # Thêm ở đầu file (optional cho unidecode)
try:
    from unidecode import unidecode
    HAS_UNIDECODE = True
except ImportError:
    HAS_UNIDECODE = False
    def unidecode(text): 
        return text  # Fallback identity



logger = get_logger("ChatComponent")


class ChatComponent:
    def __init__(
        self,
        messages: List[Dict],
        on_send: Callable,
        on_reset: Callable,
        on_model_change: Callable,
        classes: str = "w-full p-2 sm:p-4",
        send_button_label: str = "Gửi",
        message_limit: int = 50,
        placeholder: str = "Nhập tin nhắn của bạn...",
        core: Optional["Core"] = None,
        client_state: Optional[Dict] = None,
        groq_client: Optional[object] = None
    ):
        self.messages = messages[:message_limit] if messages else []
        self.on_send = on_send
        self.on_reset = on_reset
        self.on_model_change = on_model_change
        self.classes = classes
        self.send_button_label = send_button_label
        self.message_limit = message_limit
        self.placeholder = placeholder
        self.message_input = None
        self.messages_container = None
        self.loading = False
        self.core = core
        self.client_state = client_state or {}
        self.groq_client = groq_client
        self.container = None
        self.rendered = False
        self.client_id = None
        self.processing_lock = asyncio.Lock()
        self.last_message_id = None
        self.progress = None
        self.qa_threshold = getattr(Config, "QA_SEARCH_THRESHOLD", 0.8)

        # Sử dụng giá trị mặc định từ Config nếu không có trong client_state
        if "model" not in self.client_state:
            self.client_state["model"] = Config.DEFAULT_MODEL
        if "chat_mode" not in self.client_state:
            self.client_state["chat_mode"] = Config.DEFAULT_CHAT_MODE

        logger.info(f"{self.client_state.get('username', '')}: Khởi tạo: model={self.client_state['model']}, chat_mode={self.client_state['chat_mode']}")

    async def render(self):
        username = self.client_state.get("username", "")
        self.client_id = getattr(context.client, 'id', None)
        if not self.client_id or not self.client_state or "session_token" not in self.client_state or not validate_name(self.client_state["session_token"]):
            logger.error(f"{username}: Phiên hoặc client_id không hợp lệ")
            ui.notify("Lỗi: Không thể tải giao diện chat", type="negative")
            return False
        try:
            async with asyncio.timeout(30):
                check_disk_space()
                client_storage = app.storage.client.setdefault(self.client_id, {})
                new_container = ui.element("div").classes(self.classes)
                with new_container:
                    ui.label("Chat AI").classes("text-lg font-semibold mb-2")
                    self.messages_container = ui.scroll_area().classes("flex-1 mb-2 h-[50vh] sm:h-[60vh]")

                    # Chỉ hiển thị compobox nếu được bật trong Config
                    if Config.SHOW_MODEL_COMBOBOX:
                        model_select = ui.select(
                            Config.AVAILABLE_MODELS,
                            label="Chọn mô hình AI",
                            value=self.client_state.get("model", Config.DEFAULT_MODEL)
                        ).classes("w-full sm:w-1/4 mb-2").on("update:modelValue", lambda e: self.on_model_change(e.args))
                    else:
                        self.client_state["model"] = Config.DEFAULT_MODEL

                    if Config.SHOW_MODE_COMBOBOX:
                        mode_options = ["Grok", "QA", "Hybrid"]
                        mode_select = ui.select(
                            mode_options,
                            label="Chế độ Chat",
                            value=self.client_state.get("chat_mode", Config.DEFAULT_CHAT_MODE)
                        ).classes("w-full sm:w-1/4 mb-2").on("update:modelValue", lambda e: self.handle_mode_change(e.args, mode_options))
                    else:
                        self.client_state["chat_mode"] = Config.DEFAULT_CHAT_MODE

                    with ui.element("div").classes("w-full flex flex-col sm:flex-row gap-2"):
                        self.message_input = ui.textarea(
                            label="Tin nhắn",
                            placeholder=self.placeholder
                        ).props("clearable").classes("flex-1").bind_enabled_from(self, "loading", backward=lambda x: not x).on(
                            "keydown.enter", lambda e: self.handle_send() if not e.args.get("repeat") else None
                        )
                        ui.button(self.send_button_label, on_click=self.handle_send, icon="send").classes("bg-blue-600 text-white w-full sm:w-auto").bind_enabled_from(self, "loading", backward=lambda x: not x)
                        ui.button("Xóa lịch sử", on_click=self.reset, icon="delete").classes("bg-red-600 text-white w-full sm:w-auto")
                if self.container:
                    if hasattr(self.container, 'parent_slot') and self.container in self.container.parent_slot.children:
                        self.container.clear()
                        self.container.delete()
                    else:
                        logger.debug(f"{username}: Bỏ qua xóa container cũ vì không tồn tại trong danh sách children")
                self.container = new_container
                client_storage["chat_card_container"] = new_container
                self.rendered = True
                await self.load_messages_from_db(username)
                await self.update_messages()
                await self.scroll_to_bottom()
                if not self.messages_container or not self.message_input:
                    raise RuntimeError("Giao diện không đầy đủ sau render")
                logger.info(f"{username}: Render giao diện chat thành công")
                return True
        except Exception as e:
            logger.error(f"{username}: Lỗi render: {str(e)}", exc_info=True)
            ui.notify("Lỗi tải giao diện chat, vui lòng thử lại!", type="negative")
            if new_container:
                try:
                    new_container.delete()
                except Exception as e:
                    logger.warning(f"{username}: Lỗi khi xóa container mới: {str(e)}")
            self.rendered = False
            return False

    async def load_messages_from_db(self, username: str):
        try:
            async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=60.0) as conn:
                async with conn.execute(
                    "SELECT id, content, role, type, timestamp FROM chat_messages WHERE session_token = ? AND username = ? ORDER BY timestamp ASC LIMIT ?",
                    (self.client_state["session_token"], username, self.message_limit)
                ) as cursor:
                    self.messages = [{"id": row[0], "content": row[1], "role": row[2], "type": row[3], "timestamp": row[4]} for row in await cursor.fetchall()]
                    logger.info(f"{username}: Đã tải {len(self.messages)} tin nhắn từ chat_messages")
        except Exception as e:
            logger.error(f"{username}: Lỗi tải tin nhắn từ DB: {str(e)}", exc_info=True)
            self.messages = []

    async def handle_send(self):
        username = self.client_state.get("username", "")
        chat_mode = self.client_state.get("chat_mode", Config.DEFAULT_CHAT_MODE)
        logger.debug(f"{username}: Gửi tin nhắn, chế độ={chat_mode}")
        async with self.processing_lock:
            if not self.rendered or not self.message_input or not self.messages_container or not self.client_id or context.client.id != self.client_id:
                logger.warning(f"{username}: Giao diện chưa sẵn sàng hoặc client_id không khớp")
                ui.notify("Giao diện chat chưa sẵn sàng, vui lòng thử lại!", type="negative")
                return
            if chat_mode not in ["QA", "Grok", "Hybrid"]:
                logger.error(f"{username}: Chế độ không hợp lệ: {chat_mode}")
                ui.notify("Lỗi: Chế độ chat không hợp lệ", type="negative")
                return
            message = self.message_input.value.strip()
            if not message:
                ui.notify("Vui lòng nhập tin nhắn!", type="negative")
                return
            if len(message.encode()) > 1_000_000:
                ui.notify("Lỗi: Tin nhắn quá dài", type="negative")
                return
            current_time = int(time.time())
            current_message_id = hashlib.sha256(f"{message}_{current_time}".encode()).hexdigest()
            if self.last_message_id == current_message_id or any(msg["content"] == message and abs(msg["timestamp"] - current_time) < 2 for msg in self.messages):
                logger.debug(f"{username}: Tin nhắn trùng lặp, bỏ qua")
                return
            self.last_message_id = current_message_id
            self.loading = True
            with self.container:
                self.progress = ui.linear_progress().classes("w-full")
            try:
                user_message = {
                    "id": hashlib.sha256(f"{message}_{time.time_ns()}_{uuid.uuid4()}".encode()).hexdigest(),
                    "content": message,
                    "role": "user",
                    "type": "text",
                    "timestamp": current_time
                }
                self.messages.append(user_message)
                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=60.0) as conn:
                    await conn.execute(
                        "INSERT OR REPLACE INTO chat_messages (id, session_token, username, content, role, type, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (user_message["id"], self.client_state["session_token"], username, message, "user", "text", current_time)
                    )
                    await conn.commit()
                await self.update_messages()
                await self.scroll_to_bottom()
                ui.notify(f"Xử lý trong chế độ {chat_mode}", type="info")
                ui.update()

                if chat_mode == "QA":
                    matches = await self.fuzzy_match_question("qa_data", message, username, limit=1, threshold=self.qa_threshold)
                    if matches:
                        response = matches[0]["answer"]
                        ai_message = {
                            "id": hashlib.sha256(f"{response}_{time.time_ns()}".encode()).hexdigest(),
                            "content": response,
                            "role": "assistant",
                            "type": "text",
                            "timestamp": int(time.time())
                        }
                        self.messages.append(ai_message)
                        async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=60.0) as conn:
                            await conn.execute(
                                "INSERT OR REPLACE INTO chat_messages (id, session_token, username, content, role, type, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (ai_message["id"], self.client_state["session_token"], username, response, "assistant", "text", ai_message["timestamp"])
                            )
                            await conn.commit()
                        logger.info(f"{username}: Tìm thấy câu trả lời QA: {response[:50]}...")
                    else:
                        ai_message = {
                            "id": f"error_{time.time_ns()}",
                            "content": "Không tìm thấy câu trả lời trong dữ liệu QA.",
                            "role": "assistant",
                            "type": "text",
                            "timestamp": int(time.time())
                        }
                        self.messages.append(ai_message)
                        ui.notify("Không tìm thấy câu trả lời phù hợp", type="negative")
                elif chat_mode == "Hybrid":
                    matches = await self.fuzzy_match_question("qa_data", message, username, limit=1, threshold=self.qa_threshold)
                    if matches:
                        qa_answer = matches[0]["answer"]
                        hybrid_prompt = f"Câu hỏi: {message}\nCâu trả lời gợi ý: {qa_answer}\nDiễn đạt lại câu trả lời cho tự nhiên, ngắn gọn, và giữ nguyên ý nghĩa chính."
                        if len(hybrid_prompt.encode()) > 1_000_000:
                            logger.warning(f"{username}: Prompt Hybrid quá dài, fallback về QA")
                            response = qa_answer
                        else:
                            result = await self.call_grok_api(hybrid_prompt, "chat", username)
                            if "error" in result:
                                logger.info(f"{username}: Fallback về QA vì Grok lỗi: {result['error']}")
                                response = qa_answer
                            else:
                                response = result["response"]
                                if response.lower() not in ["đúng", "sai", "true", "false"]:
                                    await self.core.create_record(
                                        "qa_data",
                                        {
                                            "id": hashlib.sha256(f"{message}_{time.time_ns()}".encode()).hexdigest(),
                                            "question": message,
                                            "answer": response,
                                            "category": "chat",
                                            "created_by": username,
                                            "created_at": int(time.time()),
                                            "timestamp": int(time.time())
                                        },
                                        username
                                    )
                    else:
                        result = await self.call_grok_api(message, "chat", username)
                        if "error" in result:
                            response = "❌ Không tìm thấy câu trả lời trong QA và không gọi được Grok."
                            logger.info(f"{username}: Grok lỗi và không có QA match: {result['error']}")
                        else:
                            response = result["response"]
                            if response.lower() not in ["đúng", "sai", "true", "false"]:
                                await self.core.create_record(
                                    "qa_data",
                                    {
                                        "id": hashlib.sha256(f"{message}_{time.time_ns()}".encode()).hexdigest(),
                                        "question": message,
                                        "answer": response,
                                        "category": "chat",
                                        "created_by": username,
                                        "created_at": int(time.time()),
                                        "timestamp": int(time.time())
                                    },
                                    username
                                )
                    ai_message = {
                        "id": hashlib.sha256(f"{response}_{time.time_ns()}".encode()).hexdigest(),
                        "content": response,
                        "role": "assistant",
                        "type": "text",
                        "timestamp": int(time.time())
                    }
                    self.messages.append(ai_message)
                    async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=60.0) as conn:
                        await conn.execute(
                            "INSERT OR REPLACE INTO chat_messages (id, session_token, username, content, role, type, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (ai_message["id"], self.client_state["session_token"], username, response, "assistant", "text", ai_message["timestamp"])
                        )
                        await conn.commit()
                    logger.info(f"{username}: Xử lý Hybrid thành công, phản hồi: {response[:50]}...")
                else:  # Grok
                    result = await self.call_grok_api(message, "chat", username)
                    if "error" in result:
                        ai_message = {
                            "id": f"error_{time.time_ns()}",
                            "content": f"❌ {result['error']}",
                            "role": "assistant",
                            "type": "text",
                            "timestamp": int(time.time())
                        }
                    else:
                        response = result["response"]
                        if response.lower() in ["đúng", "sai", "true", "false"]:
                            logger.warning(f"{username}: Phản hồi Grok ngắn: {response}")
                        ai_message = {
                            "id": hashlib.sha256(f"{response}_{time.time_ns()}".encode()).hexdigest(),
                            "content": response,
                            "role": "assistant",
                            "type": "text",
                            "timestamp": int(time.time())
                        }
                        if response.lower() not in ["đúng", "sai", "true", "false"]:
                            await self.core.create_record(
                                "qa_data",
                                {
                                    "id": hashlib.sha256(f"{message}_{time.time_ns()}".encode()).hexdigest(),
                                    "question": message,
                                    "answer": response,
                                    "category": "chat",
                                    "created_by": username,
                                    "created_at": int(time.time()),
                                    "timestamp": int(time.time())
                                },
                                username
                            )
                    self.messages.append(ai_message)
                    async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=60.0) as conn:
                        await conn.execute(
                            "INSERT OR REPLACE INTO chat_messages (id, session_token, username, content, role, type, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (ai_message["id"], self.client_state["session_token"], username, ai_message["content"], "assistant", "text", ai_message["timestamp"])
                        )
                        await conn.commit()
                self.messages = self.messages[-self.message_limit:]
                self.message_input.value = ""
                await self.update_messages()
                await self.scroll_to_bottom()
                logger.info(f"{username}: Gửi tin nhắn thành công")
            except Exception as e:
                logger.error(f"{username}: Lỗi gửi tin nhắn: {str(e)}", exc_info=True)
                self.messages.append({
                    "id": f"error_{time.time_ns()}",
                    "content": "Lỗi xử lý tin nhắn",
                    "role": "assistant",
                    "type": "text",
                    "timestamp": int(time.time())
                })
                ui.notify("Lỗi gửi tin nhắn", type="negative")
                await self.update_messages()
                await self.scroll_to_bottom()
            finally:
                self.loading = False
                with self.container:
                    if hasattr(self, 'progress') and self.progress is not None and hasattr(self.progress, 'parent_slot') and self.progress in self.progress.parent_slot.children:
                        self.progress.delete()
                        self.progress = None
                    else:
                        logger.debug(f"{username}: Bỏ qua xóa progress vì không tồn tại trong danh sách children")
                ui.update()
                await self.scroll_to_bottom()

    async def handle_mode_change(self, mode_select, mode_options: List[str]):
        username = self.client_state.get("username", "")
        if self.loading:
            logger.warning(f"{username}: Không thể đổi chế độ khi đang xử lý")
            ui.notify("Vui lòng chờ xử lý tin nhắn", type="negative")
            return
        try:
            async with asyncio.timeout(10):
                if isinstance(mode_select, dict) and "value" in mode_select:
                    new_mode = mode_options[mode_select["value"]] if isinstance(mode_select["value"], int) else mode_select["value"]
                else:
                    new_mode = mode_select
                if new_mode not in ["QA", "Grok", "Hybrid"]:
                    logger.error(f"{username}: Chế độ không hợp lệ: {new_mode}")
                    ui.notify("Lỗi: Chế độ không hợp lệ", type="negative")
                    return
                self.client_state["chat_mode"] = new_mode
                await self.save_state_and_config(username)
                logger.info(f"{username}: Đổi chế độ thành {new_mode}")
                ui.notify(f"Chế độ: {new_mode}", type="positive")
                ui.update()
        except Exception as e:
            logger.error(f"{username}: Lỗi đổi chế độ: {str(e)}", exc_info=True)
            ui.notify("Lỗi đổi chế độ", type="negative")
            self.client_state["chat_mode"] = Config.DEFAULT_CHAT_MODE
            ui.update()

    async def scroll_to_bottom(self):
        username = self.client_state.get("username", "")
        try:
            if self.messages_container:
                await asyncio.sleep(0.1)
                self.messages_container.scroll_to(percent=1.0)
                logger.debug(f"{username}: Cuộn xuống cuối thành công")
        except Exception as e:
            logger.warning(f"{username}: Lỗi cuộn: {e}")

    async def save_state_and_config(self, username: str) -> bool:
        try:
            async with asyncio.timeout(10):
                check_disk_space()
                self.client_state["timestamp"] = int(time.time())
                if not Config.SHOW_MODEL_COMBOBOX:
                    self.client_state["model"] = Config.DEFAULT_MODEL
                if not Config.SHOW_MODE_COMBOBOX:
                    self.client_state["chat_mode"] = Config.DEFAULT_CHAT_MODE
                self.client_state.pop("chat_messages", None)
                state_json = json.dumps(self.client_state)
                if len(state_json.encode()) > 1_000_000:
                    logger.error(f"{username}: Trạng thái quá lớn")
                    ui.notify("Lỗi: Trạng thái phiên quá lớn", type="negative")
                    return False
                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=60.0) as conn:
                    state_id = hashlib.sha256(f"{username}_{self.client_state['session_token']}".encode()).hexdigest()
                    await conn.execute(
                        "INSERT OR REPLACE INTO client_states (id, username, session_token, state, timestamp) VALUES (?, ?, ?, ?, ?)",
                        (state_id, username, self.client_state["session_token"], state_json, self.client_state["timestamp"])
                    )
                    config_id = hashlib.sha256(f"{username}_{self.client_state['session_token']}_config".encode()).hexdigest()
                    await conn.execute(
                        "INSERT OR REPLACE INTO chat_config (id, username, model, chat_mode, timestamp) VALUES (?, ?, ?, ?, ?)",
                        (config_id, username, self.client_state["model"], self.client_state["chat_mode"], self.client_state["timestamp"])
                    )
                    await conn.commit()
                logger.info(f"{username}: Lưu cấu hình thành công, model={self.client_state['model']}, chat_mode={self.client_state['chat_mode']}")
                return True
        except Exception as e:
            logger.error(f"{username}: Lỗi lưu cấu hình: {str(e)}", exc_info=True)
            ui.notify("Lỗi lưu cấu hình", type="negative")
            return False

    async def load_state_and_render(self, container=None):
        username = self.client_state.get("username", "")
        try:
            async with asyncio.timeout(10):
                check_disk_space()
                if "session_token" not in self.client_state or not username or not validate_name(self.client_state["session_token"]):
                    logger.warning(f"{username}: Phiên đăng nhập không hợp lệ")
                    ui.notify("Lỗi: Phiên đăng nhập không hợp lệ", type="negative")
                    return False
                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=60.0) as conn:
                    async with conn.execute(
                        "SELECT state FROM client_states WHERE session_token = ? AND username = ?",
                        (self.client_state["session_token"], username)
                    ) as cursor:
                        row = await cursor.fetchone()
                        if not row:
                            self.client_state["model"] = Config.DEFAULT_MODEL
                            self.client_state["chat_mode"] = Config.DEFAULT_CHAT_MODE
                            self.client_state["timestamp"] = int(time.time())
                            state_id = hashlib.sha256(f"{username}_{self.client_state['session_token']}".encode()).hexdigest()
                            await conn.execute(
                                "INSERT INTO client_states (id, username, session_token, state, timestamp) VALUES (?, ?, ?, ?, ?)",
                                (state_id, username, self.client_state["session_token"], json.dumps(self.client_state), self.client_state["timestamp"])
                            )
                            await conn.commit()
                        else:
                            self.client_state.update(json.loads(row[0]))
                            self.client_state.pop("chat_messages", None)
                await self.load_messages_from_db(username)
                await self.save_state_and_config(username)
                success = await self.render()
                if not success:
                    ui.notify("Lỗi tải giao diện chat", type="negative")
                    return False
                logger.info(f"{username}: Tải trạng thái thành công, chat_mode={self.client_state.get('chat_mode', Config.DEFAULT_CHAT_MODE)}")
                return True
        except Exception as e:
            logger.error(f"{username}: Lỗi tải trạng thái: {str(e)}", exc_info=True)
            ui.notify("Lỗi tải trạng thái chat", type="negative")
            return False

    async def update_messages(self):
        username = self.client_state.get("username", "")
        if not self.rendered or not self.messages_container or not self.client_id or context.client.id != self.client_id:
            logger.warning(f"{username}: Giao diện chưa sẵn sàng")
            ui.notify("Lỗi: Giao diện chat chưa sẵn sàng", type="negative")
            return False
        try:
            async with asyncio.timeout(2):
                 # Tải lại tin nhắn từ SQLite để phản ánh dữ liệu sau đồng bộ
                await self.load_messages_from_db(username)
            
                self.messages_container.clear()
                with self.messages_container:
                    if not self.messages:
                        ui.label("Chưa có tin nhắn nào").classes("text-gray-500 text-center py-4")
                    for msg in self.messages:
                        if len(msg["content"].encode()) > 1_000_000:
                            logger.warning(f"{username}: Tin nhắn quá lớn: {msg['content'][:100]}...")
                            continue
                        role = "Bạn" if msg["role"] == "user" else "AI"
                        classes = "bg-blue-100 ml-4 mr-2 self-end" if msg["role"] == "user" else "bg-green-100 mr-4 ml-2 self-start"
                        with ui.element("div").classes(f"p-1 sm:p-2 mb-1 rounded {classes} max-w-[80%]"):
                            if msg.get("type") == "image":
                                ui.image(msg["content"]).classes("max-w-[80%] sm:max-w-xs rounded")
                            elif msg.get("type") == "html":
                                ui.html(msg["content"]).classes("text-sm prose")
                            else:
                                ui.markdown(f"**{role}**: {msg['content']}").classes("text-sm")
                            ui.label(f"({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(msg.get('timestamp', 0)))})").classes("text-xs text-gray-500")
                            if msg.get("error"):
                                ui.label(f"Lỗi: {msg['error']}").classes("text-red-500 text-xs")
                ui.update()
                await self.scroll_to_bottom()
                logger.info(f"{username}: Cập nhật tin nhắn thành công, số tin nhắn: {len(self.messages)}")
                return True
        except Exception as e:
            logger.error(f"{username}: Lỗi cập nhật tin nhắn: {str(e)}", exc_info=True)
            ui.notify("Lỗi cập nhật tin nhắn", type="negative")
            return False

    

    async def fuzzy_match_question(
        self,
        collection_name: str,
        question: str,
        username: str,
        limit: int = 1,
        threshold: float = 0.7,  # 70% cho BM25 score
    ):
        """Tìm kiếm chỉ bằng FTS5 (BM25), bỏ Levenshtein để đơn giản. Fallback SQL nếu không có FTS."""
        try:
            # Giả sử có hàm check_disk_space() nếu cần, nhưng có thể bỏ nếu không dùng
            # check_disk_space()  # Uncomment nếu cần
            
            # Clean và normalize query
            clean_question = question.rstrip('?.!;\'",.').strip().lower()
            norm_question = unidecode(clean_question) if HAS_UNIDECODE else clean_question
            if not clean_question:
                logger.info(f"{username}: Câu hỏi rỗng sau khi clean")
                return []

            async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=60.0) as conn:
                # Kiểm tra bảng qa_fts
                async with conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='qa_fts'"
                ) as cursor:
                    has_fts = await cursor.fetchone() is not None

                if not has_fts:
                    logger.warning(f"{username}: Bảng qa_fts không tồn tại, fallback SQL LIKE (không fuzzy)")
                    # Fallback đơn giản: LIKE %question% , sort bằng LENGTH (proxy cho match tốt)
                    async with conn.execute(
                        "SELECT question, answer FROM qa_data WHERE created_by = ? AND LOWER(question) LIKE ? "
                        "ORDER BY LENGTH(question) ASC LIMIT ?",
                        (username, f"%{clean_question}%", limit * 5)
                    ) as cursor:
                        rows = await cursor.fetchall()
                    if not rows:
                        return []
                    # Score giả: 100 * (len(clean_question) / len(q)) (càng khớp dài càng tốt)
                    results = []
                    for q, a in rows:
                        q_len = len(q)
                        match_len = len(clean_question)
                        score = max(0, 100 * (match_len / q_len)) if q_len > 0 else 0
                        if score >= threshold * 100:
                            results.append({"question": q, "answer": a, "score": score})
                    results = sorted(results, key=lambda x: x["score"], reverse=True)[:limit]
                    logger.info(f"{username}: SQL fallback, {len(results)} matches")
                    return results

                # FTS5 primary: Query kép cho clean + norm
                fts_query = f'({clean_question}* OR "{clean_question}" OR {norm_question}* OR "{norm_question}")'
                top_k = limit * 5  # Lấy rộng để fallback nếu cần
                query = """
                    SELECT qa_data.question, qa_data.answer, rank
                    FROM qa_fts JOIN qa_data ON qa_fts.rowid = qa_data.rowid
                    WHERE qa_fts MATCH ? AND qa_data.created_by = ?
                    ORDER BY rank LIMIT ?
                """
                async with conn.execute(query, (fts_query, username, top_k)) as cursor:
                    rows = await cursor.fetchall()

                if not rows:
                    logger.info(f"{username}: FTS5 không tìm thấy match cho '{fts_query}'")
                    return []

                # Tính BM25 score và filter
                results = []
                for q, a, rank in rows:
                    # BM25 score: Rank thấp (âm/0) = tốt. Normalize: 100 / (1 + |rank|)
                    bm25_score = 100 / (1 + abs(rank))
                    logger.debug(f"{username}: Candidate '{q[:30]}...' - Rank: {rank}, BM25: {bm25_score:.1f}")
                    
                    if bm25_score >= threshold * 100:
                        results.append({"question": q, "answer": a, "score": bm25_score})

                results = sorted(results, key=lambda x: x["score"], reverse=True)[:limit]
                
                # Fallback nếu không đủ threshold: Return top 1 với score thấp (tránh mất dữ liệu)
                if not results and rows:
                    top_q, top_a, top_rank = rows[0]
                    fallback_score = 100 / (1 + abs(top_rank))
                    logger.warning(f"{username}: Không đạt threshold, fallback top BM25: score {fallback_score:.1f}")
                    results = [{"question": top_q, "answer": top_a, "score": max(50, fallback_score)}]  # Min 50 để warn

                logger.info(f"{username}: FTS5 tìm thấy {len(results)} câu hỏi tương đồng cho '{fts_query}'")
                return results

        except asyncio.TimeoutError:
            logger.error(f"{username}: Timeout trong fuzzy_match_question")
            return []
        except Exception as e:
            logger.error(f"{username}: Lỗi tìm kiếm FTS5: {str(e)}", exc_info=True)
            return []
                    
    async def call_grok_api(self, message: str, context: str, username: str) -> Dict:
        if not self.groq_client:
            logger.error(f"{username}: GROQ_API_KEY không được cấu hình")
            ui.notify("Lỗi: GROQ_API_KEY không được cấu hình", type="negative")
            return {"error": "GROQ_API_KEY không được cấu hình"}
        try:
            validate({"message": message, "context": context}, {
                "type": "object",
                "properties": {"message": {"type": "string", "minLength": 1}, "context": {"type": "string"}},
                "required": ["message"]
            })
            chat_completion = await self.groq_client.chat.completions.create(
                messages=[
                    {"role": "system", "content": "Bạn là Grok, được tạo bởi xAI. Trả lời chính xác, ngắn gọn, hữu ích."},
                    {"role": "user", "content": f"{context}\n{message}" if context else message}
                ],
                model=self.client_state.get("model", Config.DEFAULT_MODEL),
                temperature=0.7,
                max_tokens=1000
            )
            response = chat_completion.choices[0].message.content
            if not response.strip():
                logger.error(f"{username}: Phản hồi Grok rỗng")
                return {"error": "Phản hồi từ API Grok rỗng"}
            logger.info(f"{username}: Gọi Grok API thành công")
            return {"success": "Grok API thành công", "response": response}
        except Exception as e:
            logger.error(f"{username}: Lỗi gọi Grok API: {str(e)}", exc_info=True)
            ui.notify("Lỗi gọi API Grok", type="negative")
            return {"error": str(e)}

    async def reset(self):
        username = self.client_state.get("username", "")
        try:
            async with asyncio.timeout(10):
                check_disk_space()
                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=60.0) as conn:
                    async with conn.execute(
                        "SELECT id FROM chat_messages WHERE session_token = ? AND username = ?",
                        (self.client_state["session_token"], username)
                    ) as cursor:
                        record_ids = [row[0] for row in await cursor.fetchall()]
                    await conn.execute(
                        "DELETE FROM chat_messages WHERE session_token = ? AND username = ?",
                        (self.client_state["session_token"], username)
                    )
                    current_time = int(time.time())
                    for record_id in record_ids:
                        details = {
                            "username": username,
                            "action": "delete_chat_history",
                            "collection_name": "chat_messages",
                            "record_id": record_id,
                            "session_token": self.client_state["session_token"]
                        }
                        try:
                            details_json = json.dumps(details, ensure_ascii=False)
                        except TypeError as e:
                            logger.error(f"{username}: Lỗi mã hóa JSON cho sync_log: {str(e)}")
                            details_json = json.dumps({"error": "Không thể mã hóa details"}, ensure_ascii=False)
                        await conn.execute(
                            "INSERT INTO sync_log (id, table_name, record_id, action, timestamp, details) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                str(uuid.uuid4()),
                                "chat_messages",
                                record_id,
                                "DELETE",
                                current_time,
                                details_json
                            )
                        )
                    await conn.commit()
                await self.on_reset()
                self.messages = []
                await self.update_messages()
                await self.scroll_to_bottom()
                logger.info(f"{username}: Xóa lịch sử chat thành công, ghi {len(record_ids)} log vào sync_log")
        except Exception as e:
            logger.error(f"{username}: Lỗi xóa lịch sử: {str(e)}", exc_info=True)
            ui.notify("Lỗi xóa lịch sử chat", type="negative")