from fastapi.responses import JSONResponse
import logging

class DatabaseError(Exception):
    """Lỗi liên quan đến cơ sở dữ liệu."""
    pass

class AuthError(Exception):
    """Lỗi liên quan đến xác thực."""
    pass

def handle_exception(exc: Exception, logger: logging.Logger, return_value=None) -> JSONResponse:
    """Xử lý ngoại lệ và trả về JSONResponse."""
    logger.error(f"Error: {str(exc)}", exc_info=True)
    return JSONResponse({"error": f"Lỗi server: {str(exc)}"}, status_code=500) if return_value is None else return_value
