from typing import Dict, Any, Optional, Callable
from nicegui import ui, app, context
from utils import get_logger
import asyncio
import inspect
import re
import json
from config import Config

logger = get_logger("FormComponent")


class FormComponent:
    def __init__(
        self,
        fields: Dict[str, Dict],
        on_submit: Callable,
        submit_label: str = "Submit",
        core=None,
        client_state: Dict = None
    ):
        if core and not hasattr(core, 'sqlite_handler'):
            raise ValueError("Invalid core object: Missing sqlite_handler")
        if client_state and not isinstance(client_state, dict):
            raise ValueError("client_state must be a dictionary")
        
        self.fields = fields or {
            "id": {"label": "ID", "type": "text", "props": "type=hidden", "value": ""},
            "question": {
                "label": "Câu hỏi",
                "type": "text",
                "hint": "Nhập câu hỏi",
                "validation": {
                    "required": "lambda x: bool(x) or 'Trường này là bắt buộc'"
                }
            },
            "answer": {
                "label": "Câu trả lời",
                "type": "textarea",
                "hint": "Nhập câu trả lời",
                "validation": {
                    "required": "lambda x: bool(x) or 'Trường này là bắt buộc'"
                }
            },
            "category": {
                "label": "Danh mục",
                "type": "select",
                "options": ["chat", "support", "other"],
                "value": "chat",
                "validation": {
                    "required": "lambda x: bool(x) or 'Trường này là bắt buộc'"
                }
            }
        }
        self.on_submit = on_submit
        self.submit_label = submit_label
        self.loading = False
        self.core = core
        self.client_state = client_state or {}
        self.session_token = self.client_state.get("session_token", "")
        self.input_elements = {}
        self.container = None
        self.cancel_button = None
        self.is_editing = False

    def collect_data(self) -> Dict[str, Any]:
        if not self.session_token:
            logger.warning("session_token rỗng, sử dụng field_name trực tiếp")
        
        data = {}
        for field_name, field_info in self.fields.items():
            try:
                storage_key = f"{self.session_token}_{field_name}" if self.session_token else field_name
                value = app.storage.user.get(storage_key, None)
                default_value = field_info.get("value", "")
                data[field_name] = value if isinstance(value, str) and value else default_value
                logger.debug(f"Thu thập {field_name}: {data[field_name]}")
            except Exception as e:
                logger.warning(f"Không thể lấy dữ liệu cho trường {field_name}: {str(e)}")
                data[field_name] = field_info.get("value", "")
        logger.info(f"Dữ liệu form thu thập được: {data}")
        return data

    async def set_data(self, data: Dict[str, Any]):
        """Điền dữ liệu vào form để chỉnh sửa."""
        try:
            for field_name, value in data.items():
                if field_name in self.fields:
                    storage_key = f"{self.session_token}_{field_name}" if self.session_token else field_name
                    app.storage.user[storage_key] = value
                    if field_name in self.input_elements:
                        if self.fields[field_name].get("type") == "select":
                            self.input_elements[field_name].set_value(value)
                        else:
                            self.input_elements[field_name].value = value
            if data.get("id") and self.container and not self.cancel_button:
                with self.container:
                    self.cancel_button = ui.button(
                        "Hủy chỉnh sửa",
                        on_click=self.reset
                    ).classes("bg-gray-600 text-white hover:bg-gray-700")
                self.is_editing = True
            if context.client.has_socket_connection:
                ui.update()
            logger.debug(f"Đã điền dữ liệu vào form: {data}")
        except Exception as e:
            logger.error(f"Lỗi điền dữ liệu vào form: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(f"Lỗi điền dữ liệu vào form: {str(e)}", type="negative")

    async def reset(self):
        """Đặt lại form về trạng thái trống."""
        try:
            for field_name, field_info in self.fields.items():
                storage_key = f"{self.session_token}_{field_name}" if self.session_token else field_name
                if storage_key in app.storage.user:
                    logger.debug(f"Xóa storage key: {storage_key}")
                    del app.storage.user[storage_key]
                default_value = field_info.get("value", "")
                if field_name in self.input_elements:
                    if field_info.get("type") == "select":
                        self.input_elements[field_name].set_value(default_value)
                    else:
                        self.input_elements[field_name].value = default_value
            if self.cancel_button:
                self.cancel_button.delete()
                self.cancel_button = None
            self.is_editing = False
            if context.client.has_socket_connection:
                ui.update()
            logger.debug("Đã đặt lại form về trạng thái trống")
        except Exception as e:
            logger.error(f"Lỗi đặt lại form: {str(e)}", exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(f"Lỗi đặt lại form: {str(e)}", type="negative")

    async def handle_submit(self, data: Dict[str, Any], progress_callback: Optional[Callable] = None) -> Dict:
        logger.info(f"Submit form với data: {data}")
        try:
            if self.loading:
                logger.warning("Form đang được xử lý, bỏ qua submit")
                return {"success": False, "error": "Form đang được xử lý"}
            self.loading = True

            if len(json.dumps(data).encode()) > Config.MAX_UPLOAD_SIZE:
                logger.warning("Dữ liệu form quá lớn, vượt quá 1MB")
                return {"success": False, "error": "Dữ liệu form quá lớn"}

            if not data.get("question") and "question" in self.fields:
                logger.warning("Câu hỏi không được để trống")
                return {"success": False, "error": "Câu hỏi không được để trống"}
            if not data.get("answer") and "answer" in self.fields:
                logger.warning("Câu trả lời không được để trống")
                return {"success": False, "error": "Câu trả lời không được để trống"}
            if not data.get("category") and "category" in self.fields:
                logger.warning(f"Danh mục không được để trống, dữ liệu nhận được: {data}")
                return {"success": False, "error": "Danh mục không được để trống"}

            signature = inspect.signature(self.on_submit)
            accepts_progress = "progress_callback" in signature.parameters
            if asyncio.iscoroutinefunction(self.on_submit):
                if accepts_progress:
                    response = await self.on_submit(data, progress_callback=progress_callback)
                else:
                    response = await self.on_submit(data)
            else:
                if accepts_progress:
                    response = self.on_submit(data, progress_callback=progress_callback)
                else:
                    response = self.on_submit(data)

            if isinstance(response, dict) and response.get("success"):
                await self.reset()
                logger.info(f"Submit thành công: {response.get('message', 'Thành công')}")
                return response
            else:
                error_msg = response.get("error", "Lưu Q&A thất bại")
                logger.warning(f"Submit thất bại: {error_msg}")
                return {"success": False, "error": error_msg}

        except Exception as e:
            error_msg = f"Lỗi submit form: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {"success": False, "error": str(e)}
        finally:
            self.loading = False

    async def render(self):
        self.container = ui.card().classes("w-full p-4")
        with self.container:
            self.input_elements = {}
            for field_name, field_info in self.fields.items():
                validation = field_info.get("validation", {})
                safe_validation = {}
                for key, value in validation.items():
                    if isinstance(value, str):
                        try:
                            if not value.startswith("lambda x:"):
                                value = f"lambda x: {value}"
                            safe_validation[key] = eval(value, {"re": re, "bool": bool, "len": len})
                        except Exception as e:
                            logger.warning(f"Không thể đánh giá validation {key}: {value}, lỗi: {str(e)}")
                            safe_validation[key] = lambda x: True
                    else:
                        safe_validation[key] = value
                input_type = field_info.get("type", "text")
                storage_key = f"{self.session_token}_{field_name}" if self.session_token else field_name
                default_value = field_info.get("value", "")
                if storage_key not in app.storage.user:
                    app.storage.user[storage_key] = default_value
                if input_type == "select":
                    input_element = ui.select(
                        options=field_info.get("options", []),
                        label=field_info.get("label", field_name),
                        validation=safe_validation,
                        value=default_value
                    ).props('clearable').bind_value(app.storage.user, storage_key)
                elif input_type == "textarea":
                    input_element = ui.textarea(
                        label=field_info.get("label", field_name),
                        validation=safe_validation,
                        placeholder=field_info.get("hint", ""),
                        value=default_value
                    ).props('clearable').bind_value(app.storage.user, storage_key)
                else:
                    input_element = ui.input(
                        label=field_info.get("label", field_name),
                        validation=safe_validation,
                        placeholder=field_info.get("hint", ""),
                        password=input_type == "password",
                        value=default_value
                    ).props('clearable').bind_value(app.storage.user, storage_key)
                if field_info.get("props") == "type=hidden":
                    input_element.style("display: none")
                self.input_elements[field_name] = input_element
            ui.button(self.submit_label, on_click=lambda: self.handle_submit(self.collect_data())).classes(
                "bg-blue-600 text-white hover:bg-blue-700"
            )