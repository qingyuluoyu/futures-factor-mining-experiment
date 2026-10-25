import logging
from time import perf_counter
from uuid import uuid4

from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException


logger = logging.getLogger("qihuo.financial_request")


class FinancialRequestRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def traced(request):
            request_id = uuid4().hex
            request.state.request_id = request_id
            started = perf_counter()
            status = 500
            error_type = "none"
            try:
                try:
                    response = await original(request)
                except RequestValidationError as error:
                    error_type = type(error).__name__
                    response = await request_validation_exception_handler(request, error)
                except HTTPException as error:
                    status = error.status_code
                    error_type = type(error).__name__
                    request_id = (error.headers or {}).get("X-Request-ID") or request_id
                    error.headers = {**(error.headers or {}), "X-Request-ID": request_id}
                    raise
                request_id = response.headers.get("X-Request-ID") or request_id
                request.state.request_id = request_id
                response.headers["X-Request-ID"] = request_id
                status = response.status_code
                return response
            except Exception as error:
                error_type = type(error).__name__
                raise
            finally:
                log = logger.warning if status >= 400 else logger.info
                log("request_id=%s operation=%s method=%s status=%s error_type=%s elapsed_ms=%.1f", request_id, self.path, request.method, status, error_type, (perf_counter() - started) * 1000)

        return traced
