import time
import logging
from typing import Callable
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log all requests with timing."""
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Log request details and timing."""
        start_time = time.time()
        
        # Log request
        logger.info(
            f"Request started: {request.method} {request.url.path}"
        )
        
        # Process request
        response = await call_next(request)
        
        # Calculate duration
        duration = time.time() - start_time
        
        # Log response
        logger.info(
            f"Request completed: {request.method} {request.url.path} "
            f"- Status: {response.status_code} - Duration: {duration:.3f}s"
        )
        
        # Add custom headers
        response.headers["X-Process-Time"] = str(duration)
        
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Middleware to apply rate limiting to specific endpoints.
    Applied globally but checks are done per user/identifier.
    """
    
    def __init__(self, app: ASGIApp):
        super().__init__(app)
        self.rate_limited_paths = [
            "/api/v1/executions",
            "/api/v1/functions"
        ]
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Apply rate limiting checks."""
        path = request.url.path
        
        # Check if path should be rate limited
        should_limit = any(
            path.startswith(limited_path) 
            for limited_path in self.rate_limited_paths
        )
        
        if should_limit and request.method in ["POST", "PUT"]:
            # Extract identifier from headers or path
            # In production, this would come from authentication
            identifier = request.headers.get("X-User-ID", "anonymous")
            tier = request.headers.get("X-User-Tier", "free")
            
            # Apply rate limit check
            from api.rate_limiter import get_rate_limiter
            rate_limiter = get_rate_limiter()
            
            try:
                # Check rate limit
                limit_info = await rate_limiter.check_rate_limit(identifier, tier)
                
                # Add rate limit headers to response
                response = await call_next(request)
                response.headers["X-RateLimit-Limit"] = str(limit_info["limit"])
                response.headers["X-RateLimit-Remaining"] = str(limit_info["remaining"])
                response.headers["X-RateLimit-Reset"] = str(limit_info["reset"])
                
                return response
                
            except Exception as e:
                # Rate limit exceeded or other error
                # Let the exception propagate to FastAPI's exception handler
                raise
        
        # No rate limiting needed
        return await call_next(request)


class MetricsMiddleware(BaseHTTPMiddleware):
    """Middleware to collect request metrics."""
    
    def __init__(self, app: ASGIApp):
        super().__init__(app)
        self.request_count = 0
        self.error_count = 0
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Collect request metrics."""
        self.request_count += 1
        
        try:
            response = await call_next(request)
            
            # Count errors
            if response.status_code >= 400:
                self.error_count += 1
            
            return response
            
        except Exception as e:
            self.error_count += 1
            raise
    
    def get_metrics(self) -> dict:
        """Get collected metrics."""
        return {
            "total_requests": self.request_count,
            "total_errors": self.error_count,
            "error_rate": self.error_count / max(1, self.request_count)
        }