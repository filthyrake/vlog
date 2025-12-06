"""
Tests for worker metadata and capabilities validation.

Tests the security features that prevent:
- Arbitrary large JSON blobs
- Unknown fields in capabilities/metadata
- Data exfiltration via metadata storage abuse
"""


class TestWorkerCapabilitiesValidation:
    """Tests for WorkerCapabilities schema validation."""

    def test_register_with_valid_capabilities(self, worker_client, worker_admin_headers):
        """Test registration with valid capabilities."""
        response = worker_client.post(
            "/api/worker/register",
            headers=worker_admin_headers,
            json={
                "worker_name": "test-worker",
                "worker_type": "remote",
                "capabilities": {
                    "hwaccel_enabled": True,
                    "hwaccel_type": "nvidia",
                    "gpu_name": "NVIDIA GeForce RTX 3090",
                    "supported_codecs": ["h264", "hevc"],
                    "encoders": {"h264": ["h264_nvenc", "libx264"], "hevc": ["hevc_nvenc", "libx265"]},
                    "max_concurrent_encode_sessions": 3,
                    "ffmpeg_version": "5.1.2",
                    "driver_version": "525.60.11",
                    "cuda_version": "12.0",
                },
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "worker_id" in data
        assert "api_key" in data

    def test_register_capabilities_rejects_unknown_fields(self, worker_client, worker_admin_headers):
        """Test that unknown fields in capabilities are rejected."""
        response = worker_client.post(
            "/api/worker/register",
            headers=worker_admin_headers,
            json={
                "worker_name": "test-worker",
                "worker_type": "remote",
                "capabilities": {
                    "hwaccel_enabled": True,
                    "hwaccel_type": "nvidia",
                    "unknown_field": "should be rejected",
                    "malicious_data": {"nested": "should not be allowed"},
                },
            },
        )
        # Should get 422 Unprocessable Entity due to Pydantic validation
        assert response.status_code == 422
        error_detail = response.json()["detail"]
        # Check that it mentions extra fields not permitted
        assert any("extra" in str(e).lower() or "permitted" in str(e).lower() for e in error_detail)

    def test_register_capabilities_too_large(self, worker_client, worker_admin_headers):
        """Test that oversized capabilities JSON is rejected."""
        # Create a capabilities object that will exceed 10KB when serialized
        large_encoders = {}
        for i in range(500):  # Many codec entries
            large_encoders[f"codec_{i}"] = [f"encoder_{i}_{j}" for j in range(20)]

        response = worker_client.post(
            "/api/worker/register",
            headers=worker_admin_headers,
            json={
                "worker_name": "test-worker",
                "worker_type": "remote",
                "capabilities": {"hwaccel_enabled": True, "hwaccel_type": "nvidia", "encoders": large_encoders},
            },
        )
        # Should get 400 Bad Request due to size limit
        assert response.status_code == 400
        assert "too large" in response.json()["detail"].lower()

    def test_register_capabilities_validates_string_lengths(self, worker_client, worker_admin_headers):
        """Test that string fields have length limits."""
        response = worker_client.post(
            "/api/worker/register",
            headers=worker_admin_headers,
            json={
                "worker_name": "test-worker",
                "worker_type": "remote",
                "capabilities": {
                    "hwaccel_enabled": True,
                    "hwaccel_type": "nvidia",
                    "gpu_name": "A" * 300,  # Exceeds 200 char limit
                },
            },
        )
        # Should get 422 Unprocessable Entity due to Pydantic validation
        assert response.status_code == 422

    def test_register_capabilities_validates_codec_length(self, worker_client, worker_admin_headers):
        """Test that codec names are validated for reasonable length."""
        response = worker_client.post(
            "/api/worker/register",
            headers=worker_admin_headers,
            json={
                "worker_name": "test-worker",
                "worker_type": "remote",
                "capabilities": {
                    "hwaccel_enabled": True,
                    "hwaccel_type": "nvidia",
                    "supported_codecs": ["h264", "A" * 50],  # One codec name too long
                },
            },
        )
        # Should get 422 due to validation
        assert response.status_code == 422

    def test_register_capabilities_validates_encoder_limits(self, worker_client, worker_admin_headers):
        """Test that encoder names have reasonable limits."""
        response = worker_client.post(
            "/api/worker/register",
            headers=worker_admin_headers,
            json={
                "worker_name": "test-worker",
                "worker_type": "remote",
                "capabilities": {
                    "hwaccel_enabled": True,
                    "hwaccel_type": "nvidia",
                    "encoders": {
                        "h264": ["encoder_" + "x" * 100]  # Encoder name too long
                    },
                },
            },
        )
        # Should get 422 due to validation
        assert response.status_code == 422

    def test_register_capabilities_validates_session_limits(self, worker_client, worker_admin_headers):
        """Test that max_concurrent_encode_sessions has reasonable bounds."""
        # Test value too high
        response = worker_client.post(
            "/api/worker/register",
            headers=worker_admin_headers,
            json={
                "worker_name": "test-worker",
                "worker_type": "remote",
                "capabilities": {
                    "hwaccel_enabled": True,
                    "max_concurrent_encode_sessions": 1000,  # Exceeds limit of 100
                },
            },
        )
        assert response.status_code == 422

        # Test value too low
        response = worker_client.post(
            "/api/worker/register",
            headers=worker_admin_headers,
            json={
                "worker_name": "test-worker",
                "worker_type": "remote",
                "capabilities": {
                    "hwaccel_enabled": True,
                    "max_concurrent_encode_sessions": 0,  # Below minimum of 1
                },
            },
        )
        assert response.status_code == 422


class TestWorkerMetadataValidation:
    """Tests for WorkerMetadata schema validation."""

    def test_register_with_valid_metadata(self, worker_client, worker_admin_headers):
        """Test registration with valid Kubernetes metadata."""
        response = worker_client.post(
            "/api/worker/register",
            headers=worker_admin_headers,
            json={
                "worker_name": "test-worker",
                "worker_type": "remote",
                "metadata": {
                    "pod_name": "vlog-worker-abc123",
                    "pod_namespace": "video-processing",
                    "pod_uid": "12345678-1234-1234-1234-123456789abc",
                    "node_name": "worker-node-01",
                    "container_name": "transcoder",
                    "container_image": "ghcr.io/filthyrake/vlog-worker:latest",
                    "cloud_provider": "aws",
                    "region": "us-west-2",
                    "availability_zone": "us-west-2a",
                    "labels": {"app": "vlog", "tier": "worker"},
                },
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "worker_id" in data

    def test_register_metadata_rejects_unknown_fields(self, worker_client, worker_admin_headers):
        """Test that unknown fields in metadata are rejected."""
        response = worker_client.post(
            "/api/worker/register",
            headers=worker_admin_headers,
            json={
                "worker_name": "test-worker",
                "worker_type": "remote",
                "metadata": {
                    "pod_name": "test-pod",
                    "secret_data": "should not be allowed",
                    "exfiltrated_info": {"password": "secret"},
                },
            },
        )
        # Should get 422 due to extra fields
        assert response.status_code == 422
        error_detail = response.json()["detail"]
        assert any("extra" in str(e).lower() or "permitted" in str(e).lower() for e in error_detail)

    def test_register_metadata_too_large(self, worker_client, worker_admin_headers):
        """Test that oversized metadata JSON is rejected."""
        # Create metadata that exceeds 10KB
        # Use valid labels (under 50 count) but with large values to exceed size
        large_labels = {f"label_{i}": "x" * 400 for i in range(25)}

        response = worker_client.post(
            "/api/worker/register",
            headers=worker_admin_headers,
            json={
                "worker_name": "test-worker",
                "worker_type": "remote",
                "metadata": {"pod_name": "test-pod", "labels": large_labels},
            },
        )
        # Should get 400 due to size limit (or 422 if labels validation catches it first)
        assert response.status_code in [400, 422]
        # Either way, it should be rejected
        assert "detail" in response.json()

    def test_register_metadata_validates_label_limits(self, worker_client, worker_admin_headers):
        """Test that labels dict has reasonable size limits."""
        # Too many labels
        response = worker_client.post(
            "/api/worker/register",
            headers=worker_admin_headers,
            json={
                "worker_name": "test-worker",
                "worker_type": "remote",
                "metadata": {
                    "pod_name": "test-pod",
                    "labels": {f"label_{i}": "value" for i in range(100)},  # Exceeds 50
                },
            },
        )
        assert response.status_code == 422

    def test_register_metadata_validates_field_lengths(self, worker_client, worker_admin_headers):
        """Test that metadata fields have length limits."""
        response = worker_client.post(
            "/api/worker/register",
            headers=worker_admin_headers,
            json={
                "worker_name": "test-worker",
                "worker_type": "remote",
                "metadata": {
                    "pod_name": "x" * 300,  # Exceeds 253 char limit
                },
            },
        )
        assert response.status_code == 422


class TestHeartbeatMetadataValidation:
    """Tests for heartbeat metadata validation."""

    def test_heartbeat_with_valid_capabilities(self, worker_client, registered_worker):
        """Test heartbeat with valid capabilities in metadata."""
        response = worker_client.post(
            "/api/worker/heartbeat",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
            json={
                "status": "idle",
                "metadata": {
                    "capabilities": {
                        "hwaccel_enabled": True,
                        "hwaccel_type": "nvidia",
                        "gpu_name": "NVIDIA GeForce RTX 4090",
                        "supported_codecs": ["h264", "hevc", "av1"],
                        "encoders": {"h264": ["h264_nvenc"], "hevc": ["hevc_nvenc"], "av1": ["av1_nvenc"]},
                        "max_concurrent_encode_sessions": 5,
                    }
                },
            },
        )
        assert response.status_code == 200

    def test_heartbeat_metadata_rejects_invalid_capabilities(self, worker_client, registered_worker):
        """Test that heartbeat rejects invalid capabilities structure."""
        response = worker_client.post(
            "/api/worker/heartbeat",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
            json={
                "status": "idle",
                "metadata": {
                    "capabilities": {
                        "hwaccel_enabled": True,
                        "unknown_field": "not allowed",  # Should be rejected
                    }
                },
            },
        )
        assert response.status_code == 422

    def test_heartbeat_metadata_too_large(self, worker_client, registered_worker):
        """Test that heartbeat rejects oversized metadata."""
        # Create oversized metadata
        large_data = {"key_" + str(i): "x" * 100 for i in range(200)}

        response = worker_client.post(
            "/api/worker/heartbeat",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
            json={"status": "idle", "metadata": large_data},
        )
        assert response.status_code == 400
        assert "too large" in response.json()["detail"].lower()

    def test_heartbeat_without_metadata(self, worker_client, registered_worker):
        """Test that heartbeat works without metadata (optional field)."""
        response = worker_client.post(
            "/api/worker/heartbeat",
            headers={"X-Worker-API-Key": registered_worker["api_key"]},
            json={"status": "active"},
        )
        assert response.status_code == 200
