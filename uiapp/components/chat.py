
from nicegui import ui, context, app
import mimetypes
import os
from typing import List, Dict, Callable, Optional
import asyncio
import time
import aiosqlite
import json
import hashlib
import uuid
from PIL import Image
import io

from utils.logging import get_logger
from utils.core_common import validate_name, check_disk_space
from config import Config
from jsonschema import validate, ValidationError as JSONSchemaValidationError
from fuzzywuzzy import fuzz
from Levenshtein import distance as levenshtein_distance
from datetime import datetime
from uiapp.language import get_text

try:
    from unidecode import unidecode
    HAS_UNIDECODE = True
except ImportError:
    HAS_UNIDECODE = False

    def unidecode(text):
        return text


logger = get_logger("ChatComponent")

class ChatComponent:
    def __init__(
        self,
        messages: List[Dict],
        on_send: Callable,
        on_reset: Callable,
        on_model_change: Callable,
        classes: str = "w-full p-2 sm:p-4",
        send_button_label: str = "send_button",
        message_limit: int = 500,
        placeholder: str = "message_input_placeholder",
        core: Optional["Core"] = None,
        client_state: Optional[Dict] = None,
        groq_client: Optional[object] = None,
        language: str = None  # Bỏ mặc định "vi"
    ):
        self.client_state = client_state or {}
        self.language = self.client_state.get("language", app.storage.user.get("language", "vi"))
        if language and language in ["vi", "en"]:  # Chỉ cho phép ghi đè nếu hợp lệ
            self.language = language
            logger.warning(f"Ngôn ngữ được ghi đè thành {language}, client_state['language']={self.client_state.get('language')}")
        
        self.messages = messages[:message_limit] if messages else []
        self.on_send = on_send
        self.on_reset = on_reset
        self.on_model_change = on_model_change
        self.classes = classes
        self.send_button_label = send_button_label
        self.message_limit = message_limit
        self.placeholder = placeholder
        self.message_input = None
        self.upload_input = None
        self.messages_container = None
        self.loading = False
        self.core = core
        self.groq_client = groq_client
        self.container = None
        self.rendered = False
        self.client_id = None
        self.processing_lock = asyncio.Lock()
        self.last_message_id = None
        self.progress = None
        self.displayed_message_ids = set()
        self.qa_threshold = getattr(Config, "QA_SEARCH_THRESHOLD", 0.7)
        
        if "model" not in self.client_state:
            self.client_state["model"] = Config.DEFAULT_MODEL
        if "chat_mode" not in self.client_state:
            self.client_state["chat_mode"] = Config.DEFAULT_CHAT_MODE

        logger.info(f"{self.client_state.get('username', '')}: Khởi tạo: model={self.client_state['model']}, chat_mode={self.client_state['chat_mode']}, language={self.language}")

    
    async def handle_mode_change(self, mode_select, mode_options: List[str]):
        username = self.client_state.get("username", "")
        if self.loading:
            logger.warning(f"{username}: Không thể đổi chế độ khi đang xử lý")
            ui.notify(get_text(self.language, "wait_processing"), type="negative")
            return
        try:
            async with asyncio.timeout(10):
                if isinstance(mode_select, dict) and "value" in mode_select:
                    new_mode = mode_options[mode_select["value"]] if isinstance(mode_select["value"], int) else mode_select["value"]
                else:
                    new_mode = mode_select
                if new_mode not in ["QA", "Grok", "Hybrid"]:
                    logger.error(f"{username}: Chế độ không hợp lệ: {new_mode}")
                    ui.notify(get_text(self.language, "invalid_mode_error"), type="negative")
                    return
                self.client_state["chat_mode"] = new_mode
                await self.save_state_and_config(username)
                logger.info(f"{username}: Đổi chế độ thành {new_mode}")
                ui.notify(get_text(self.language, "mode_changed", mode=new_mode), type="positive")
                ui.update()
        except Exception as e:
            logger.error(f"{username}: Lỗi đổi chế độ: {str(e)}", exc_info=True)
            ui.notify(get_text(self.language, "mode_change_error"), type="negative")
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
                self.client_state["language"] = self.language
                if not Config.SHOW_MODEL_COMBOBOX:
                    self.client_state["model"] = Config.DEFAULT_MODEL
                if not Config.SHOW_MODE_COMBOBOX:
                    self.client_state["chat_mode"] = Config.DEFAULT_CHAT_MODE
                self.client_state.pop("chat_messages", None)
                state_json = json.dumps(self.client_state)
                if len(state_json.encode()) > 1_000_000:
                    logger.error(f"{username}: Trạng thái quá lớn")
                    ui.notify(get_text(self.language, "state_too_large_error"), type="negative")
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
                    await conn.execute(
                        """
                        INSERT INTO sync_log (id, table_name, record_id, action, timestamp, details)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid.uuid4()),
                            "client_states",
                            state_id,
                            "INSERT",
                            self.client_state["timestamp"],
                            json.dumps({
                                "username": username,
                                "action": "save_client_state",
                                "session_token": self.client_state["session_token"]
                            }, ensure_ascii=False)
                        )
                    )
                    await conn.commit()
                logger.info(f"{username}: Lưu cấu hình thành công, model={self.client_state['model']}, chat_mode={self.client_state['chat_mode']}")
                return True
        except Exception as e:
            logger.error(f"{username}: Lỗi lưu cấu hình: {str(e)}", exc_info=True)
            ui.notify(get_text(self.language, "system_error", error=str(e)), type="negative")
            return False

    async def load_state_and_render(self, container=None):
        username = self.client_state.get("username", "")
        try:
            async with asyncio.timeout(10):
                check_disk_space()
                if "session_token" not in self.client_state or not username or not validate_name(self.client_state["session_token"]):
                    logger.warning(f"{username}: Phiên đăng nhập không hợp lệ")
                    ui.notify(get_text(self.language, "invalid_session"), type="negative")
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
                            await conn.execute(
                                """
                                INSERT INTO sync_log (id, table_name, record_id, action, timestamp, details)
                                VALUES (?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    str(uuid.uuid4()),
                                    "client_states",
                                    state_id,
                                    "INSERT",
                                    self.client_state["timestamp"],
                                    json.dumps({
                                        "username": username,
                                        "action": "create_client_state",
                                        "session_token": self.client_state["session_token"]
                                    }, ensure_ascii=False)
                                )
                            )
                            await conn.commit()
                        else:
                            self.client_state.update(json.loads(row[0]))
                            self.client_state.pop("chat_messages", None)
                await self.load_messages_from_db(username)
                await self.save_state_and_config(username)
                success = await self.render()
                if not success:
                    ui.notify(get_text(self.language, "chat_ui_load_error"), type="negative")
                    return False
                logger.info(f"{username}: Tải trạng thái thành công, chat_mode={self.client_state.get('chat_mode', Config.DEFAULT_CHAT_MODE)}")
                return True
        except Exception as e:
            logger.error(f"{username}: Lỗi tải trạng thái: {str(e)}", exc_info=True)
            ui.notify(get_text(self.language, "chat_state_load_error"), type="negative")
            return False

    
    
    
    



    
    async def call_grok_api(
        self,
        message: str,
        context: str = "",
        username: str = "",
        file_url: str = None,
        history_limit: int = 5
    ) -> Dict:
        if not self.groq_client:
            logger.error(f"{username}: GROQ_API_KEY không được cấu hình")
            ui.notify(
                get_text(self.language, "grok_api_key_missing"),
                type="negative"
            )
            return {"error": get_text(self.language, "grok_api_key_missing")}

        try:
            # Check input
            if not isinstance(message, str) or not message.strip():
                logger.error(f"{username}: Message không hợp lệ: {message}")
                return {"error": "Message phải là string không rỗng"}

            context = str(context) if context else ""
            file_url = str(file_url) if file_url else ""

            logger.debug(
                f"{username}: Input - message: {message[:30]}..., "
                f"context: {context[:30]}..., file_url: {file_url}"
            )

            # Schema validate
            schema = {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "minLength": 1},
                    "context": {"type": "string"},
                    "file_url": {"type": "string"}
                },
                "required": ["message"]
            }
            validate({"message": message, "context": context, "file_url": file_url}, schema)
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S') 
            system_prompt = Config.SYSTEM_PROMPTS.get(
                self.language, Config.SYSTEM_PROMPTS["vi"]
            )
            system_prompt += f"\nThời gian hiện tại là: {current_time}."  # Thêm dòng này

            messages = [{"role": "system", "content": system_prompt}]

            # History: Chronological (cũ → mới), filter valid messages
            recent_history = self.messages[-history_limit * 2:] if self.messages else []
            for msg in recent_history:
                if (
                    msg["role"] in ["user", "assistant"]
                    and isinstance(msg["content"], str)
                    and len(msg["content"].strip()) > 2
                ):
                    content = msg["content"]
                    if msg.get("file_url") and Config.GROK_VISION_ENABLED:
                        content += f" [File: {msg['file_url']}]"
                    messages.append({"role": msg["role"], "content": content})
                    if len(messages) > history_limit * 2 + 2:
                        break

            logger.debug(f"{username}: Gửi {len(messages) - 1} turns history cho Grok")

            # Context (QA answer) và current message tách riêng
            if context:
                messages.append({"role": "assistant", "content": f"QA Context: {context}"})
            messages.append({"role": "user", "content": message})

            if file_url and Config.GROK_VISION_ENABLED:
                messages.append({"role": "user", "content": f"[File: {file_url}]"})

            # Grok call
            chat_completion = await self.groq_client.chat.completions.create(
                messages=messages,
                model=self.client_state.get("model", Config.DEFAULT_MODEL),
                temperature=0.7,
                max_tokens=1000
            )
            response = chat_completion.choices[0].message.content

            if not response.strip():
                logger.error(f"{username}: Phản hồi Grok rỗng")
                return {"error": get_text(self.language, "grok_api_error")}

            logger.info(f"{username}: Grok API thành công với {len(messages) - 1} messages")
            return {"success": "Grok API thành công", "response": response}

        except Exception as e:
            logger.error(f"{username}: Lỗi Grok API: {str(e)}", exc_info=True)
            ui.notify(get_text(self.language, "grok_api_error"), type="negative")
            return {"error": str(e)}
    
    
    
    async def reset(self):
        username = self.client_state.get("username", "")
        try:
            async with asyncio.timeout(10):
                check_disk_space()
                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=60.0) as conn:
                    # Lấy danh sách tin nhắn của username
                    async with conn.execute(
                        """
                        SELECT id, file_url 
                        FROM chat_messages 
                        WHERE username = ?
                        """,
                        (username,),
                    ) as cursor:
                        rows = await cursor.fetchall()
                        record_ids = [row[0] for row in rows]
                        file_urls = [row[1] for row in rows if row[1]]

                    # Xóa tin nhắn chỉ của username
                    await conn.execute(
                        "DELETE FROM chat_messages WHERE username = ?",
                        (username,),
                    )

                    current_time = int(time.time())
                    for record_id, file_url in [(row[0], row[1]) for row in rows]:
                        details = {
                            "username": username,
                            "action": "delete_chat_history",
                            "record_id": record_id,
                        }
                        if file_url:
                            details["file_url"] = file_url  # Lưu file_url vào log
                        await conn.execute(
                            """
                            INSERT INTO sync_log 
                            (id, table_name, record_id, action, timestamp, details)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                str(uuid.uuid4()),
                                "chat_messages",
                                record_id,
                                "DELETE",
                                current_time,
                                json.dumps(details, ensure_ascii=False),
                            ),
                        )

                    # Xóa log đồng bộ để đảm bảo phục hồi dữ liệu từ Firestore
                    await conn.execute(
                        """
                        DELETE FROM sync_log 
                        WHERE table_name = ? 
                        AND action IN ('sync_to_firestore', 'sync_to_sqlite')
                        AND details LIKE ?
                        """,
                        ("chat_messages", f'%{{"username": "{username}"%'),
                    )
                    await conn.commit()

                    logger.debug(
                        f"{username}: Đã xóa {len(record_ids)} tin nhắn và log đồng bộ, giữ lại file vật lý"
                    )

                self.messages = []
                self.displayed_message_ids.clear()
                await self.update_messages()
                await self.scroll_to_bottom()

                logger.info(
                    f"{username}: Xóa lịch sử chat thành công, ghi {len(record_ids)} log DELETE vào sync_log"
                )

                if context.client.has_socket_connection:
                    ui.notify(
                        get_text(
                            self.language,
                            "deleted_messages",
                            count=len(record_ids),
                            username=username,
                        ),
                        type="positive",
                    )

        except Exception as e:
            logger.error(f"{username}: Lỗi xóa lịch sử: {str(e)}", exc_info=True)
            ui.notify(get_text(self.language, "reset_history_error"), type="negative")

    
    async def find_related_context(self, message: str, username: str) -> str:
        try:
            clean_message = message.rstrip('?.!;,').strip().lower()
            recent_history = self.messages[-20:] if self.messages else []
            best_match = {"content": "", "score": 0}

            for msg in reversed(recent_history):
                if msg["role"] in ["user", "assistant"] and isinstance(msg["content"], str):
                    content_lower = msg["content"].lower()
                    partial_similarity = fuzz.partial_ratio(clean_message, content_lower)
                    token_similarity = fuzz.token_sort_ratio(clean_message, content_lower)
                    similarity = max(partial_similarity, token_similarity)
                    if similarity > 70:
                        if similarity > best_match["score"]:
                            best_match = {"content": msg["content"], "score": similarity}
            
            if best_match["content"]:
                logger.info(f"{username}: Tìm thấy bối cảnh liên quan trong lịch sử chat (độ tương đồng: {best_match['score']}): {best_match['content'][:50]}...")
            else:
                logger.info(f"{username}: Không tìm thấy bối cảnh liên quan trong lịch sử chat")
            return best_match["content"]
        except Exception as e:
            logger.error(f"{username}: Lỗi tìm bối cảnh liên quan: {str(e)}")
            return ""

    
    async def fuzzy_match_question(
        self,
        collection_name: str,
        question: str,
        username: str,
        limit: int = 1,
        threshold: float = 0.5
    ):
        """Tìm câu hỏi tương đồng trong qa_data, ưu tiên khớp chính xác nhất."""
        try:
            check_disk_space()

            # Làm sạch câu hỏi nhưng giữ các từ khóa quan trọng
            clean_search = question.strip().lower()
            if not clean_search:
                logger.info(f"{username}: Câu hỏi rỗng sau khi clean.")
                return []

            logger.debug(
                f"{username}: fuzzy_match_question | "
                f"Original='{question}', Cleaned='{clean_search}'"
            )

            async with aiosqlite.connect(Config.SQLITE_DB_PATH) as conn:
                # FTS5 QUERY
                try:
                    fts_query = f'({clean_search}* OR "{clean_search}" OR {clean_search})'
                    logger.debug(f"{username}: FTS query: {fts_query}")

                    query = """
                        SELECT qa_data.id, qa_data.question, qa_data.answer, qa_data.category,
                               qa_data.created_by, qa_data.created_at, qa_data.timestamp
                        FROM qa_fts
                        JOIN qa_data ON qa_fts.rowid = qa_data.rowid
                        WHERE qa_fts MATCH ? AND qa_data.created_by = ?
                        ORDER BY rank, qa_data.timestamp DESC
                        LIMIT ?
                    """
                    params = [fts_query, username, limit]
                    cursor = await conn.execute(query, params)
                    rows = await cursor.fetchall()

                    if not rows:
                        logger.info(
                            f"{username}: FTS5 không tìm thấy kết quả cho '{clean_search}'"
                        )
                    else:
                        logger.info(
                            f"{username}: FTS5 tìm thấy {len(rows)} kết quả cho '{clean_search}'"
                        )

                    results = [
                        {
                            "id": row[0],
                            "question": row[1],
                            "answer": row[2],
                            "category": row[3],
                            "created_by": row[4],
                            "created_at": row[5],
                            "timestamp": row[6],
                        }
                        for row in rows
                    ]

                    if results:
                        return results

                except Exception as fts_error:
                    logger.warning(
                        f"{username}: Lỗi FTS5: {fts_error}. "
                        "Fallback sang fuzzy search."
                    )

                # FALLBACK FUZZY SEARCH
                async with conn.execute(
                    """
                    SELECT id, question, answer, category, created_by,
                           created_at, timestamp
                    FROM qa_data
                    WHERE created_by = ?
                    ORDER BY timestamp DESC
                    LIMIT 1000
                    """,
                    (username,)
                ) as cursor:
                    all_rows = await cursor.fetchall()

                if not all_rows:
                    logger.info(f"{username}: Không có dữ liệu Q&A để fuzzy search.")
                    return []

                search_lower = clean_search
                matches = []
                for row in all_rows:
                    q_lower = row[1].lower() if row[1] else ""
                    a_lower = row[2].lower() if row[2] else ""
                    max_ratio = max(
                        fuzz.ratio(search_lower, q_lower),
                        fuzz.ratio(search_lower, a_lower),
                        fuzz.token_sort_ratio(search_lower, q_lower),
                        fuzz.token_sort_ratio(search_lower, a_lower),
                        fuzz.partial_ratio(search_lower, q_lower),
                        fuzz.partial_ratio(search_lower, a_lower)
                    )
                    if max_ratio >= threshold * 100:
                        matches.append(
                            {
                                "id": row[0],
                                "question": row[1],
                                "answer": row[2],
                                "category": row[3],
                                "created_by": row[4],
                                "created_at": row[5],
                                "timestamp": row[6],
                                "score": round(max_ratio, 1),
                            }
                        )

                matches.sort(key=lambda x: (x["score"], x["timestamp"]), reverse=True)
                logger.info(
                    f"{username}: Fuzzy tìm thấy {len(matches)} kết quả "
                    f"trên {len(all_rows)} bản ghi cho '{clean_search}'"
                )

                return matches[:limit]

        except Exception as e:
            logger.error(f"{username}: Lỗi fuzzy_match_question: {str(e)}", exc_info=True)
            return []

    
    
    
    
    async def load_messages_from_db(self, username: str):
        try:
            async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=60.0) as conn:
                async with conn.execute(
                    """
                    SELECT id, content, role, type, file_url, timestamp
                    FROM chat_messages
                    WHERE session_token = ? AND username = ?
                    ORDER BY timestamp ASC LIMIT ?
                    """,
                    (self.client_state["session_token"], username, self.message_limit)
                ) as cursor:
                    self.messages = [
                        {
                            "id": row[0],
                            "content": row[1] or "",
                            "role": row[2],
                            "type": row[3] or "text",
                            "file_url": row[4],
                            "timestamp": row[5] or int(time.time())
                        }
                        for row in await cursor.fetchall()
                    ]
                    logger.info(f"{username}: Loaded {len(self.messages)} messages from chat_messages")

        except Exception as e:
            logger.error(f"{username}: Error loading messages from DB: {str(e)}", exc_info=True)
            ui.notify(get_text(self.language, "load_messages_error"), type="negative")
            self.messages = []

    
    
    async def update_messages(self):
        username = self.client_state.get("username", "")
        if not self.rendered or not self.messages_container:
            logger.warning(
                f"{username}: UI not ready (rendered={self.rendered}, "
                f"messages_container={self.messages_container})"
            )
            ui.notify(get_text(self.language, "chat_ui_not_ready"), type="negative")
            return False

        try:
            async with asyncio.timeout(2):
                if not self.messages:
                    await self.load_messages_from_db(username)
                    self.messages_container.clear()
                    self.displayed_message_ids.clear()

                new_messages = [
                    msg for msg in self.messages
                    if msg["id"] not in self.displayed_message_ids
                ]

                with self.messages_container:  # Đảm bảo slot UI
                    if not self.messages:
                        self.messages_container.clear()
                        ui.label(
                            get_text(self.language, "no_messages_label")
                        ).classes("text-gray-500 text-center py-4")

                    for msg in new_messages:
                        if len((msg["content"] or "").encode()) > 1_000_000:
                            logger.warning(
                                f"{username}: Message too large: {msg['content'][:100]}..."
                            )
                            continue

                        role = (
                            get_text(self.language, "user_role_label")
                            if msg["role"] == "user"
                            else get_text(self.language, "ai_role_label")
                        )
                        classes = (
                            "bg-blue-100 self-start"
                            if msg["role"] == "user"
                            else "bg-green-100 self-start"
                        )

                        with ui.element("div").classes(
                            f"p-1 sm:p-2 mb-1 rounded {classes} max-w-full "
                            f"sm:max-w-[98%] whitespace-normal"
                        ).props(f"id=message-{msg['id']}"):

                            if msg.get("type") == "image" and msg.get("file_url"):
                                file_id = msg["file_url"].split("/")[-1]
                                file_path = os.path.join(
                                    Config.CHAT_FILE_STORAGE_PATH, file_id
                                )
                                if os.path.exists(file_path):
                                    ui.image(msg["file_url"]).classes(
                                        "max-w-[90%] sm:max-w-xs rounded object-contain"
                                    )
                                else:
                                    logger.error(f"{username}: Image not found: {file_path}")
                                    ui.label(
                                        get_text(
                                            self.language,
                                            "image_not_found",
                                            file_url=msg["file_url"]
                                        )
                                    ).classes("text-red-500")

                            elif msg.get("type") == "file" and msg.get("file_url"):
                                filename = msg["content"].replace(
                                    "[Uploaded file: ", ""
                                ).rstrip("]")
                                max_filename_length = 50
                                if len(filename) > max_filename_length:
                                    name, ext = os.path.splitext(filename)
                                    short_filename = (
                                        f"{name[:max_filename_length-4-len(ext)]}...{ext}"
                                    )
                                else:
                                    short_filename = filename

                                ui.link(
                                    f"{get_text(self.language, 'download_file_label', default='Tải file')}: "
                                    f"{short_filename}",
                                    msg["file_url"]
                                ).classes("text-blue-600 whitespace-normal")

                            else:
                                content = msg["content"] if msg["content"] else "..."
                                ui.markdown(f"**{role}**: {content}").classes(
                                    "text-sm whitespace-normal"
                                )

                            if msg["content"]:
                                ui.label(
                                    f"({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(msg.get('timestamp', 0)))})"
                                ).classes("text-xs text-gray-500")

                            if msg.get("error"):
                                ui.label(
                                    get_text(
                                        self.language,
                                        "error_label",
                                        default="Lỗi: {error}",
                                        error=msg["error"]
                                    )
                                ).classes("text-red-500 text-xs")

                        self.displayed_message_ids.add(msg["id"])

                ui.update()
                await self.scroll_to_bottom()
                logger.info(
                    f"{username}: Updated {len(new_messages)} new messages, "
                    f"total: {len(self.messages)}"
                )
                return True

        except Exception as e:
            logger.error(f"{username}: Error updating messages: {str(e)}", exc_info=True)
            ui.notify(get_text(self.language, "update_messages_error"), type="negative")
            return False

    async def render(self):
        username = self.client_state.get("username", "")
        self.client_id = getattr(context.client, "id", None)
        if (
            not self.client_id
            or not self.client_state
            or "session_token" not in self.client_state
            or not validate_name(self.client_state["session_token"])
        ):
            logger.error(f"{username}: Phiên hoặc client_id không hợp lệ")
            ui.notify(get_text(self.language, "invalid_session"), type="negative")
            return False

        try:
            async with asyncio.timeout(30):
                check_disk_space()
                client_storage = app.storage.client.setdefault(self.client_id, {})
                new_container = ui.element("div").classes("w-full p-1 sm:p-4")

                with new_container:
                    ui.label(get_text(self.language, "chat_ai_label")).classes(
                        "text-lg font-semibold mb-2"
                    )
                    self.messages_container = ui.scroll_area().classes(
                        "flex-1 mb-2 h-[50vh] sm:h-[60vh]"
                    )

                    if Config.SHOW_MODEL_COMBOBOX:
                        model_select = ui.select(
                            Config.AVAILABLE_MODELS,
                            label=get_text(self.language, "select_model_label"),
                            value=self.client_state.get(
                                "model", Config.DEFAULT_MODEL
                            ),
                        ).classes("w-full sm:w-1/4 mb-2").on(
                            "update:modelValue",
                            lambda e: self.on_model_change(e.args),
                        )
                    else:
                        self.client_state["model"] = Config.DEFAULT_MODEL

                    if Config.SHOW_MODE_COMBOBOX:
                        mode_options = ["Grok", "QA", "Hybrid"]
                        mode_select = ui.select(
                            mode_options,
                            label=get_text(self.language, "select_mode_label"),
                            value=self.client_state.get(
                                "chat_mode", Config.DEFAULT_CHAT_MODE
                            ),
                        ).classes("w-full sm:w-1/4 mb-2").on(
                            "update:modelValue",
                            lambda e: self.handle_mode_change(e.args, mode_options),
                        )
                    else:
                        self.client_state["chat_mode"] = Config.DEFAULT_CHAT_MODE

                    with ui.element("div").classes("w-full flex flex-col sm:flex-row gap-1"):
                        self.message_input = ui.textarea(
                            label=get_text(self.language, "message_input_label"),
                            placeholder=get_text(
                                self.language,
                                self.placeholder,
                                default="Enter your message..."
                            ),
                        ).props("clearable").classes("flex-1").bind_enabled_from(
                            self, "loading", backward=lambda x: not x
                        ).on(
                            "keydown.enter",
                            lambda e: self.handle_send()
                            if not e.args.get("repeat")
                            else None,
                        )

                        self.upload_input = ui.upload(
                            label=get_text(self.language, "upload_file_label"),
                            auto_upload=False,
                            on_upload=lambda e: self.handle_upload(e),
                        ).props(
                            f'accept="{",".join(Config.CHAT_FILE_ALLOWED_FORMATS)}"'
                        ).classes("w-full sm:w-auto").bind_enabled_from(
                            self, "loading", backward=lambda x: not x
                        )

                        ui.button(
                            get_text(self.language, "upload_button"),
                            on_click=lambda: self.upload_input.run_method("upload"),
                        ).classes(
                            "bg-blue-600 text-white hover:bg-blue-700 w-full sm:w-auto"
                        ).bind_enabled_from(
                            self, "loading", backward=lambda x: not x
                        )

                        ui.button(
                            get_text(self.language, self.send_button_label),
                            on_click=self.handle_send,
                            icon="send",
                        ).classes(
                            "bg-blue-600 text-white w-full sm:w-auto"
                        ).bind_enabled_from(
                            self, "loading", backward=lambda x: not x
                        )

                        ui.button(
                            get_text(self.language, "reset_button"),
                            on_click=self.reset,
                            icon="delete",
                        ).classes("bg-red-600 text-white w-full sm:w-auto")

                if self.container:
                    if (
                        hasattr(self.container, "parent_slot")
                        and self.container in self.container.parent_slot.children
                    ):
                        self.container.clear()
                        self.container.delete()
                    else:
                        logger.debug(
                            f"{username}: Bỏ qua xóa container cũ vì không tồn tại trong danh sách children"
                        )

                self.container = new_container
                client_storage["chat_card_container"] = new_container
                self.rendered = True
                self.displayed_message_ids.clear()

                await self.load_messages_from_db(username)
                await self.update_messages()
                await self.scroll_to_bottom()

                if not self.messages_container or not self.message_input:
                    raise RuntimeError(get_text(self.language, "chat_ui_not_ready"))

                logger.info(f"{username}: Render giao diện chat thành công")
                return True

        except Exception as e:
            logger.error(f"{username}: Lỗi render: {str(e)}", exc_info=True)
            ui.notify(get_text(self.language, "chat_ui_load_error"), type="negative")
            if new_container:
                try:
                    new_container.delete()
                except Exception as e:
                    logger.warning(f"{username}: Lỗi khi xóa container mới: {str(e)}")
            self.rendered = False
            return False
            
    
    async def handle_send(self):
        username = self.client_state.get("username", "")
        chat_mode = self.client_state.get("chat_mode", Config.DEFAULT_CHAT_MODE)
        logger.debug(f"{username}: Sending message, mode={chat_mode}")

        async with self.processing_lock:
            if (
                not self.rendered
                or not self.message_input
                or not self.messages_container
                or not self.client_id
            ):
                logger.warning(f"{username}: UI not ready or client_id missing")
                ui.notify(get_text(self.language, "chat_ui_not_ready"), type="negative")
                return

            if chat_mode not in ["QA", "Grok", "Hybrid"]:
                logger.error(f"{username}: Invalid mode: {chat_mode}")
                ui.notify(get_text(self.language, "invalid_chat_mode_error"), type="negative")
                return

            message = self.message_input.value.strip()
            if not message:
                ui.notify(get_text(self.language, "empty_message_error"), type="negative")
                return

            if len(message.encode()) > 1_000_000:
                ui.notify(get_text(self.language, "message_too_long_error"), type="negative")
                return

            current_time = int(time.time())
            current_message_id = hashlib.sha256(f"{message}_{current_time}".encode()).hexdigest()

            if (
                self.last_message_id == current_message_id
                or any(
                    msg["content"] == message and abs(msg["timestamp"] - current_time) < 2
                    for msg in self.messages
                )
            ):
                logger.debug(f"{username}: Duplicate message, ignoring")
                return

            self.last_message_id = current_message_id
            self.loading = True

            with self.container:
                self.progress = ui.linear_progress().classes("w-full")

            try:
                # Save user message
                result = await self.core.add_chat_message(
                    username=username,
                    session_token=self.client_state["session_token"],
                    content=message,
                    role="user",
                    message_type="text",
                )
                user_message_id = result["message_id"]
                self.messages.append({
                    "id": user_message_id,
                    "content": message,
                    "role": "user",
                    "type": "text",
                    "timestamp": current_time,
                })

                await self.update_messages()
                await self.scroll_to_bottom()
                ui.notify(get_text(self.language, "processing_mode", mode=chat_mode), type="info")
                ui.update()

                # Process response
                if chat_mode == "QA":
                    matches = await self.fuzzy_match_question(
                        "qa_data", message, username, limit=1, threshold=self.qa_threshold
                    )
                    response = (
                        matches[0]["answer"]
                        if matches
                        else get_text(self.language, "no_qa_answer")
                    )
                    if not matches:
                        ui.notify(get_text(self.language, "no_qa_answer"), type="negative")

                elif chat_mode == "Hybrid":
                    # Tìm top 3 QA khớp nhất
                    matches = await self.fuzzy_match_question(
                        "qa_data", message, username, limit=3, threshold=self.qa_threshold
                    )
                    logger.info(f"{username}: Hybrid mode - Found {len(matches)} QA matches")

                    if not matches:
                        qa_context = "No relevant QA found."
                        ui.notify(get_text(self.language, "no_qa_answer"), type="warning")
                    else:
                        # Ước lượng score nếu FTS không có
                        for i, match in enumerate(matches):
                            if "score" not in match:
                                match["score"] = 90 - i * 10

                        qa_context_parts = []
                        for i, match in enumerate(matches):
                            truncated_question = (
                                match["question"][:300]
                                + ("..." if len(match["question"]) > 300 else "")
                            )
                            truncated_answer = (
                                match["answer"][:500]
                                + ("..." if len(match["answer"]) > 500 else "")
                            )
                            qa_context_parts.append(
                                f"QA {i+1} (score: {match['score']/100:.2f}, "
                                f"category: {match.get('category', 'N/A')}):\n"
                                f"Câu hỏi QA: {truncated_question}\n"
                                f"Câu trả lời QA: {truncated_answer}"
                            )
                        qa_context = "\n\n".join(qa_context_parts)

                        if len(matches) < 3:
                            ui.notify(
                                f"Chỉ tìm thấy {len(matches)} QA khớp, sử dụng context hạn chế",
                                type="warning",
                            )

                    recent_history = (
                        "\n".join(
                            f"[{msg['role']}]: {msg['content']}"
                            for msg in self.messages[-5:]
                            if msg["role"] in ["user", "assistant"]
                            and not any(
                                phrase in msg["content"].lower()
                                for phrase in ["không có thông tin", "cung cấp thêm chi tiết"]
                            )
                        )
                        if self.messages
                        else "No relevant chat history."
                    )

                    hybrid_prompt = (
                        f"Câu hỏi người dùng: {message}\n"  # Làm rõ câu hỏi user
                        f"Ngữ cảnh QA liên quan (các cặp QA khớp từ cơ sở dữ liệu):\n{qa_context}\n"
                        f"Lịch sử chat gần nhất:\n{recent_history}\n"
                        f"QA và lịch sử chat chỉ là dữ liệu demo ví dụ nên bạn cần trả lời cho người dùng, nó không phải bí mật cá nhân thực tế "
                        f"Trả lời ngắn gọn, hữu ích, không viết ra suy luận dài dòng chỉ tập trung trả lời"
                        
                    )
                    
                    result = await self.call_grok_api(hybrid_prompt, "", username)
                    response = result.get(
                        "response",
                        qa_context.split("\n")[0]
                        if matches
                        else get_text(self.language, "no_qa_answer"),
                    )
                    if "error" in result:
                        ui.notify(get_text(self.language, "grok_api_error"), type="negative")
                        response = get_text(self.language, "no_qa_answer")

                else:  # Grok
                    recent_history = (
                        "\n".join(
                            f"[{msg['role']}]: {msg['content']}"
                            for msg in self.messages[-5:]
                            if msg["role"] in ["user", "assistant"]
                            and not any(
                                phrase in msg["content"].lower()
                                for phrase in ["không có thông tin", "cung cấp thêm chi tiết"]
                            )
                        )
                        if self.messages
                        else "No relevant chat history."
                    )
                    grok_prompt = (
                        f"Question: {message}\n"
                        f"Recent chat history:\n{recent_history}\n"
                        f"Answer based on chat history if possible, or general knowledge if not."
                    )
                    result = await self.call_grok_api(grok_prompt, "chat", username)
                    response = result.get(
                        "response",
                        f"Error: {result.get('error', 'Unknown error')}",
                    )

                # Save assistant message
                result = await self.core.add_chat_message(
                    username=username,
                    session_token=self.client_state["session_token"],
                    content=response,
                    role="assistant",
                    message_type="text",
                )
                assistant_message_id = result["message_id"]

                self.messages.append({
                    "id": assistant_message_id,
                    "content": "",
                    "role": "assistant",
                    "type": "text",
                    "timestamp": current_time,
                })
                await self.update_messages()

                from app import CHAT_COMPONENTS
                CHAT_COMPONENTS[self.client_id] = self
                app.storage.client[self.client_id] = {"client_id": self.client_id}

                escaped_response = response.replace("'", "\\'").replace("\n", "\\n")
                js_code = f"""
                    (function() {{
                        const element = document.getElementById('message-{assistant_message_id}');
                        if (!element) {{
                            console.error('Element message-{assistant_message_id} not found');
                            return;
                        }}
                        let text = '{escaped_response}';
                        let index = 0;
                        element.innerHTML = '<strong>{get_text(self.language, "ai_role_label")}:</strong> ';
                        function type() {{
                            if (index < text.length) {{
                                element.innerHTML += text[index];
                                index++;
                                setTimeout(type, 20);
                            }} else {{
                                element.innerHTML += '<br><span class="text-xs text-gray-500">'
                                    + '({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current_time))})'
                                    + '</span>';
                                fetch('/typing-complete/{assistant_message_id}', {{
                                    method: 'POST',
                                    headers: {{ 'Content-Type': 'application/json' }},
                                    body: JSON.stringify({{
                                        response: '{escaped_response}',
                                        client_id: '{self.client_id}'
                                    }})
                                }}).then(response => response.json()).then(data => {{
                                    if (data.success) {{
                                        fetch('/update-ui', {{
                                            method: 'POST',
                                            headers: {{ 'Content-Type': 'application/json' }},
                                            body: JSON.stringify({{ client_id: '{self.client_id}' }})
                                        }}).catch(err => console.error('Failed to call /update-ui: ', err));
                                    }}
                                }}).catch(err => console.error('Failed to call /typing-complete: ', err));
                            }}
                        }}
                        type();
                    }})();
                """

                try:
                    await ui.run_javascript(js_code, timeout=5.0)
                    logger.info(
                        f"{username}: JavaScript sent for typing effect, "
                        f"message_id={assistant_message_id}"
                    )
                except TimeoutError as e:
                    logger.warning(
                        f"{username}: JavaScript timeout for message_id={assistant_message_id}: {str(e)}"
                    )

                async def fallback_update():
                    await asyncio.sleep(30)
                    for msg in self.messages:
                        if msg["id"] == assistant_message_id and not msg["content"]:
                            msg["content"] = response
                            await self.update_messages()
                            logger.warning(
                                f"{username}: JavaScript timeout, updated assistant message manually"
                            )
                            break

                asyncio.create_task(fallback_update())

                self.messages = self.messages[-self.message_limit:]
                self.message_input.value = ""
                await self.scroll_to_bottom()
                logger.info(f"{username}: Message sent successfully")

            except Exception as e:
                logger.error(f"{username}: Error sending message: {str(e)}", exc_info=True)
                result = await self.core.add_chat_message(
                    username=username,
                    session_token=self.client_state["session_token"],
                    content=get_text(self.language, "send_message_error"),
                    role="assistant",
                    message_type="text",
                )
                self.messages.append({
                    "id": result["message_id"],
                    "content": get_text(self.language, "send_message_error"),
                    "role": "assistant",
                    "type": "text",
                    "timestamp": int(time.time()),
                })
                ui.notify(get_text(self.language, "send_message_error"), type="negative")
                await self.update_messages()
                await self.scroll_to_bottom()

            finally:
                self.loading = False
                with self.container:
                    if (
                        hasattr(self, "progress")
                        and self.progress is not None
                        and hasattr(self.progress, "parent_slot")
                        and self.progress in self.progress.parent_slot.children
                    ):
                        self.progress.delete()
                        self.progress = None
                    else:
                        logger.debug(f"{username}: Skipped deleting progress bar (not found)")
                ui.update()
                await self.scroll_to_bottom()
    
    async def handle_upload(self, event):
        username = self.client_state.get("username", "")
        try:
            file = event.content
            filename = event.name
            content_type, _ = mimetypes.guess_type(filename)
            if not content_type:
                content_type = "application/octet-stream"

            file.seek(0, os.SEEK_END)
            size = file.tell()
            file.seek(0)

            if content_type not in Config.CHAT_FILE_ALLOWED_FORMATS:
                ui.notify(get_text(self.language, "unsupported_file_format", formats=", ".join(Config.CHAT_FILE_ALLOWED_FORMATS)), type="negative")
                return
            if size > Config.CHAT_FILE_MAX_SIZE:
                ui.notify(get_text(self.language, "file_size_exceeded", size=Config.CHAT_FILE_MAX_SIZE / (1024 * 1024)), type="negative")
                return

            file_id = str(uuid.uuid4())
            file_content = file.read()

            if content_type.startswith("image/"):
                from PIL import Image
                import io
                img = Image.open(io.BytesIO(file_content))
                img = img.resize((800, 800), Image.LANCZOS)
                output = io.BytesIO()
                img.save(output, format="JPEG", quality=85)
                file_content = output.getvalue()

            file_url = await self.core.upload_file(
                file_content, content_type, Config.CHAT_FILE_STORAGE_PATH, file_id
            )
            logger.debug(
                f"{username}: Đã upload file, file_url={file_url}, "
                f"message_type={'image' if content_type.startswith('image/') else 'file'}"
            )

            message = self.message_input.value.strip() or f"[Uploaded file: {filename}]"
            message_type = "image" if content_type.startswith("image/") else "file"

            result = await self.core.add_chat_message(
                username=username,
                session_token=self.client_state["session_token"],
                content=message,
                file_url=file_url,
                role="user",
                message_type=message_type
            )

            self.messages.append({
                "id": result["message_id"],
                "content": message,
                "role": "user",
                "type": message_type,
                "file_url": file_url,
                "timestamp": int(time.time())
            })

            if result.get("warning"):
                ui.notify(result["warning"], type="warning")
            else:
                ui.notify(get_text(self.language, "file_upload_success", filename=filename), type="positive")

            await self.update_messages()
            await self.scroll_to_bottom()
            
            if content_type.startswith("image/") and not getattr(Config, "GROK_VISION_ENABLED", False):
                warning_message = get_text(self.language, "grok_vision_not_supported")
                result = await self.core.add_chat_message(
                    username=username,
                    session_token=self.client_state["session_token"],
                    content=warning_message,
                    role="assistant",
                    message_type="text"
                )
                self.messages.append({
                    "id": result["message_id"],
                    "content": warning_message,
                    "role": "assistant",
                    "type": "text",
                    "timestamp": int(time.time())
                })
                await self.update_messages()
                await self.scroll_to_bottom()

        except Exception as e:
            logger.error(f"{username}: Lỗi upload file: {str(e)}", exc_info=True)
            ui.notify(get_text(self.language, "file_upload_error", error=str(e)), type="negative")
