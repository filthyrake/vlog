"""
API Versioning Infrastructure (Issue #218)

Provides utilities for versioned API routes, deprecation headers,
and enhanced OpenAPI documentation.

Usage:
    from api.versioning import create_versioned_router, VersionInfo

    # Create a versioned router
    v1_router = create_versioned_router("v1")

    # Add routes to the versioned router
    @v1_router.get("/videos")
    async def list_videos():
        ...

    # Include in app with version prefix
    app.include_router(v1_router, prefix="/api/v1")
"""

import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Request, Response
from fastapi.routing import APIRoute
from starlette.middleware.base import BaseHTTPMiddleware

from config import (
    API_DEPRECATION_NOTICE,
    API_DEPRECATION_SUNSET,
    API_INCLUDE_LEGACY_ROUTES,
    API_SUPPORTED_VERSIONS,
    API_VERSION,
    OPENAPI_CONTACT_EMAIL,
    OPENAPI_CONTACT_NAME,
    OPENAPI_DESCRIPTION,
    OPENAPI_LICENSE_NAME,
    OPENAPI_LICENSE_URL,
    OPENAPI_TERMS_OF_SERVICE,
    OPENAPI_TITLE,
)

logger = logging.getLogger(__name__)

# Regex pattern for valid API version format (e.g., "v1", "v2", "v10")
API_VERSION_PATTERN = re.compile(r"^v\d+$")


def sanitize_header_value(value: str) -> str:
    """
    Sanitize a string for use in HTTP headers.

    Removes carriage return and newline characters to prevent
    HTTP header injection (CRLF injection) attacks.

    Args:
        value: The string to sanitize

    Returns:
        Sanitized string safe for use in HTTP headers
    """
    if not value:
        return value
    return value.replace("\r", "").replace("\n", "")


def validate_config() -> None:
    """
    Validate API versioning configuration at startup.

    Raises:
        ValueError: If configuration is invalid

    This function is called at module load to fail fast on
    misconfiguration rather than causing runtime errors.
    """
    # Validate API_VERSION is set and non-empty
    if not API_VERSION:
        raise ValueError(
            "VLOG_API_VERSION must be set and non-empty. "
            "Example: VLOG_API_VERSION=v1"
        )

    # Validate API_VERSION matches expected format
    if not API_VERSION_PATTERN.match(API_VERSION):
        raise ValueError(
            f"VLOG_API_VERSION '{API_VERSION}' has invalid format. "
            f"Must match pattern 'v' followed by a number (e.g., 'v1', 'v2'). "
            f"This prevents path injection vulnerabilities."
        )

    # Validate API_SUPPORTED_VERSIONS is set and non-empty
    if not API_SUPPORTED_VERSIONS:
        raise ValueError(
            "API_SUPPORTED_VERSIONS must contain at least one version. "
            "Check config.py to ensure API_SUPPORTED_VERSIONS is properly defined."
        )

    # Validate all supported versions match the expected format
    for version in API_SUPPORTED_VERSIONS:
        if not API_VERSION_PATTERN.match(version):
            raise ValueError(
                f"API_SUPPORTED_VERSIONS contains invalid version '{version}'. "
                f"All versions must match pattern 'v' followed by a number."
            )

    # Warn if current version is not in supported versions
    if API_VERSION not in API_SUPPORTED_VERSIONS:
        logger.warning(
            f"API_VERSION '{API_VERSION}' is not in API_SUPPORTED_VERSIONS "
            f"{API_SUPPORTED_VERSIONS}. This may cause unexpected behavior."
        )

    # Validate deprecation sunset date format if set
    if API_DEPRECATION_SUNSET:
        # Check for CRLF injection attempts
        if "\r" in API_DEPRECATION_SUNSET or "\n" in API_DEPRECATION_SUNSET:
            raise ValueError(
                "VLOG_API_DEPRECATION_SUNSET contains invalid characters (CR/LF). "
                "This could enable HTTP header injection attacks."
            )

    logger.debug(
        f"API versioning config validated: version={API_VERSION}, "
        f"supported={API_SUPPORTED_VERSIONS}"
    )


# Validate configuration at module load (fail fast)
validate_config()


@dataclass
class VersionInfo:
    """Information about an API version."""

    version: str
    is_current: bool = False
    is_deprecated: bool = False
    sunset_date: Optional[str] = None
    description: Optional[str] = None

    @property
    def prefix(self) -> str:
        """Return the URL prefix for this version (e.g., '/api/v1')."""
        return f"/api/{self.version}"


def get_version_info(version: str) -> VersionInfo:
    """Get version information for a specific API version."""
    is_current = version == API_VERSION
    is_deprecated = not is_current and API_DEPRECATION_NOTICE
    sunset_date = API_DEPRECATION_SUNSET if is_deprecated else None

    return VersionInfo(
        version=version,
        is_current=is_current,
        is_deprecated=is_deprecated,
        sunset_date=sunset_date,
        description=f"API version {version}" + (" (current)" if is_current else ""),
    )


def get_all_versions() -> List[VersionInfo]:
    """Get information about all supported API versions."""
    return [get_version_info(v) for v in API_SUPPORTED_VERSIONS]


class DeprecationHeadersRoute(APIRoute):
    """
    Custom route class that adds deprecation headers to responses.

    Adds the following headers for deprecated versions:
    - Deprecation: true
    - Sunset: <date> (if configured)
    - Link: <docs-url>; rel="successor-version"
    """

    def __init__(self, *args, version_info: Optional[VersionInfo] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.version_info = version_info

    def get_route_handler(self) -> Callable:
        original_handler = super().get_route_handler()

        async def custom_handler(request: Request) -> Response:
            try:
                response: Response = await original_handler(request)
            except Exception:
                # Let exception propagate - deprecation headers aren't critical for errors
                raise

            if self.version_info and self.version_info.is_deprecated:
                try:
                    response.headers["Deprecation"] = "true"

                    if self.version_info.sunset_date:
                        # Sanitize to prevent header injection
                        response.headers["Sunset"] = sanitize_header_value(
                            self.version_info.sunset_date
                        )

                    # Link to current version documentation
                    current_docs_url = f"/api/{API_VERSION}/docs"
                    response.headers["Link"] = f'<{current_docs_url}>; rel="successor-version"'
                except Exception as e:
                    # Don't fail the response if deprecation headers can't be added
                    logger.warning(f"Failed to add deprecation headers: {e}")

            return response

        return custom_handler


def create_versioned_router(
    version: str,
    *,
    tags: Optional[List[str]] = None,
    deprecated: Optional[bool] = None,
) -> APIRouter:
    """
    Create an APIRouter configured for a specific API version.

    Args:
        version: Version string (e.g., "v1", "v2")
        tags: Optional list of tags for OpenAPI documentation
        deprecated: Override deprecation status (auto-detected if None)

    Returns:
        APIRouter configured with version-specific settings
    """
    version_info = get_version_info(version)

    # Allow override of deprecation status
    if deprecated is not None:
        version_info = VersionInfo(
            version=version_info.version,
            is_current=version_info.is_current,
            is_deprecated=deprecated,
            sunset_date=version_info.sunset_date if deprecated else None,
            description=version_info.description,
        )

    # Create router with deprecation-aware route class for deprecated versions
    if version_info.is_deprecated:

        class VersionedDeprecationRoute(DeprecationHeadersRoute):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, version_info=version_info, **kwargs)

        route_class = VersionedDeprecationRoute
    else:
        route_class = APIRoute

    router = APIRouter(
        tags=tags or [f"API {version}"],
        route_class=route_class,
    )

    return router


class VersionHeaderMiddleware(BaseHTTPMiddleware):
    """
    Middleware that adds API version information to response headers.

    Headers added:
    - X-API-Version: Current API version
    - X-API-Supported-Versions: Comma-separated list of supported versions

    This middleware includes error handling to ensure version header
    failures don't crash the API or prevent responses from being sent.
    """

    async def dispatch(self, request: Request, call_next):
        try:
            response = await call_next(request)
        except Exception as e:
            # Log that versioning failed on this request, then re-raise
            logger.debug(f"Request failed before version headers could be added: {e}")
            raise

        # Only add version headers to API responses (precise matching)
        if request.url.path.startswith("/api/") or request.url.path == "/api":
            try:
                response.headers["X-API-Version"] = API_VERSION
                response.headers["X-API-Supported-Versions"] = ",".join(
                    API_SUPPORTED_VERSIONS
                )
            except Exception as e:
                # Don't fail the request if header setting fails
                logger.warning(f"Failed to add version headers: {e}")

        return response


def configure_openapi_schema(
    app,
    *,
    title: Optional[str] = None,
    version: Optional[str] = None,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Configure and return an enhanced OpenAPI schema for the application.

    This function customizes the auto-generated OpenAPI schema with:
    - Custom title, description, and version
    - Contact and license information
    - Version-specific server entries
    - Enhanced endpoint descriptions

    Includes error handling to return a minimal valid schema on failure,
    ensuring the /docs endpoint remains functional.

    Args:
        app: FastAPI application instance
        title: Override title (uses OPENAPI_TITLE if not provided)
        version: Override version (uses API_VERSION if not provided)
        description: Override description (uses OPENAPI_DESCRIPTION if not provided)

    Returns:
        Dict containing the OpenAPI schema
    """
    # Return cached schema if available
    if app.openapi_schema:
        return app.openapi_schema

    try:
        from fastapi.openapi.utils import get_openapi

        # Build info object
        info: Dict[str, Any] = {
            "title": title or OPENAPI_TITLE,
            "version": version or API_VERSION,
            "description": description or OPENAPI_DESCRIPTION,
        }

        if OPENAPI_TERMS_OF_SERVICE:
            info["termsOfService"] = OPENAPI_TERMS_OF_SERVICE

        if OPENAPI_CONTACT_NAME or OPENAPI_CONTACT_EMAIL:
            info["contact"] = {}
            if OPENAPI_CONTACT_NAME:
                info["contact"]["name"] = OPENAPI_CONTACT_NAME
            if OPENAPI_CONTACT_EMAIL:
                info["contact"]["email"] = OPENAPI_CONTACT_EMAIL

        if OPENAPI_LICENSE_NAME:
            info["license"] = {"name": OPENAPI_LICENSE_NAME}
            if OPENAPI_LICENSE_URL:
                info["license"]["url"] = OPENAPI_LICENSE_URL

        openapi_schema = get_openapi(
            title=info["title"],
            version=info["version"],
            description=info["description"],
            routes=app.routes,
        )

        # Add additional info fields
        if "termsOfService" in info:
            openapi_schema["info"]["termsOfService"] = info["termsOfService"]
        if "contact" in info:
            openapi_schema["info"]["contact"] = info["contact"]
        if "license" in info:
            openapi_schema["info"]["license"] = info["license"]

        # Add version information to description
        version_info_text = "\n\n## API Versions\n\n"
        for v_info in get_all_versions():
            status = (
                "**Current**"
                if v_info.is_current
                else "Deprecated"
                if v_info.is_deprecated
                else "Supported"
            )
            version_info_text += f"- `{v_info.version}`: {status}\n"
            if v_info.is_deprecated and v_info.sunset_date:
                version_info_text += f"  - Sunset date: {v_info.sunset_date}\n"

        openapi_schema["info"]["description"] = (
            openapi_schema["info"].get("description", "") + version_info_text
        ).strip()

        # Add servers for each version
        openapi_schema["servers"] = [
            {"url": f"/api/{API_VERSION}", "description": f"Current API ({API_VERSION})"}
        ]

        app.openapi_schema = openapi_schema
        return app.openapi_schema

    except Exception as e:
        # Log the error and return a minimal valid schema
        logger.error(f"OpenAPI schema generation failed: {e}", exc_info=True)

        # Return minimal valid OpenAPI schema so /docs still works
        minimal_schema = {
            "openapi": "3.1.0",
            "info": {
                "title": title or OPENAPI_TITLE or "API",
                "version": version or API_VERSION or "1.0.0",
                "description": f"OpenAPI schema generation encountered an error: {str(e)}",
            },
            "paths": {},
        }

        # Don't cache the error schema - allow retry on next request
        return minimal_schema


def setup_versioned_api(app, routers: Dict[str, APIRouter]) -> None:
    """
    Set up versioned API routes on a FastAPI application.

    This function:
    1. Mounts versioned routers at their respective prefixes
    2. Optionally adds legacy unversioned routes that alias to the current version
    3. Adds version header middleware
    4. Configures the OpenAPI schema

    Args:
        app: FastAPI application instance
        routers: Dict mapping version strings to APIRouter instances
            Example: {"v1": v1_router, "v2": v2_router}
    """
    # Add version header middleware
    app.add_middleware(VersionHeaderMiddleware)

    # Mount versioned routers
    for version, router in routers.items():
        app.include_router(router, prefix=f"/api/{version}")
        logger.info(f"Mounted API {version} at /api/{version}")

    # Add legacy routes if configured
    if API_INCLUDE_LEGACY_ROUTES and API_VERSION in routers:
        # Mount current version router without version prefix for backwards compatibility
        app.include_router(
            routers[API_VERSION],
            prefix="/api",
            include_in_schema=False,  # Don't duplicate in OpenAPI docs
        )
        logger.info(f"Mounted legacy routes at /api (aliased to {API_VERSION})")

    # Configure OpenAPI schema
    def custom_openapi():
        return configure_openapi_schema(app)

    app.openapi = custom_openapi
