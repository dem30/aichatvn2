import asyncio
import traceback
from typing import Callable, Dict, Tuple
from nicegui import ui, app, context
from tenacity import retry, stop_after_attempt, wait_fixed
from config import Config
from core import Core
from uiapp.components.training import TrainingComponent
from utils.core_common import validate_name
from utils.logging import get_logger
from uiapp.language import get_text

logger = get_logger("TabTraining")

async def create_tab(core: Core) -> Tuple[Callable, Callable]:
    @retry(stop=stop_after_attempt(3), wait=wait_fixed(0.5))
    async def render_func(core: Core, username: str, is_admin: bool, client_state: Dict):
        client_id = context.client.id
        language = client_state.get("language", app.storage.user.get("language", "vi"))
        logger.debug(f"{username}: Bắt đầu render_func cho Tab Training, client_id={client_id}, language={language}")

        if not hasattr(core, "sqlite_handler"):
            logger.error(f"{username}: Core không hợp lệ: Thiếu sqlite_handler")
            if context.client.has_socket_connection:
                ui.notify(get_text(language, "invalid_core_error", "Core không hợp lệ: Thiếu sqlite_handler"), type="negative")
            return

        if not client_state or "session_token" not in client_state or not validate_name(client_state["session_token"]):
            logger.error(f"{username}: Phiên đăng nhập không hợp lệ")
            if context.client.has_socket_connection:
                ui.notify(get_text(language, "invalid_session", "Phiên không hợp lệ"), type="negative")
            return

        if not is_admin:
            logger.warning(f"{username}: Không có quyền admin")
            if context.client.has_socket_connection:
                ui.notify(get_text(language, "no_admin_access", "Chỉ admin có quyền truy cập tab training này"), type="negative")
            return

        client_storage = app.storage.client.setdefault(client_id, {})
        training_component = client_storage.get("training_component")
        if training_component is None or not getattr(training_component, "rendered", False):
            training_component = TrainingComponent(
                core=core,
                client_state=client_state,
                classes="w-full p-4 sm:p-6 md:p-8 flex flex-col gap-4",
                language=language
            )
            training_component.client_id = client_id
            client_storage["training_component"] = training_component
            logger.debug(f"{username}: Tạo mới TrainingComponent cho client_id={client_id}, language={language}")

        try:
            card_container = client_storage.get("training_card_container")
            if card_container:
                try:
                    card_container.clear()
                    card_container.delete()
                    logger.debug(f"{username}: Xóa training_card_container cũ thành công")
                except Exception as e:
                    logger.debug(f"{username}: Không thể xóa container cũ: {str(e)}")

            await asyncio.sleep(0.05)
            training_component.container = ui.card().classes(training_component.classes)
            client_storage["training_card_container"] = training_component.container
            
            await training_component.render()
            await training_component.save_state_and_config()
            ui.update()
            logger.info(f"{username}: Render tab Training thành công, client_id={client_id}")
        except Exception as e:
            error_msg = get_text(language, "tab_render_error", "Lỗi render tab {tab_name}: {error}", tab_name="Training", error=str(e))
            if is_admin:
                error_msg += f"\n{get_text(language, 'details', 'Chi tiết')}: {traceback.format_exc()}"
            logger.error(error_msg, exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(get_text(language, "render_training_error", "Đã xảy ra lỗi khi tải giao diện training."), type="negative")
            if training_component.container:
                try:
                    training_component.container.delete()
                    logger.debug(f"{username}: Xóa training_card_container sau lỗi thành công")
                except Exception as e:
                    logger.warning(f"{username}: Lỗi khi xóa training_card_container: {str(e)}")

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(0.5))
    async def update_func(core: Core, username: str, is_admin: bool, client_state: Dict):
        client_id = context.client.id
        language = client_state.get("language", app.storage.user.get("language", "vi"))
        logger.debug(f"{username}: Bắt đầu update_func cho Tab Training, client_id={client_id}, language={language}")

        client_storage = app.storage.client.setdefault(client_id, {})
        training_component = client_storage.get("training_component")
        if training_component is None or not getattr(training_component, "rendered", False):
            logger.debug(f"{username}: TrainingComponent chưa sẵn sàng, gọi lại render_func")
            await render_func(core, username, is_admin, client_state)
            training_component = client_storage.get("training_component")

        if training_component and training_component.rendered:
            async with training_component.processing_lock:
                success = await training_component.update()
                if success:
                    ui.update()
                    logger.info(f"{username}: Cập nhật tab Training thành công, client_id={client_id}")
                else:
                    logger.warning(f"{username}: Cập nhật tab Training thất bại")
                    if context.client.has_socket_connection:
                        ui.notify(get_text(language, "update_training_error", "Lỗi: Không thể cập nhật tab Training"), type="negative")
        else:
            logger.warning(f"{username}: TrainingComponent không tồn tại hoặc chưa render, không thể cập nhật")
            if context.client.has_socket_connection:
                ui.notify(get_text(language, "training_ui_not_ready", "Lỗi: Giao diện Training chưa sẵn sàng"), type="negative")

    return render_func, update_func

# CSS đáp ứng cho card
ui.add_css("""
    .card {
        width: 100%;
        margin-bottom: 0.5rem;
    }
    @media (max-width: 640px) {
        .card {
            padding: 0.5rem;
        }
        .q-btn {
            width: 100%;
            margin-bottom: 0.5rem;
        }
    }
""")

@app.on_disconnect
async def cleanup_client_storage():
    client_id = context.client.id
    language = app.storage.client.get(client_id, {}).get("language", "vi")
    logger.debug(f"{client_id}: Client ngắt kết nối, bắt đầu dọn dẹp")
    client_storage = app.storage.client.get(client_id, {})
    card_container = client_storage.get("training_card_container")
    if card_container:
        try:
            if hasattr(card_container, 'parent_slot') and card_container in card_container.parent_slot.children:
                card_container.clear()
                card_container.delete()
                logger.debug(f"{client_id}: Xóa training_card_container thành công")
            else:
                logger.debug(f"{client_id}: training_card_container đã bị xóa trước đó")
        except Exception as e:
            logger.warning(f"{client_id}: Lỗi khi xóa training_card_container: {str(e)}")
    app.storage.client.pop(client_id, None)
    logger.debug(f"{client_id}: Đã xóa client storage")