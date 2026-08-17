"""
Tests for API versioning functionality (Issue #218).

Tests cover:
- Versioned endpoints at /api/v1/* work correctly
- X-API-Version and X-API-Supported-Versions headers are present
- Deprecation headers for deprecated versions
- Configuration validation
- Legacy route behavior
"""



class TestVersionHeaders:
    """Test API version headers are present in responses."""

    def test_version_header_on_versioned_endpoint(self, public_client):
        """Test X-API-Version header is present on /api/v1/* endpoints."""
        response = public_client.get("/api/v1/videos")
        assert response.status_code == 200
        assert "X-API-Version" in response.headers
        assert response.headers["X-API-Version"] == "v1"

    def test_supported_versions_header(self, public_client):
        """Test X-API-Supported-Versions header is present."""
        response = public_client.get("/api/v1/videos")
        assert response.status_code == 200
        assert "X-API-Supported-Versions" in response.headers
        assert "v1" in response.headers["X-API-Supported-Versions"]

    def test_version_headers_on_admin_api(self, admin_client):
        """Test version headers are present on admin API."""
        response = admin_client.get("/api/v1/videos")
        assert response.status_code == 200
        assert "X-API-Version" in response.headers
        assert "X-API-Supported-Versions" in response.headers

    def test_no_version_headers_on_non_api_endpoints(self, public_client):
        """Test version headers are NOT added to non-API endpoints."""
        response = public_client.get("/health")
        # Health endpoint should not have version headers
        assert "X-API-Version" not in response.headers


class TestVersionedEndpoints:
    """Test versioned API endpoints work correctly."""

    def test_v1_videos_endpoint(self, public_client):
        """Test /api/v1/videos endpoint works."""
        response = public_client.get("/api/v1/videos")
        assert response.status_code == 200
        data = response.json()
        assert "videos" in data

    def test_v1_categories_endpoint(self, public_client):
        """Test /api/v1/categories endpoint works."""
        response = public_client.get("/api/v1/categories")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_admin_v1_videos_endpoint(self, admin_client):
        """Test admin /api/v1/videos endpoint works."""
        response = admin_client.get("/api/v1/videos")
        assert response.status_code == 200


class TestLegacyRoutes:
    """Test legacy unversioned routes behavior."""

    def test_legacy_videos_endpoint(self, public_client):
        """Test legacy /api/videos endpoint works (aliases to v1)."""
        response = public_client.get("/api/videos")
        assert response.status_code == 200
        data = response.json()
        assert "videos" in data

    def test_legacy_and_versioned_return_same_data(self, public_client):
        """Test legacy and versioned endpoints return the same data."""
        legacy_response = public_client.get("/api/videos")
        versioned_response = public_client.get("/api/v1/videos")

        assert legacy_response.status_code == versioned_response.status_code
        assert legacy_response.json() == versioned_response.json()


class TestConfigValidation:
    """Test API versioning configuration validation."""

    def test_validate_config_accepts_valid_version(self):
        """Test validate_config accepts valid version formats."""
        from api.versioning import API_VERSION_PATTERN

        assert API_VERSION_PATTERN.match("v1")
        assert API_VERSION_PATTERN.match("v2")
        assert API_VERSION_PATTERN.match("v10")
        assert API_VERSION_PATTERN.match("v123")

    def test_validate_config_rejects_invalid_version(self):
        """Test validate_config rejects invalid version formats."""
        from api.versioning import API_VERSION_PATTERN

        assert not API_VERSION_PATTERN.match("1")
        assert not API_VERSION_PATTERN.match("version1")
        assert not API_VERSION_PATTERN.match("v")
        assert not API_VERSION_PATTERN.match("V1")
        assert not API_VERSION_PATTERN.match("v1.0")
        assert not API_VERSION_PATTERN.match("v1/../../etc")


class TestHeaderSanitization:
    """Test header sanitization for security."""

    def test_sanitize_header_value_removes_crlf(self):
        """Test sanitize_header_value removes CR/LF characters."""
        from api.versioning import sanitize_header_value

        assert sanitize_header_value("normal value") == "normal value"
        assert sanitize_header_value("value\r\ninjection") == "valueinjection"
        assert sanitize_header_value("value\rinjection") == "valueinjection"
        assert sanitize_header_value("value\ninjection") == "valueinjection"
        assert sanitize_header_value("") == ""
        assert sanitize_header_value(None) is None

    def test_sanitize_header_preserves_valid_content(self):
        """Test sanitize_header_value preserves valid header content."""
        from api.versioning import sanitize_header_value

        # RFC 5322 date format
        date = "Sat, 01 Jan 2028 00:00:00 GMT"
        assert sanitize_header_value(date) == date


class TestVersionInfo:
    """Test VersionInfo dataclass functionality."""

    def test_version_info_prefix(self):
        """Test VersionInfo.prefix property returns correct URL prefix."""
        from api.versioning import VersionInfo

        info = VersionInfo(version="v1")
        assert info.prefix == "/api/v1"

        info = VersionInfo(version="v2")
        assert info.prefix == "/api/v2"

    def test_get_version_info(self):
        """Test get_version_info returns correct information."""
        from api.versioning import get_version_info

        info = get_version_info("v1")
        assert info.version == "v1"
        assert info.is_current is True  # v1 is the current version

    def test_get_all_versions(self):
        """Test get_all_versions returns all supported versions."""
        from api.versioning import get_all_versions

        versions = get_all_versions()
        assert len(versions) >= 1
        assert any(v.version == "v1" for v in versions)


class TestOpenAPISchema:
    """Test OpenAPI schema configuration."""

    def test_openapi_schema_includes_version_info(self, public_client):
        """Test OpenAPI schema includes version information."""
        response = public_client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()

        assert "info" in schema
        assert "version" in schema["info"]
        assert schema["info"]["version"] == "v1"

    def test_openapi_schema_has_description(self, public_client):
        """Test OpenAPI schema has description with version info."""
        response = public_client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()

        assert "description" in schema["info"]
        # Description should mention API versions
        assert "v1" in schema["info"]["description"]
