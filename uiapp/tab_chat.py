import asyncio
import hashlib
import time
import traceback
import uuid
from typing import Callable, Dict, Tuple

import aiosqlite
from groq import AsyncGroq
from nicegui import ui, context, app
from tenacity import retry, stop_after_attempt, wait_fixed

from config import Config
from core import Core
from uiapp.components.chat import ChatComponent
from utils.logging import get_logger
from utils.core_common import validate_name, check_disk_space

logger = get_logger("TabChat")

CHAT_HISTORY_LIMIT = getattr(Config, "CHAT_HISTORY_LIMIT", 50)
messages_lock = asyncio.Lock()

async def create_tab(core: Core) -> Tuple[Callable, Callable]:
    groq_client = AsyncGroq(api_key=Config.GROQ_API_KEY)

    async def scroll_to_bottom(client_state: Dict = None):
        _username = client_state.get("username", "unknown") if client_state else "unknown"
        try:
            client_id = context.client.id
            client_storage = app.storage.client.get(client_id, {})
            chat_component = client_storage.get("chat_component")
            if chat_component and getattr(chat_component, "messages_container", None):
                chat_component.messages_container.scroll_to(percent=1.0)
                logger.debug(f"{_username}: Cuộn xuống cuối thành công, client_id={client_id}")
                return
            logger.warning(f"{_username}: messages_container chưa sẵn sàng để scroll, client_id={client_id}")
            ui.notify("Không thể cuộn xuống cuối, vui lòng thử lại!", type="negative")
        except Exception as e:
            logger.warning(f"{_username}: Scroll failed: {e}, client_id={client_id}")

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(0.5))
    async def render_func(core: Core, username: str, is_admin: bool, client_state: Dict):
        client_id = context.client.id
        logger.debug(f"{username}: Bắt đầu render_func cho Tab Chat, client_id={client_id}")

        if not hasattr(core, "sqlite_handler"):
            logger.error(f"{username}: Core không hợp lệ: Thiếu sqlite_handler")
            ui.notify("Lỗi hệ thống: Core không hợp lệ", type="negative")
            return

        if not client_state or "session_token" not in client_state or not validate_name(client_state["session_token"]):
            logger.error(f"{username}: Phiên đăng nhập không hợp lệ")
            ui.notify("Lỗi: Phiên đăng nhập không hợp lệ", type="negative")
            return

        if "model" not in client_state:
            client_state["model"] = Config.DEFAULT_MODEL
        if "chat_mode" not in client_state:
            client_state["chat_mode"] = Config.DEFAULT_CHAT_MODE

        client_storage = app.storage.client.setdefault(client_id, {})
        chat_component = client_storage.get("chat_component")
        if chat_component is None or not getattr(chat_component, "rendered", False):
            async def on_send(message: str, file=None):
                client_id = context.client.id
                client_storage = app.storage.client.get(client_id, {})
                chat_component = client_storage.get("chat_component")
                _username = client_state.get("username", "")
                logger.debug(f"{_username}: >>> on_send triggered với message='{message}', file={file}, client_id={client_id}")
                if not chat_component or not chat_component.rendered or not chat_component.message_input or not chat_component.messages_container:
                    logger.error(f"{_username}: ChatComponent chưa sẵn sàng, client_id={client_id}")
                    ui.notify("Giao diện chat chưa sẵn sàng, vui lòng thử lại!", type="negative")
                    return
                chat_component.message_input.value = message
                chat_component.uploaded_file = file
                await chat_component.handle_send()

            async def on_reset():
                client_id = context.client.id
                client_storage = app.storage.client.get(client_id, {})
                chat_component = client_storage.get("chat_component")
                _username = client_state.get("username", "")
                if chat_component and getattr(chat_component, "messages_container", None):
                    try:
                        async with chat_component.processing_lock:
                            chat_component.messages = []
                            async with aiosqlite.connect(Config.SQLITE_DB_PATH, timeout=60.0) as conn:
                                await conn.execute(
                                    "DELETE FROM chat_messages WHERE session_token = ? AND username = ?",
                                    (client_state["session_token"], _username)
                                )
                                await conn.commit()
                            await chat_component.update_messages()
                            ui.update()
                            await scroll_to_bottom(client_state)
                            logger.info(f"{_username}: Xóa lịch sử chat thành công, client_id={client_id}")
                    except Exception as e:
                        logger.error(f"{_username}: Lỗi xóa lịch sử: {str(e)}", exc_info=True)
                        ui.notify("Lỗi xóa lịch sử chat", type="negative")

            async def on_model_change(model: str):
                client_id = context.client.id
                client_storage = app.storage.client.get(client_id, {})
                chat_component = client_storage.get("chat_component")
                if chat_component:
                    async with chat_component.processing_lock:
                        client_state["model"] = model
                        await chat_component.save_state_and_config(_username)
                        ui.notify(f"Đã chuyển sang mô hình {model}", type="positive")
                        await scroll_to_bottom(client_state)

            chat_component = ChatComponent(
                messages=[],
                on_send=on_send,
                on_reset=on_reset,
                on_model_change=on_model_change,
                core=core,
                client_state=client_state,
                groq_client=groq_client,
            )
            client_storage["chat_component"] = chat_component
            logger.debug(f"{username}: Tạo mới ChatComponent cho client_id={client_id}")

        try:
            card_container = client_storage.get("chat_card_container")
            if card_container:
                try:
                    card_container.clear()
                    card_container.delete()
                except Exception as e:
                    logger.warning(f"{username}: Lỗi khi xóa chat_card_container cũ: {str(e)}")
            card_container = ui.card().classes("w-full")
            client_storage["chat_card_container"] = card_container
            with card_container:
                success = await chat_component.render()
                if not success:
                    raise RuntimeError("Render ChatComponent thất bại")
            await chat_component.update_messages()
            ui.update()
            await scroll_to_bottom(client_state)
            logger.info(f"{username}: Render tab Chat thành công, client_id={client_id}")
        except Exception as e:
            error_msg = f"Lỗi render Tab Chat: {str(e)}"
            if is_admin:
                error_msg += f"\nChi tiết: {traceback.format_exc()}"
            logger.error(error_msg, exc_info=True)
            ui.notify("Đã xảy ra lỗi khi tải giao diện chat.", type="negative")
            if card_container:
                try:
                    card_container.delete()
                except Exception as e:
                    logger.warning(f"{username}: Lỗi khi xóa chat_card_container: {str(e)}")

    async def update_func(core: Core, username: str, is_admin: bool, client_state: Dict):
        client_id = context.client.id
        logger.debug(f"{username}: Bắt đầu update_func cho Tab Chat, client_id={client_id}")
        client_storage = app.storage.client.setdefault(client_id, {})
        chat_component = client_storage.get("chat_component")
        if chat_component is None or not getattr(chat_component, "rendered", False):
            await render_func(core, username, is_admin, client_state)
            chat_component = client_storage.get("chat_component")
        if chat_component:
            async with chat_component.processing_lock:
                await chat_component.update_messages()
                ui.update()
                await scroll_to_bottom(client_state)
            logger.info(f"{username}: Cập nhật tab Chat thành công, client_id={client_id}")

    return render_func, update_func