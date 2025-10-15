
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
from uiapp.language import get_text
from fuzzywuzzy import fuzz

logger = get_logger("TrainingComponent")

QA_HISTORY_LIMIT = getattr(Config, "QA_HISTORY_LIMIT", 50)

@retry(stop=stop_after_attempt(3), wait=wait_fixed(0.5))
async def safe_ui_update():
    logger.debug("Trying safe_ui_update")
    ui.update()

class TrainingComponent:
    def __init__(self, core: Core, client_state: Dict, classes: str, language: str = None):
        self.core = core
        self.client_state = client_state or {}
        # Prioritize language from app.storage.user, then client_state
        self.language = app.storage.user.get("language", client_state.get("language", "vi"))
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
        logger.info(f"{self.username}: Initialized TrainingComponent with language={self.language}")
        
        # Initialize FormComponent with current language
        self.qa_form = self._create_form_component()
        self.search_input = None
        self.json_input = None
        asyncio.create_task(self.enable_wal_mode())

    def _create_form_component(self) -> FormComponent:
        """Create or recreate FormComponent with current language."""
        return FormComponent(
            fields={
                "id": {"label": get_text(self.language, "id_label", "ID"), "type": "text", "props": "type=hidden", "value": ""},
                "question": {
                    "label": get_text(self.language, "question_label", "Question"),
                    "type": "text",
                    "validation": {"required": f"lambda x: bool(x) or '{get_text(self.language, 'required_field', 'Required')}'"},
                    "placeholder": get_text(self.language, "question_placeholder", "Enter question...")
                },
                "answer": {
                    "label": get_text(self.language, "answer_label", "Answer"),
                    "type": "textarea",
                    "validation": {"required": f"lambda x: bool(x) or '{get_text(self.language, 'required_field', 'Required')}'"},
                    "placeholder": get_text(self.language, "answer_placeholder", "Enter answer...")
                },
                "category": {
                    "label": get_text(self.language, "category_label", "Category"),
                    "type": "select",
                    "options": [
                        get_text(self.language, "category_chat", "chat"),
                        get_text(self.language, "category_support", "support"),
                        get_text(self.language, "category_other", "other")
                    ],
                    "value": get_text(self.language, "category_chat", "chat"),
                    "validation": {"required": f"lambda x: bool(x) or '{get_text(self.language, 'required_field', 'Required')}'"}
                }
            },
            on_submit=self.handle_qa_submit,
            submit_label=get_text(self.language, "save_qa_button", "Save Q&A"),
            core=self.core,
            client_state=self.client_state,
            language=self.language
        )

    async def render(self):
        async with self.processing_lock:
            if self.rendered and self.client_id == context.client.id:
                logger.info(f"{self.username}: UI already rendered, updating instead")
                await self.update()
                return

            self.client_id = context.client.id
            if self.container:
                self.container.clear()
                self.container.delete()
            self.container = ui.card().classes(self.classes)
            with self.container:
                ui.label(get_text(self.language, "manage_qa_label", "Manage Q&A")).classes("text-lg font-semibold mb-4")
                
                self.search_input = ui.input(
                    label=get_text(self.language, "search_qa_label", "Search Q&A"),
                    placeholder=get_text(self.language, "search_qa_placeholder", "Enter keyword to search in questions or answers...")
                ).classes("mb-4 w-full")
                self.search_input.on("change", self.handle_search)
                
                await self.qa_form.render()
                
                self.qa_list_container = ui.element("div").classes("w-full")
                await self.update_qa_records()
                
                self.json_input = ui.textarea(
                    label=get_text(self.language, "json_qa_label", "Import JSON Q&A"),
                    placeholder='[{"question": "' + get_text(self.language, "question_label", "Question") + '", "answer": "' + get_text(self.language, "answer_label", "Answer") + '", "category": "' + get_text(self.language, "category_chat", "chat") + '"}]'
                ).classes("mb-4 w-full")
                ui.button(get_text(self.language, "import_json_button", "Import Q&A from JSON"), on_click=self.handle_json_submit).classes("bg-blue-600 text-white hover:bg-blue-700 mb-4 w-full")
                
                ui.upload(on_upload=self.handle_file_upload).props("accept=.json,.csv").classes("mb-4 w-full")
                
                with ui.row().classes("w-full"):
                    ui.button(get_text(self.language, "export_qa_button", "Export Q&A to JSON"), on_click=self.handle_export_qa).classes("bg-green-600 text-white hover:bg-green-700 mr-2 w-full")
                    ui.button(get_text(self.language, "delete_all_qa_button", "Delete All Q&A"), on_click=self.on_reset).classes("bg-red-600 text-white hover:bg-red-700 w-full")

            self.rendered = True
            # Save language to app.storage.user
            app.storage.user["language"] = self.language
            await self.save_state_and_config()
            logger.info(f"{self.username}: Rendered TrainingComponent UI with language={self.language}")

    async def update(self):
        try:
            self.client_id = getattr(context.client, 'id', None)
            logger.debug(f"{self.username}: Starting update TrainingComponent, client_id={self.client_id}, current context.client.id={context.client.id}")
            
            # Check for language change
            new_language = app.storage.user.get("language", self.client_state.get("language", "vi"))
            if new_language != self.language:
                logger.info(f"{self.username}: Language changed from {self.language} to {new_language}")
                self.language = new_language
                self.qa_form = self._create_form_component()  # Recreate form with new language
                await self.render()  # Re-render entire UI with new language
                if context.client.has_socket_connection:
                    ui.notify(get_text(self.language, "language_updated", "Language updated to {lang}", lang=self.language), type="positive")
                return True

            if not self.rendered:
                logger.warning(f"{self.username}: UI not rendered, attempting to render")
                await self.render()
                return True

            if self.search_input:
                self.search_input.value = ""
                logger.debug(f"{self.username}: Reset search_input to refresh all Q&A")
            else:
                logger.warning(f"{self.username}: search_input missing, creating new")
                self.search_input = ui.input(
                    label=get_text(self.language, "search_qa_label", "Search Q&A"),
                    placeholder=get_text(self.language, "search_qa_placeholder", "Enter keyword to search in questions or answers...")
                ).classes("mb-4 w-full")
                self.search_input.on("change", self.handle_search)

            await self.update_qa_records()
            await safe_ui_update()
            logger.info(f"{self.username}: Updated TrainingComponent UI successfully")
            
            if context.client.has_socket_connection:
                ui.notify(get_text(self.language, "updated_qa_data", "Updated Q&A data"), type="positive")
            return True
        except Exception as e:
            logger.error(f"{self.username}: Error updating UI: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(get_text(self.language, "update_training_error", "Error updating training UI"), type="negative")
            return False

    
    
    async def fetch_qa_data(
        self,
        progress_callback: Optional[Callable[[float], None]] = None,
        page: int = 1,
        page_size: int = 10
    ) -> Tuple[List[Dict], int]:
        try:
            async with aiosqlite.connect(Config.SQLITE_DB_PATH) as conn:
                search_value = self.search_input.value.strip() if self.search_input else ""
                logger.debug(
                    f"{self.username}: fetch_qa_data with search_value='{search_value}', "
                    f"page={page}, page_size={page_size}"
                )

                if search_value:
                    clean_search = search_value.rstrip('?.!;,').strip()
                    logger.debug(
                        f"{self.username}: Original search '{search_value}', cleaned '{clean_search}'"
                    )

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

                        if total_matches > 0:
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

                            logger.info(
                                f"{self.username}: FTS search '{search_value}' (cleaned '{clean_search}'): "
                                f"{total_matches} matches, page {page} ({len(data)} items)"
                            )

                            if total_matches > 1000:
                                logger.warning(
                                    f"{self.username}: Many FTS results ({total_matches}), "
                                    f"showing top {len(data)}"
                                )
                                if context.client.has_socket_connection:
                                    ui.notify(
                                        get_text(
                                            self.language,
                                            "too_many_results",
                                            "Found many results ({count}), showing page {page}",
                                            count=total_matches,
                                            page=page
                                        ),
                                        type="info"
                                    )

                            if not data:
                                if context.client.has_socket_connection:
                                    ui.notify(
                                        get_text(
                                            self.language,
                                            "no_qa_found",
                                            "No matching Q&A found"
                                        ),
                                        type="info"
                                    )

                            return data, total_matches

                        else:
                            logger.info(
                                f"{self.username}: No exact FTS results for '{search_value}', "
                                f"falling back to fuzzy matching"
                            )

                    except Exception as fts_error:
                        logger.warning(
                            f"{self.username}: FTS error (not no-results): {str(fts_error)}. "
                            "Falling back to fuzzy in-memory (limit 1000 records)"
                        )
                        if context.client.has_socket_connection:
                            ui.notify(
                                get_text(
                                    self.language,
                                    "fts_not_ready",
                                    "Using fuzzy search (FTS error)"
                                ),
                                type="warning"
                            )

                    # --- Fallback fuzzy search ---
                    async with conn.execute(
                        """
                        SELECT id, question, answer, category, created_by, created_at, timestamp
                        FROM qa_data
                        WHERE created_by = ?
                        ORDER BY timestamp DESC
                        LIMIT 1000
                        """,
                        (self.username,)
                    ) as cursor:
                        all_rows = await cursor.fetchall()

                    if not all_rows:
                        logger.info(f"{self.username}: No Q&A data found")
                        return [], 0

                    threshold = getattr(Config, "TRAINING_SEARCH_THRESHOLD", 0.6)
                    search_lower = search_value.strip().lower()

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
                            matches.append({
                                "id": row[0],
                                "question": row[1],
                                "answer": row[2],
                                "category": row[3],
                                "created_by": row[4],
                                "created_at": row[5],
                                "timestamp": row[6],
                                "score": round(max_ratio, 1)
                            })

                    matches.sort(key=lambda x: (x["score"], x["timestamp"]), reverse=True)
                    total_matches = len(matches)
                    start_idx = (page - 1) * page_size
                    end_idx = start_idx + page_size
                    data = matches[start_idx:end_idx]

                    logger.info(
                        f"{self.username}: Fallback fuzzy search '{search_value}' "
                        f"(in 1000 records): {total_matches} matches, "
                        f"page {page} ({len(data)} items)"
                    )

                    if len(all_rows) == 1000:
                        logger.warning(
                            f"{self.username}: Fallback searched only in 1000 most recent records"
                        )

                    return data, total_matches

                else:
                    # --- Non-search case ---
                    query_count = "SELECT COUNT(*) FROM qa_data WHERE created_by = ?"
                    cursor_count = await conn.execute(query_count, (self.username,))
                    total_matches = (await cursor_count.fetchone())[0]
                    logger.debug(f"{self.username}: Non-search count: {total_matches}")

                    query = """
                        SELECT id, question, answer, category, created_by, created_at, timestamp
                        FROM qa_data
                        WHERE created_by = ?
                        ORDER BY timestamp DESC
                        LIMIT ? OFFSET ?
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

                    logger.info(
                        f"{self.username}: Loaded {len(data)} Q&A records without search, "
                        f"total: {total_matches}"
                    )
                    return data, total_matches

                if progress_callback:
                    await progress_callback(1.0)

        except Exception as e:
            logger.error(
                f"{self.username}: Error fetching Q&A data: {str(e)}",
                exc_info=True
            )
            if context.client.has_socket_connection:
                ui.notify(
                    get_text(
                        self.language,
                        "fetch_qa_error",
                        "Error fetching Q&A data: {error}",
                        error=str(e)
                    ),
                    type="negative"
                )
            return [], 0
            
    async def update_qa_records(self):
        try:
            data, total_count = await self.fetch_qa_data(page=1, page_size=QA_HISTORY_LIMIT)
            logger.debug(f"{self.username}: Before updating, Q&A records: {len(data)}, total: {total_count}")
            
            if not self.container or not hasattr(self.container, 'parent_slot'):
                logger.warning(f"{self.username}: container missing or invalid, creating new")
                self.container = ui.card().classes(self.classes)
            
            if not self.qa_list_container or not hasattr(self.qa_list_container, 'parent_slot'):
                logger.warning(f"{self.username}: qa_list_container missing or invalid, creating new")
                self.qa_list_container = ui.element("div").classes("w-full")
                with self.container:
                    self.qa_list_container.move(target=self.container)
                logger.debug(f"{self.username}: Created and attached qa_list_container to container")
            
            self.qa_list_container.clear()
            with self.qa_list_container:
                if not data:
                    ui.label(get_text(self.language, "no_qa_data_label", "⚠️ No Q&A data available")).classes("text-gray-500 italic")
                else:
                    for row in data:
                        with ui.card().classes("w-full mb-2 p-4"):
                            ui.label(f"{get_text(self.language, 'question_label', 'Question')}: {row['question']}").classes("font-bold")
                            ui.label(f"{get_text(self.language, 'answer_label', 'Answer')}: {row['answer']}")
                            ui.label(f"{get_text(self.language, 'category_label', 'Category')}: {row['category']}")
                            ui.label(f"{get_text(self.language, 'created_by_label', 'Created by')}: {row['created_by']}")
                            ui.label(f"{get_text(self.language, 'created_at_label', 'Created at')}: {row['created_at']}")
                            with ui.row():
                                ui.button(get_text(self.language, "edit_button", "Edit"), on_click=lambda r=row: self.handle_edit(r)).classes("bg-blue-600 text-white hover:bg-blue-700 mr-2")
                                ui.button(get_text(self.language, "delete_button", "Delete"), on_click=lambda r=row: self.delete_row(r)).classes("bg-red-600 text-white hover:bg-red-700")
            
            await safe_ui_update()
            logger.info(f"{self.username}: Loaded {len(data)} Q&A records from DB, total: {total_count}")
            logger.debug(f"{self.username}: qa_list_container parent: {getattr(self.qa_list_container, 'parent_slot', 'None')}")
            
            if context.client.has_socket_connection:
                ui.notify(get_text(self.language, "loaded_qa_records", "Loaded {count} Q&A records", count=len(data)), type="positive")
        except Exception as e:
            logger.error(f"{self.username}: Error loading Q&A: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(get_text(self.language, "load_qa_error", "Error loading Q&A: {error}", error=str(e)), type="negative")

    async def enable_wal_mode(self):
        try:
            async with aiosqlite.connect(Config.SQLITE_DB_PATH) as conn:
                await conn.execute("PRAGMA journal_mode=WAL")
                await conn.commit()
                logger.info(f"{self.username}: Enabled WAL mode for SQLite")
        except Exception as e:
            logger.error(f"{self.username}: Error enabling WAL: {str(e)}", exc_info=True)

    async def refresh_session_token(self):
        try:
            if context.client.has_socket_connection:
                new_token = await self.core.refresh_session_token(self.username)
                if new_token and isinstance(new_token, str):
                    async with self.state_lock:
                        self.client_state["session_token"] = new_token
                    logger.info(f"{self.username}: Refreshed session_token")
                    return True
                else:
                    logger.warning(f"{self.username}: Failed to refresh session_token")
                    return False
            else:
                logger.warning(f"{self.username}: Skipped session_token refresh due to disconnected client")
                return False
        except Exception as e:
            logger.error(f"{self.username}: Error refreshing session_token: {str(e)}", exc_info=True)
            return False

    async def cleanup_client_storage(self):
        try:
            if self.container and context.client.has_socket_connection:
                self.container.clear()
                logger.info(f"{self.username}: Cleared container")
            else:
                logger.warning(f"{self.username}: Skipped cleanup due to disconnected client or missing container")
        except Exception as e:
            logger.warning(f"{self.username}: Skipped cleanup due to disconnected client: {str(e)}")

    def _handle_result_error(self, result, action: str) -> bool:
        if isinstance(result, str):
            logger.error(f"{self.username}: Error {action}: {result}")
            if context.client.has_socket_connection:
                ui.notify(get_text(self.language, "generic_error", "Error {action}: {error}", action=action, error=result), type="negative")
            return True
        if isinstance(result, dict) and "error" in result:
            logger.error(f"{self.username}: Error {action}: {result['error']}")
            if context.client.has_socket_connection:
                ui.notify(get_text(self.language, "generic_error", "Error {action}: {error}", action=action, error=result['error']), type="negative")
            return True
        return False

    async def save_state_and_config(self) -> bool:
        async with self.state_lock:
            try:
                if not self.client_state.get("session_token"):
                    if not await self.refresh_session_token():
                        if context.client.has_socket_connection:
                            ui.notify(get_text(self.language, "invalid_session", "Error: Failed to refresh session_token"), type="negative")
                        return False
                check_disk_space()
                self.client_state["timestamp"] = int(time.time())
                self.client_state["language"] = self.language
                self.client_state.pop("qa_records", None)
                state_json = json.dumps(self.client_state, ensure_ascii=False)
                if len(state_json.encode()) > Config.MAX_UPLOAD_SIZE:
                    logger.error(f"{self.username}: State too large")
                    if context.client.has_socket_connection:
                        ui.notify(get_text(self.language, "state_too_large", "Error: Session state too large"), type="negative")
                    return False
                await self.core.save_client_state(self.client_state["session_token"], self.client_state)
                app.storage.user["language"] = self.language
                state_id = hashlib.sha256(
                    f"{self.username}_{self.client_state['session_token']}".encode()
                ).hexdigest()
                async with self.log_lock:
                    await self.core.log_sync_action(
                        table_name="client_states",
                        record_id=state_id,
                        action="SAVE_TRAINING_STATE",
                        details={"username": self.username, "action": "save_training_state", "language": self.language},
                        username=self.username
                    )
                logger.info(f"{self.username}: Saved state successfully with language={self.language}")
                return True
            except Exception as e:
                logger.error(f"{self.username}: Error saving state: {str(e)}", exc_info=True)
                if context.client.has_socket_connection:
                    ui.notify(get_text(self.language, "save_state_error", "Error saving state"), type="negative")
                return False

    
    async def delete_row(self, row_data):
        async with self.db_lock:
            try:
                record_id = row_data["id"]
                result = await self.core.delete_record("qa_data", record_id, self.username)
                if self._handle_result_error(
                    result,
                    get_text(self.language, "delete_qa_action", "delete Q&A")
                ):
                    return

                # Ghi log DELETE và đặt last_sync = 0
                current_time = int(time.time())
                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=60.0) as conn:
                    # Ghi log DELETE vào sync_log
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

                    # Đặt last_sync = 0 cho qa_data
                    await conn.execute(
                        """
                        DELETE FROM sync_log
                        WHERE table_name = ?
                        AND action IN ('sync_to_firestore', 'sync_to_sqlite')
                        """,
                        ("qa_data",)
                    )
                    await conn.commit()
                    logger.debug(
                        f"{self.username}: Đã xóa sync_log cho qa_data "
                        f"với action='sync_to_firestore' và 'sync_to_sqlite' để đặt last_sync=0"
                    )

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
                    ui.notify(
                        get_text(
                            self.language,
                            "deleted_qa",
                            (
                                "Deleted Q&A {id}. Data still exists in Firestore, "
                                "recoverable via sync_to_sqlite."
                            ),
                            id=record_id
                        ),
                        type="positive"
                    )
                    logger.info(
                        f"{self.username}: Deleted Q&A {record_id} and logged DELETE. "
                        f"Data still exists in Firestore, recoverable via sync_to_sqlite."
                    )

            except Exception as e:
                logger.error(f"{self.username}: Error deleting Q&A: {str(e)}", exc_info=True)
                if context.client.has_socket_connection:
                    ui.notify(
                        get_text(
                            self.language,
                            "delete_qa_error",
                            "Error deleting Q&A: {error}",
                            error=str(e)
                        ),
                        type="negative"
                    )
                    
    async def _get_search_record_ids(self, search_query: str) -> List[str]:
        if not search_query:
            async with aiosqlite.connect(Config.SQLITE_DB_PATH) as conn:
                async with conn.execute("SELECT id FROM qa_data WHERE created_by = ?", (self.username,)) as cursor:
                    return [row[0] for row in await cursor.fetchall()]
        
        clean_search = search_query.rstrip('?.!;,').strip()
        try:
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
            logger.warning(f"{self.username}: FTS error in delete: {str(fts_error)}. Falling back to LIKE")
            async with aiosqlite.connect(Config.SQLITE_DB_PATH) as conn:
                async with conn.execute(
                    "SELECT id FROM qa_data WHERE (question LIKE ? OR answer LIKE ?) AND created_by = ?",
                    (f"%{clean_search}%", f"%{clean_search}%", self.username)
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
                            ui.notify(
                                get_text(
                                    self.language,
                                    "invalid_session",
                                    "Error: Invalid session",
                                ),
                                type="negative",
                            )
                        logger.error(f"{self.username}: Invalid session")
                        return

                search_query = (getattr(self.search_input, "value", "") or "").strip()
                record_ids = await self._get_search_record_ids(search_query)

                if not record_ids:
                    if context.client.has_socket_connection:
                        ui.notify(
                            get_text(
                                self.language,
                                "no_qa_to_delete",
                                "No Q&A to delete",
                            ),
                            type="info",
                        )
                    logger.info(f"{self.username}: No Q&A to delete")
                    return

                logger.debug(f"{self.username}: Record IDs to delete: {record_ids}")

                if context.client.has_socket_connection:
                    with ui.dialog() as dialog, ui.card():
                        ui.label(
                            get_text(
                                self.language,
                                "confirm_delete_qa",
                                "Are you sure you want to delete {count} Q&A{search}?",
                                count=len(record_ids),
                                search=" matching search" if search_query else "",
                            )
                        )
                        with ui.row():
                            ui.button(
                                get_text(self.language, "ok_button", "OK"),
                                on_click=lambda: dialog.submit(True),
                            )
                            ui.button(
                                get_text(self.language, "cancel_button", "Cancel"),
                                on_click=lambda: dialog.submit(False),
                            )

                    confirm = await dialog
                    logger.debug(f"{self.username}: Confirm value from dialog: {confirm}")

                    if not confirm:
                        ui.notify(
                            get_text(
                                self.language,
                                "cancel_delete",
                                "Delete cancelled",
                            ),
                            type="info",
                        )
                        logger.info(f"{self.username}: Delete Q&A cancelled")
                        return

                current_time = int(time.time())
                async with aiosqlite.connect(Config.SQLITE_DB_PATH) as conn:
                    is_full_reset = not search_query

                    # Lấy record_ids của username để ghi log DELETE
                    if is_full_reset:
                        async with conn.execute(
                            "SELECT id FROM qa_data WHERE created_by = ?",
                            (self.username,),
                        ) as cursor:
                            record_ids = [row[0] for row in await cursor.fetchall()]

                    # Ghi log DELETE cho từng bản ghi
                    for record_id in record_ids:
                        await conn.execute(
                            """
                            INSERT INTO sync_log (
                                id, table_name, record_id, action, timestamp, details
                            )
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
                                    ensure_ascii=False,
                                ),
                            ),
                        )

                    if is_full_reset:
                        # Xóa toàn bộ bảng qa_data
                        await conn.execute("DELETE FROM qa_data")
                        logger.debug(f"{self.username}: Đã xóa toàn bộ bảng qa_data")
                    else:
                        # Xóa các bản ghi trong qa_data theo record_ids
                        placeholders = ",".join("?" for _ in record_ids)
                        await conn.execute(
                            f"""
                            DELETE FROM qa_data
                            WHERE id IN ({placeholders})
                            AND created_by = ?
                            """,
                            (*record_ids, self.username),
                        )
                        logger.debug(
                            f"{self.username}: Đã xóa {len(record_ids)} bản ghi qa_data"
                        )

                    # Xóa sync_log đồng bộ của qa_data
                    await conn.execute(
                        """
                        DELETE FROM sync_log
                        WHERE table_name = ?
                        AND action IN ('sync_to_firestore', 'sync_to_sqlite')
                        """,
                        ("qa_data",),
                    )
                    await conn.commit()
                    logger.debug(f"{self.username}: Đã xóa sync_log đồng bộ cho qa_data")

                await self.update_qa_records()

                async with self.log_lock:
                    await self.core.log_sync_action(
                        table_name="qa_data",
                        record_id=f"batch_delete_{len(record_ids)}_records",
                        action="DELETE",
                        details={
                            "username": self.username,
                            "action": (
                                "delete_all_qa" if is_full_reset else "delete_search_results"
                            ),
                            "count": len(record_ids),
                            "search_query": search_query if search_query else None,
                        },
                        username=self.username,
                    )

                if context.client.has_socket_connection:
                    message = get_text(
                        self.language,
                        "deleted_qa_batch",
                        (
                            "Deleted all {count} Q&A{search} in SQLite. "
                            "To delete permanently, sync to Firestore. "
                            "To recover, sync from Firestore."
                        ),
                        count=len(record_ids),
                        search=" matching search" if search_query else "",
                    )
                    ui.notify(message, type="positive")

                logger.info(
                    f"{self.username}: Deleted {len(record_ids)} Q&A "
                    f"{'matching search' if search_query else 'all'} and logged DELETE. "
                    f"Data still exists in Firestore, recoverable via sync_to_sqlite or can be deleted via sync_to_firestore."
                )

            except Exception as e:
                logger.error(
                    f"{self.username}: Error deleting Q&A: {str(e)}",
                    exc_info=True,
                )
                if context.client.has_socket_connection:
                    ui.notify(
                        get_text(
                            self.language,
                            "delete_qa_error",
                            "Error deleting Q&A: {error}",
                            error=str(e),
                        ),
                        type="negative",
                    )
    
    async def handle_export_qa(self):
        try:
            all_data = []
            page = 1
            while True:
                result = await self.core.read_records("qa_data", self.username, page=page, page_size=500)
                if self._handle_result_error(result, get_text(self.language, "export_qa_action", "export Q&A")):
                    return
                data = result.get("results", [])
                if not isinstance(data, list):
                    logger.error(f"{self.username}: Result from read_records is not a list: {data}")
                    if context.client.has_socket_connection:
                        ui.notify(get_text(self.language, "invalid_qa_data", "Error: Invalid Q&A data"), type="negative")
                    return
                if not data:
                    break
                all_data.extend(data)
                if len(data) < 500:
                    break
                page += 1
            if not all_data:
                if context.client.has_socket_connection:
                    ui.notify(get_text(self.language, "no_qa_to_export", "No Q&A data to export"), type="warning")
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
                ui.notify(get_text(self.language, "exported_qa", "Exported {count} Q&A", count=len(all_data)), type="positive")
        except Exception as e:
            logger.error(f"{self.username}: Error exporting Q&A: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(get_text(self.language, "export_qa_error", "Error exporting Q&A: {error}", error=str(e)), type="negative")

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
                    ui.label(get_text(self.language, "no_qa_found", "⚠️ No matching Q&A found")).classes("text-gray-500 italic")
                else:
                    for row in data:
                        with ui.card().classes("w-full mb-2 p-4"):
                            ui.label(f"{get_text(self.language, 'question_label', 'Question')}: {row['question']}").classes("font-bold")
                            ui.label(f"{get_text(self.language, 'answer_label', 'Answer')}: {row['answer']}")
                            ui.label(f"{get_text(self.language, 'category_label', 'Category')}: {row['category']}")
                            ui.label(f"{get_text(self.language, 'created_by_label', 'Created by')}: {row['created_by']}")
                            ui.label(f"{get_text(self.language, 'created_at_label', 'Created at')}: {row['created_at']}")
                            with ui.row():
                                ui.button(get_text(self.language, "edit_button", "Edit"), on_click=lambda r=row: self.handle_edit(r)).classes("bg-blue-600 text-white hover:bg-blue-700 mr-2")
                                ui.button(get_text(self.language, "delete_button", "Delete"), on_click=lambda r=row: self.delete_row(r)).classes("bg-red-600 text-white hover:bg-red-700")
            if context.client.has_socket_connection:
                ui.update()
                ui.notify(get_text(self.language, "search_results", "Found {count} results", count=len(data)), type="positive")
        except Exception as e:
            logger.error(f"{self.username}: Error searching Q&A: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(get_text(self.language, "search_qa_error", "Error searching Q&A: {error}", error=str(e)), type="negative")

    async def handle_json_submit(self):
        async with self.db_lock:
            try:
                qa_list = json.loads(self.json_input.value)
                if not isinstance(qa_list, list):
                    logger.error(f"{self.username}: JSON is not a list")
                    if context.client.has_socket_connection:
                        ui.notify(get_text(self.language, "invalid_json_list", "JSON must be a list"), type="negative")
                    return
                valid_qa_list = await self.process_qa_list(qa_list, "JSON input")
                if not valid_qa_list:
                    logger.error(f"{self.username}: No valid Q&A records")
                    if context.client.has_socket_connection:
                        ui.notify(get_text(self.language, "no_valid_qa", "No valid Q&A records"), type="negative")
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
                if self._handle_result_error(result, get_text(self.language, "import_json_action", "import Q&A from JSON")):
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
                    ui.notify(get_text(self.language, "imported_qa", "Imported {count} Q&A", count=len(valid_qa_list)), type="positive")
            except json.JSONDecodeError as e:
                logger.error(f"{self.username}: Invalid JSON: {str(e)}")
                if context.client.has_socket_connection:
                    ui.notify(get_text(self.language, "invalid_json", "Invalid JSON: {error}", error=str(e)), type="negative")
            except Exception as e:
                logger.error(f"{self.username}: Error importing JSON Q&A: {str(e)}", exc_info=True)
                if context.client.has_socket_connection:
                    ui.notify(get_text(self.language, "import_json_error", "Error importing JSON Q&A: {error}", error=str(e)), type="negative")

    async def handle_file_upload(self, e):
        async with self.db_lock:
            try:
                content = e.content.read()
                if len(content) > Config.MAX_UPLOAD_SIZE:
                    if context.client.has_socket_connection:
                        ui.notify(get_text(self.language, "file_too_large", "File too large"), type="negative")
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
                        ui.notify(get_text(self.language, "unsupported_file_format", "Only JSON or CSV accepted"), type="negative")
                    return
                valid_qa_list = await self.process_qa_list(qa_list, f"file {e.name}")
                if not valid_qa_list:
                    if context.client.has_socket_connection:
                        ui.notify(get_text(self.language, "no_valid_qa", "No valid Q&A records"), type="negative")
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
                if self._handle_result_error(result, get_text(self.language, "import_file_action", "import Q&A from file")):
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
                    ui.notify(get_text(self.language, "imported_qa_file", "Imported {count} Q&A from file", count=len(valid_qa_list)), type="positive")
            except Exception as e:
                logger.error(f"{self.username}: Error importing Q&A from file: {str(e)}", exc_info=True)
                if context.client.has_socket_connection:
                    ui.notify(get_text(self.language, "import_file_error", "Error importing Q&A from file: {error}", error=str(e)), type="negative")

    async def process_qa_list(self, qa_list: List[Dict], source: str) -> List[Dict]:
        valid_qa_list = []
        current_time = int(time.time())
        async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=60.0) as conn:
            for qa in qa_list:
                if "question" not in qa or "answer" not in qa:
                    logger.error(f"{self.username}: Q&A missing question or answer from {source}")
                    continue
                qa["category"] = sanitize_field_name(qa.get("category", get_text(self.language, "category_chat", "chat")))
                if "created_by" not in qa:
                    qa["created_by"] = self.username
                try:
                    if "created_at" not in qa or not qa["created_at"]:
                        qa["created_at"] = current_time
                    else:
                        qa["created_at"] = int(float(qa["created_at"]))
                except (ValueError, TypeError):
                    logger.warning(f"{self.username}: Invalid created_at '{qa.get('created_at', 'None')}' from {source}, using default {current_time}")
                    qa["created_at"] = current_time
                
                try:
                    if "timestamp" not in qa or not qa["timestamp"]:
                        qa["timestamp"] = current_time
                    else:
                        qa["timestamp"] = int(float(qa["timestamp"]))
                except (ValueError, TypeError):
                    logger.warning(f"{self.username}: Invalid timestamp '{qa.get('timestamp', 'None')}' from {source}, using default {current_time}")
                    qa["timestamp"] = current_time
                
                if len(json.dumps(qa).encode()) > Config.MAX_UPLOAD_SIZE:
                    logger.error(f"{self.username}: Q&A {qa['question']} exceeds {Config.MAX_UPLOAD_SIZE} bytes from {source}")
                    continue
                
                if "id" not in qa or not qa["id"]:
                    qa["id"] = str(uuid.uuid4())
                
                try:
                    async with conn.execute(
                        'SELECT id FROM "qa_data" WHERE question = ? AND answer = ? AND created_by = ?',
                        (qa["question"], qa["answer"], self.username)
                    ) as cursor:
                        if await cursor.fetchone():
                            logger.warning(f"{self.username}: Skipping duplicate Q&A '{qa['question'][:50]}...' from {source}")
                            continue
                except Exception as dup_error:
                    logger.warning(f"{self.username}: Error checking duplicate for '{qa['question'][:50]}...': {str(dup_error)}, proceeding anyway")
                
                valid_qa_list.append(qa)
        return valid_qa_list

    async def handle_edit(self, row: Dict):
        try:
            if not self.client_state.get("session_token"):
                if not await self.refresh_session_token():
                    if context.client.has_socket_connection:
                        ui.notify(get_text(self.language, "invalid_session", "Error: Failed to refresh session_token"), type="negative")
                    return
            session_token = self.client_state.get("session_token", "")
            for field_name in ["id", "question", "answer", "category"]:
                storage_key = f"{session_token}_{field_name}" if session_token else field_name
                app.storage.user[storage_key] = row[field_name]
            if self.qa_form:
                await self.qa_form.set_data(row)
                self.client_state["is_editing"] = True
            if context.client.has_socket_connection:
                ui.notify(get_text(self.language, "edit_qa_filled", "Filled data for editing Q&A"), type="positive")
        except Exception as e:
            logger.error(f"{self.username}: Error filling edit Q&A data: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(get_text(self.language, "edit_qa_error", "Error filling edit Q&A data: {error}", error=str(e)), type="negative")

    async def cancel_edit(self):
        try:
            if self.qa_form:
                await self.qa_form.reset()
            self.client_state["is_editing"] = False
            if context.client.has_socket_connection:
                ui.notify(get_text(self.language, "cancel_edit_qa", "Cancelled Q&A edit"), type="positive")
            await self.update_qa_records()
        except Exception as e:
            logger.error(f"{self.username}: Error cancelling Q&A edit: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(get_text(self.language, "cancel_edit_error", "Error cancelling Q&A edit: {error}", error=str(e)), type="negative")

    async def handle_qa_submit(self, data: Dict):
        async with self.db_lock:
            try:
                data["category"] = sanitize_field_name(data.get("category", get_text(self.language, "category_chat", "chat")))
                data["created_by"] = self.username
                data["created_at"] = int(time.time())
                data["timestamp"] = int(time.time())

                if "id" not in data or not data["id"]:
                    data["id"] = str(uuid.uuid4())

                if len(json.dumps(data).encode()) > Config.MAX_UPLOAD_SIZE:
                    if context.client.has_socket_connection:
                        ui.notify(get_text(self.language, "qa_too_large", "Q&A data too large"), type="negative")
                    return {"success": False, "error": get_text(self.language, "qa_too_large", "Q&A data too large")}

                async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=60.0) as conn:
                    async with conn.execute(
                        'SELECT id FROM "qa_data" WHERE question = ? AND answer = ? AND created_by = ?',
                        (data["question"], data["answer"], self.username)
                    ) as cursor:
                        existing = await cursor.fetchone()
                        if existing:
                            logger.warning(f"{self.username}: Duplicate Q&A detected (existing ID: {existing[0]}), skipping insert")
                            if context.client.has_socket_connection:
                                ui.notify(get_text(self.language, "duplicate_qa", "Q&A already exists, skipped to avoid duplicate"), type="warning")
                            return {"success": False, "error": get_text(self.language, "duplicate_qa", "Q&A already exists")}

                record_id = data["id"]
                is_update = False
                logger.debug(f"{self.username}: record_id before processing: {record_id}")
                if record_id:
                    async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=60.0) as conn:
                        async with conn.execute(
                            'SELECT id FROM "qa_data" WHERE id = ? AND created_by = ?',
                            (record_id, self.username)
                        ) as cursor:
                            if await cursor.fetchone():
                                result = await self.core.update_record("qa_data", record_id, data, self.username)
                                is_update = True
                                action = "UPDATE_QA"
                            else:
                                logger.warning(f"{self.username}: ID {record_id} does not exist or not owned by user, falling back to create")
                                data["id"] = str(uuid.uuid4())
                                result = await self.core.create_record("qa_data", data, self.username)
                                action = "CREATE_QA"
                else:
                    result = await self.core.create_record("qa_data", data, self.username)
                    action = "CREATE_QA"

                if isinstance(result, dict) and "error" in result:
                    self._handle_result_error(result, get_text(self.language, "save_qa_action", "save Q&A"))
                    return {"success": False, "error": result["error"]}

                final_id = result.get("id", str(uuid.uuid4())) if isinstance(result, dict) else str(uuid.uuid4())
                async with self.log_lock:
                    await self.core.log_sync_action(
                        table_name="qa_data",
                        record_id=final_id,
                        action=action,
                        details={"username": self.username, "action": action.lower(), "is_update": is_update},
                        username=self.username
                    )

                if self.qa_form:
                    await self.qa_form.reset()
                self.client_state["is_editing"] = False

                await self.update_qa_records()
                if context.client.has_socket_connection:
                    ui.notify(get_text(self.language, "saved_qa", "Saved Q&A successfully{update}", update=" (updated)" if is_update else " (new)"), type="positive")
                return {"success": True, "message": get_text(self.language, "saved_qa", "Saved Q&A")}
            except Exception as e:
                logger.error(f"{self.username}: Error saving Q&A: {str(e)}", exc_info=True)
                if context.client.has_socket_connection:
                    ui.notify(get_text(self.language, "save_qa_error", "Error saving Q&A: {error}", error=str(e)), type="negative")
                return {"success": False, "error": str(e)}


