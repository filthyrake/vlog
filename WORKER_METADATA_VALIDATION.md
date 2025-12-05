# Worker Metadata Validation - Security Enhancement

## Summary

This PR adds comprehensive input validation for worker metadata and capabilities JSON to prevent:
- Storage of arbitrarily large JSON blobs
- Data exfiltration via metadata fields
- Unknown/malicious fields in worker data
- Storage abuse

## Changes Made

### 1. Enhanced Schema Models (`api/worker_schemas.py`)

#### WorkerCapabilities Model
- Added `model_config = ConfigDict(extra="forbid")` to reject unknown fields
- Added field length limits:
  - `hwaccel_type`: max 20 chars
  - `gpu_name`: max 200 chars
  - `ffmpeg_version`, `driver_version`, `cuda_version`: max 100 chars
  - `vaapi_device`: max 200 chars
- Added integer bounds: `max_concurrent_encode_sessions` (1-100)
- Added custom validators for:
  - Codec names (max 20 chars each)
  - Encoder names (max 50 chars each)
  - Encoder list length (max 20 per codec)

#### WorkerMetadata Model (New)
- Structured schema for Kubernetes pod information
- Added `model_config = ConfigDict(extra="forbid")` to reject unknown fields
- Fields for pod metadata:
  - `pod_name`, `pod_namespace`, `pod_uid`, `node_name`
  - `container_name`, `container_image`
  - `cloud_provider`, `region`, `availability_zone`
  - `labels` dict (max 50 labels, validated lengths)
- All string fields have appropriate max_length constraints

### 2. Updated Registration Endpoint (`api/worker_api.py`)

```python
@app.post("/api/worker/register")
async def register_worker(data: WorkerRegisterRequest):
    # Validate and serialize capabilities with size limit
    if data.capabilities:
        capabilities_json = json.dumps(data.capabilities.model_dump())
        if len(capabilities_json) > 10000:  # 10KB limit
            raise HTTPException(400, "Capabilities JSON too large (max 10KB)")
    
    # Same validation for metadata
    if data.metadata:
        metadata_json = json.dumps(data.metadata.model_dump())
        if len(metadata_json) > 10000:  # 10KB limit
            raise HTTPException(400, "Metadata JSON too large (max 10KB)")
```

### 3. Updated Heartbeat Endpoint (`api/worker_api.py`)

```python
@app.post("/api/worker/heartbeat")
async def worker_heartbeat(data: HeartbeatRequest, worker: dict):
    # Validate metadata with size limit
    if data.metadata:
        metadata_json = json.dumps(data.metadata)
        if len(metadata_json) > 10000:  # 10KB limit
            raise HTTPException(400, "Metadata JSON too large (max 10KB)")
```

Added validator to HeartbeatRequest to check capabilities structure if present in metadata.

### 4. Comprehensive Test Suite (`tests/test_worker_metadata_validation.py`)

Added 16 focused tests covering:
- ✅ Valid capabilities acceptance
- ✅ Unknown field rejection (extra="forbid")
- ✅ Oversized JSON rejection (10KB limit)
- ✅ String length validation
- ✅ Codec and encoder name validation
- ✅ Integer bounds validation
- ✅ Valid metadata acceptance
- ✅ Metadata unknown field rejection
- ✅ Labels limit validation
- ✅ Heartbeat with valid capabilities
- ✅ Heartbeat metadata validation
- ✅ Heartbeat metadata size limits

All tests pass: **16/16 ✓**

## Security Benefits

1. **Prevents Data Exfiltration**: Unknown fields are rejected, preventing workers from storing arbitrary data
2. **Prevents Storage Abuse**: 10KB limit prevents workers from storing large blobs
3. **Input Sanitization**: All fields have length and format constraints
4. **Schema Enforcement**: Pydantic validation ensures type safety and structure

## Backward Compatibility

- Existing workers sending valid capabilities/metadata continue to work
- Invalid data that was previously accepted will now be rejected (422/400 errors)
- This is a security enhancement, so breaking invalid behavior is intentional

## Testing

```bash
# Run new validation tests
VLOG_TEST_MODE=1 pytest tests/test_worker_metadata_validation.py -v

# Verify no regressions in other tests
VLOG_TEST_MODE=1 pytest tests/test_public_api.py tests/test_admin_api.py -v
```

## Linting

```bash
ruff check api/worker_schemas.py api/worker_api.py tests/test_worker_metadata_validation.py
# All checks passed! ✓
```

## Example Usage

### Valid Registration
```python
POST /api/worker/register
{
  "worker_name": "gpu-worker-01",
  "worker_type": "remote",
  "capabilities": {
    "hwaccel_enabled": true,
    "hwaccel_type": "nvidia",
    "gpu_name": "NVIDIA RTX 4090",
    "supported_codecs": ["h264", "hevc", "av1"],
    "encoders": {
      "h264": ["h264_nvenc", "libx264"],
      "hevc": ["hevc_nvenc", "libx265"],
      "av1": ["av1_nvenc"]
    },
    "max_concurrent_encode_sessions": 5
  },
  "metadata": {
    "pod_name": "vlog-worker-abc123",
    "pod_namespace": "video-processing",
    "node_name": "worker-node-01",
    "labels": {"app": "vlog", "tier": "worker"}
  }
}
```

### Rejected - Unknown Field
```python
POST /api/worker/register
{
  "worker_name": "test",
  "capabilities": {
    "hwaccel_enabled": true,
    "malicious_field": "data"  # ❌ Rejected with 422
  }
}
```

### Rejected - Too Large
```python
POST /api/worker/register
{
  "worker_name": "test",
  "metadata": {
    "labels": {f"key_{i}": "value" for i in range(1000)}  # ❌ Rejected with 400
  }
}
```

## Notes

- The existing `tests/test_worker_api.py` file had pre-existing syntax errors (IndentationError at line 426) that were present before this PR
- Those errors are unrelated to these changes and should be addressed separately
- All new tests pass, and existing valid tests (public API, admin API) continue to pass
