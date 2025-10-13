from typing import Dict, Any, Optional, Callable
from nicegui import ui, app, context
from utils import get_logger
import asyncio
import inspect
import re
import json
from config import Config
from uiapp.language import get_text
from uiapp.components.button import ButtonComponent  # Import ButtonComponent

logger = get_logger("FormComponent")


class FormComponent:
    def __init__(
        self,
        fields: Dict[str, Dict],
        on_submit: Callable,
        submit_label: str = None,
        core=None,
        client_state: Dict = None,
        language: str = None
    ):
        self.client_state = client_state or {}
        self.language = app.storage.user.get("language", client_state.get("language", "vi"))
        if core and not hasattr(core, 'sqlite_handler'):
            raise ValueError(get_text(self.language, "invalid_core_error", default="Invalid core object: Missing sqlite_handler"))
        if client_state and not isinstance(client_state, dict):
            raise ValueError(get_text(self.language, "invalid_client_state_error", default="client_state must be a dictionary"))
        
        self.fields = fields or {
            "id": {
                "label": get_text(self.language, "id_label", default="ID"),
                "type": "text",
                "props": "type=hidden",
                "value": ""
            },
            "question": {
                "label": get_text(self.language, "question_label", default="Question"),
                "type": "text",
                "hint": get_text(self.language, "question_hint", default="Enter question"),
                "validation": {
                    "required": f"lambda x: bool(x) or '{get_text(self.language, 'required_field', default='This field is required')}'"
                }
            },
            "answer": {
                "label": get_text(self.language, "answer_label", default="Answer"),
                "type": "textarea",
                "hint": get_text(self.language, "answer_hint", default="Enter answer"),
                "validation": {
                    "required": f"lambda x: bool(x) or '{get_text(self.language, 'required_field', default='This field is required')}'"
                }
            },
            "category": {
                "label": get_text(self.language, "category_label", default="Category"),
                "type": "select",
                "options": [
                    get_text(self.language, "category_chat", default="chat"),
                    get_text(self.language, "category_support", default="support"),
                    get_text(self.language, "category_other", default="other")
                ],
                "value": get_text(self.language, "category_chat", default="chat"),
                "validation": {
                    "required": f"lambda x: bool(x) or '{get_text(self.language, 'required_field', default='This field is required')}'"
                }
            }
        }
        self.submit_label = submit_label or get_text(self.language, "submit_button", default="Submit")
        self.on_submit = on_submit
        self.loading = False
        self.core = core
        self.session_token = self.client_state.get("session_token", "")
        self.input_elements = {}
        self.container = None
        self.submit_button = None  # Thêm biến để lưu trữ nút gửi
        self.cancel_button = None
        self.is_editing = False
        logger.debug(f"FormComponent initialized with language={self.language}, client_state['language']={self.client_state.get('language')}")

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
                            logger.warning(get_text(self.language, "validation_error", default="Cannot evaluate validation {key}: {value}, error: {error}", key=key, value=value, error=str(e)))
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
            # Sử dụng ButtonComponent cho nút gửi
            self.submit_button = ButtonComponent(
                label=self.submit_label,
                on_click=lambda: self.handle_submit(self.collect_data()),
                classes="bg-blue-600 text-white hover:bg-blue-700",
                core=self.core,
                client_state=self.client_state,
                language=self.language
            )
            await self.submit_button.render()

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
                    # Sử dụng ButtonComponent cho nút Cancel Edit
                    self.cancel_button = ButtonComponent(
                        label="cancel_edit_button",
                        on_click=self.reset,
                        classes="bg-gray-600 text-white hover:bg-gray-700",
                        core=self.core,
                        client_state=self.client_state,
                        language=self.language
                    )
                    await self.cancel_button.render()
                self.is_editing = True
            if context.client.has_socket_connection:
                ui.update()
            logger.debug(get_text(self.language, "filled_form_data", default="Filled form with data: {data}", data=data))
        except Exception as e:
            logger.error(get_text(self.language, "fill_form_error", default="Error filling form: {error}", error=str(e)), exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(get_text(self.language, "fill_form_error", default="Error filling form: {error}", error=str(e)), type="negative")

    
    async def update(self):
        """Cập nhật giao diện của FormComponent khi ngôn ngữ thay đổi."""
        try:
            self.language = app.storage.user.get(
                "language",
                self.client_state.get("language", "vi")
            )
            logger.debug(f"FormComponent: Cập nhật với language={self.language}")

            # Cập nhật nhãn, placeholder và validation cho các trường
            for field_name, field_info in self.fields.items():
                if field_name in self.input_elements:
                    field_info["label"] = get_text(
                        self.language,
                        field_info["label"],
                        default=field_info["label"],
                    )
                    if "hint" in field_info:
                        field_info["hint"] = get_text(
                            self.language,
                            field_info["hint"],
                            default=field_info["hint"],
                        )

                    if "validation" in field_info and "required" in field_info["validation"]:
                        field_info["validation"]["required"] = (
                            "lambda x: bool(x) or '"
                            + get_text(
                                self.language,
                                "required_field",
                                default="This field is required",
                            )
                            + "'"
                        )
                        safe_validation = {}
                        for key, value in field_info["validation"].items():
                            if isinstance(value, str):
                                try:
                                    if not value.startswith("lambda x:"):
                                        value = f"lambda x: {value}"
                                    safe_validation[key] = eval(
                                        value,
                                        {"re": re, "bool": bool, "len": len},
                                    )
                                except Exception as e:
                                    logger.warning(
                                        get_text(
                                            self.language,
                                            "validation_error",
                                            default=(
                                                "Cannot evaluate validation "
                                                "{key}: {value}, error: {error}"
                                            ),
                                            key=key,
                                            value=value,
                                            error=str(e),
                                        )
                                    )
                                    safe_validation[key] = lambda x: True
                            else:
                                safe_validation[key] = value
                        self.input_elements[field_name].validation = safe_validation

                    # Sửa lỗi: Gán trực tiếp label và placeholder
                    self.input_elements[field_name].label = field_info["label"]
                    if "hint" in field_info:
                        self.input_elements[field_name].placeholder = field_info["hint"]

                    if field_info.get("type") == "select":
                        field_info["options"] = [
                            get_text(self.language, opt, default=opt)
                            for opt in field_info["options"]
                        ]
                        self.input_elements[field_name].options = field_info["options"]
                        if (
                            self.input_elements[field_name].value
                            not in field_info["options"]
                        ):
                            self.input_elements[field_name].set_value(
                                field_info.get("value", "")
                            )

                    # Cập nhật giao diện của input element
                    if context.client.has_socket_connection:
                        self.input_elements[field_name].update()

            # Cập nhật nút gửi
            if self.submit_button and context.client.has_socket_connection:
                self.submit_button.language = self.language
                await self.submit_button.update()

            # Cập nhật nút Cancel Edit nếu có
            if self.cancel_button and context.client.has_socket_connection:
                self.cancel_button.language = self.language
                await self.cancel_button.update()

            if context.client.has_socket_connection:
                ui.update()

            logger.info(
                f"FormComponent: Cập nhật giao diện thành công với language={self.language}"
            )

        except Exception as e:
            logger.error(
                f"FormComponent: Lỗi cập nhật giao diện: {str(e)}",
                exc_info=True,
            )
            if context.client.has_socket_connection:
                ui.notify(
                    get_text(
                        self.language,
                        "form_update_error",
                        default="Error updating form: {error}",
                        error=str(e),
                    ),
                    type="negative",
                )
                
    def collect_data(self) -> Dict[str, Any]:
        if not self.session_token:
            logger.warning(get_text(self.language, "empty_session_token", default="session_token is empty, using field_name directly"))
        
        data = {}
        for field_name, field_info in self.fields.items():
            try:
                storage_key = f"{self.session_token}_{field_name}" if self.session_token else field_name
                value = app.storage.user.get(storage_key, None)
                default_value = field_info.get("value", "")
                data[field_name] = value if isinstance(value, str) and value else default_value
                logger.debug(get_text(self.language, "collect_field", default="Collected {field_name}: {value}", field_name=field_name, value=data[field_name]))
            except Exception as e:
                logger.warning(get_text(self.language, "collect_field_error", default="Cannot collect data for field {field_name}: {error}", field_name=field_name, error=str(e)))
                data[field_name] = field_info.get("value", "")
        logger.info(get_text(self.language, "collected_form_data", default="Collected form data: {data}", data=data))
        return data

    
    async def reset(self):
        """Đặt lại form về trạng thái trống."""
        try:
            for field_name, field_info in self.fields.items():
                storage_key = f"{self.session_token}_{field_name}" if self.session_token else field_name
                if storage_key in app.storage.user:
                    logger.debug(get_text(self.language, "delete_storage_key", default="Deleting storage key: {storage_key}", storage_key=storage_key))
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
            logger.debug(get_text(self.language, "form_reset_success", default="Form reset to empty state"))
        except Exception as e:
            logger.error(get_text(self.language, "form_reset_error", default="Error resetting form: {error}", error=str(e)), exc_info=True)
            if context.client.has_socket_connection:
                ui.notify(get_text(self.language, "form_reset_error", default="Error resetting form: {error}", error=str(e)), type="negative")

    async def handle_submit(self, data: Dict[str, Any], progress_callback: Optional[Callable] = None) -> Dict:
        logger.info(get_text(self.language, "submit_form_data", default="Submitting form with data: {data}", data=data))
        try:
            if self.loading:
                logger.warning(get_text(self.language, "form_processing", default="Form is being processed, ignoring submit"))
                return {"success": False, "error": get_text(self.language, "form_processing", default="Form is being processed")}
            self.loading = True

            if len(json.dumps(data).encode()) > Config.MAX_UPLOAD_SIZE:
                logger.warning(get_text(self.language, "form_data_too_large", default="Form data too large, exceeds 1MB"))
                return {"success": False, "error": get_text(self.language, "form_data_too_large", default="Form data too large")}
            
            if not data.get("question") and "question" in self.fields:
                logger.warning(get_text(self.language, "question_empty", default="Question cannot be empty"))
                return {"success": False, "error": get_text(self.language, "question_empty", default="Question cannot be empty")}
            if not data.get("answer") and "answer" in self.fields:
                logger.warning(get_text(self.language, "answer_empty", default="Answer cannot be empty"))
                return {"success": False, "error": get_text(self.language, "answer_empty", default="Answer cannot be empty")}
            if not data.get("category") and "category" in self.fields:
                logger.warning(get_text(self.language, "category_empty", default="Category cannot be empty, received data: {data}", data=data))
                return {"success": False, "error": get_text(self.language, "category_empty", default="Category cannot be empty")}

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
                logger.info(get_text(self.language, "submit_success", default="Submit successful: {message}", message=response.get("message", "Success")))
                return response
            else:
                error_msg = response.get("error", get_text(self.language, "save_qa_failed", default="Failed to save Q&A"))
                logger.warning(get_text(self.language, "submit_failed", default="Submit failed: {error}", error=error_msg))
                return {"success": False, "error": error_msg}

        except Exception as e:
            error_msg = get_text(self.language, "submit_form_error", default="Error submitting form: {error}", error=str(e))
            logger.error(error_msg, exc_info=True)
            return {"success": False, "error": str(e)}
        finally:
            self.loading = False

    