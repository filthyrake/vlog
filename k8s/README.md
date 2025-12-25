# VLog Kubernetes Deployment

Kubernetes manifests for deploying remote transcoding workers.

## Container Images

The GPU worker container is based on **Rocky Linux 10** with:
- FFmpeg 7.1.2 from RPM Fusion (includes nvenc, vaapi, qsv encoders)
- intel-media-driver 25.2.6 (required for Intel Battlemage/Arc B580 support)
- Python 3.12

Image tags:
- `vlog-worker-gpu:rocky10` - Rocky Linux 10 based GPU worker (recommended)
- `vlog-worker-gpu:latest` - Latest stable release

## Prerequisites

1. A running Kubernetes cluster (k3s, k8s, etc.)
2. VLog Worker API running and accessible from the cluster
3. Container registry for worker images

## Quick Start

```bash
# 1. Build and push the worker image
docker build -f Dockerfile.worker -t your-registry/vlog-worker:latest .
docker push your-registry/vlog-worker:latest

# 2. Register a worker to get an API key
curl -X POST http://your-vlog-server:9002/api/worker/register

# 3. Create the namespace
kubectl apply -f k8s/namespace.yaml

# 4. Update configmap.yaml with your Worker API URL
# Edit k8s/configmap.yaml and set VLOG_WORKER_API_URL

# 5. Create the secret with your API key
kubectl create secret generic vlog-worker-credentials \
  --namespace vlog \
  --from-literal=VLOG_WORKER_API_KEY=<your-api-key>

# 6. Deploy workers
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/worker-deployment.yaml

# 7. (Optional) Enable PodDisruptionBudget for high availability
kubectl apply -f k8s/worker-pdb.yaml

# 8. (Optional) Enable auto-scaling
kubectl apply -f k8s/worker-hpa.yaml
```

## Files

- `namespace.yaml` - Creates the `vlog` namespace
- `configmap.yaml` - Worker configuration (API URL, intervals)
- `worker-deployment.yaml` - CPU-only worker deployment
- `worker-deployment-nvidia.yaml` - NVIDIA GPU worker deployment (NVENC)
- `worker-deployment-intel.yaml` - Intel Arc/QuickSync worker deployment (VAAPI)
- `worker-hpa.yaml` - Horizontal Pod Autoscaler for auto-scaling
- `worker-pdb.yaml` - PodDisruptionBudget for CPU workers (ensures minimum availability during disruptions)
- `worker-pdb-nvidia.yaml` - PodDisruptionBudget for NVIDIA GPU workers
- `worker-pdb-intel.yaml` - PodDisruptionBudget for Intel GPU workers
- `cleanup-cronjob.yaml` - CronJob for cleaning up stale transcoding jobs
- `networkpolicy.yaml` - NetworkPolicy restricting worker pod network access

## PodDisruptionBudgets (High Availability)

PodDisruptionBudgets (PDBs) protect worker pods from being evicted simultaneously during voluntary disruptions. This ensures transcoding jobs aren't interrupted during:

- **Node drains** - `kubectl drain` for maintenance
- **Cluster autoscaling** - When downscaling removes nodes
- **Node upgrades** - Rolling updates of cluster nodes
- **Other voluntary disruptions** - Planned maintenance events

Each worker type has a PDB configured with `minAvailable: 1`, ensuring at least one pod remains running during disruptions.

### Applying PodDisruptionBudgets

```bash
# Apply PDB for CPU workers
kubectl apply -f k8s/worker-pdb.yaml

# Apply PDB for NVIDIA GPU workers (if using GPU workers)
kubectl apply -f k8s/worker-pdb-nvidia.yaml

# Apply PDB for Intel GPU workers (if using GPU workers)
kubectl apply -f k8s/worker-pdb-intel.yaml

# Verify PDBs are active
kubectl get poddisruptionbudget -n vlog
```

### Important Notes

- **PDBs only protect against voluntary disruptions**, not involuntary ones (node failures, OOM kills, etc.)
- **Requires at least 2 replicas** - With only 1 replica, the PDB cannot be satisfied during disruptions
- **Adjust `minAvailable`** - For production, consider `minAvailable: 2` or use `maxUnavailable: 1` instead
- **GPU workers** - PDBs prevent GPU resource contention during rolling updates

### Tuning for Your Workload

If you have critical transcoding requirements, consider:

```yaml
# Option 1: Guarantee minimum capacity (50% of replicas)
spec:
  minAvailable: 50%

# Option 2: Limit maximum disruption (only 1 pod at a time)
spec:
  maxUnavailable: 1
```

## Network Security

The `networkpolicy.yaml` restricts network access for worker pods to limit the blast radius if a pod is compromised. Workers only need:

1. **Egress to Worker API** (port 9002) - For job claiming, progress updates, file transfers
2. **Egress to DNS** (port 53) - For hostname resolution
3. **Optionally, egress to Redis** (port 6379) - For instant job dispatch

All ingress is denied since workers don't need incoming connections.

### Prerequisites

NetworkPolicy requires a CNI that supports it. Common options:
- **Calico** - Full NetworkPolicy support
- **Cilium** - Full support with enhanced features
- **Weave Net** - Full support

**Note**: Default k3s/k8s networking does NOT enforce NetworkPolicy. Verify your CNI supports it before relying on this policy.

### Configuration Required

Before applying the policy, you must configure the Worker API egress rule. Edit `networkpolicy.yaml` and uncomment one of the options:

**Option A: External Worker API** - If your Worker API runs outside the cluster:
```yaml
- to:
    - ipBlock:
        cidr: 192.168.1.100/32  # Replace with your API server's IP
  ports:
    - protocol: TCP
      port: 9002
```

**Option B: In-cluster Worker API** - If the API is deployed as a Kubernetes service:
```yaml
- to:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: vlog
      podSelector:
        matchLabels:
          app.kubernetes.io/name: vlog
          app.kubernetes.io/component: worker-api
  ports:
    - protocol: TCP
      port: 9002
```

### Applying the NetworkPolicy

```bash
# Edit the policy to configure Worker API egress
vim k8s/networkpolicy.yaml

# Apply the network policy
kubectl apply -f k8s/networkpolicy.yaml

# Verify the policy is active
kubectl get networkpolicy -n vlog
```

### Optional: Redis Egress

If using Redis for instant job dispatch, uncomment one of the Redis egress options in the policy file and configure the appropriate CIDR or pod selector for your Redis deployment.

## Secrets Management

**Important:** Kubernetes secrets should never be committed to version control.

### Creating Secrets via kubectl (Recommended)

After registering a worker, create the secret directly:

```bash
# Register a worker to get an API key
# Note: VLOG_WORKER_ADMIN_SECRET must be set in your environment
vlog worker register --name "k8s-worker"

# Or via curl (include admin secret header):
curl -X POST http://your-vlog-server:9002/api/worker/register \
  -H "Content-Type: application/json" \
  -H "X-Admin-Secret: $VLOG_WORKER_ADMIN_SECRET" \
  -d '{"worker_name": "k8s-worker"}'

# Create the secret (replace with your actual API key from registration response)
kubectl create secret generic vlog-worker-credentials \
  --namespace vlog \
  --from-literal=VLOG_WORKER_API_KEY="your-actual-api-key"
```

### Updating Secrets

To update an existing secret:

```bash
kubectl delete secret vlog-worker-credentials -n vlog
kubectl create secret generic vlog-worker-credentials \
  --namespace vlog \
  --from-literal=VLOG_WORKER_API_KEY="new-api-key"
```

### External Secrets Management (Production)

For production environments, consider using external secrets management:

- **[Sealed Secrets](https://github.com/bitnami-labs/sealed-secrets)** - Encrypt secrets that can be safely committed to Git
- **[External Secrets Operator](https://external-secrets.io/)** - Sync secrets from AWS Secrets Manager, HashiCorp Vault, etc.
- **[HashiCorp Vault](https://www.vaultproject.io/)** - Centralized secrets management with dynamic credentials

## GPU-Accelerated Workers

For faster transcoding, deploy GPU-enabled workers:

### NVIDIA GPU Workers

Prerequisites:
- [NVIDIA GPU Operator](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/getting-started.html) installed
- Nodes with NVIDIA GPUs labeled `nvidia.com/gpu.present=true`
- NVIDIA RuntimeClass configured (check with `kubectl get runtimeclass`)

```bash
# Build GPU-enabled image
docker build -f Dockerfile.worker.gpu -t your-registry/vlog-worker-gpu:latest .
docker push your-registry/vlog-worker-gpu:latest

# For k3s: Import image directly to containerd
docker save vlog-worker-gpu:latest | ssh user@node 'sudo k3s ctr images import -'

# Deploy NVIDIA GPU workers
kubectl apply -f k8s/worker-deployment-nvidia.yaml

# (Optional) Apply PodDisruptionBudget for NVIDIA workers
kubectl apply -f k8s/worker-pdb-nvidia.yaml
```

**Important**: The deployment uses `runtimeClassName: nvidia` which is required for GPU access. If your cluster uses a different runtime class name, update the deployment accordingly.

Supported encoders: `h264_nvenc`, `hevc_nvenc`, `av1_nvenc` (RTX 40 series only)

**Note**: Consumer NVIDIA GPUs have concurrent encode limits:
- RTX 4090/4080/4070: 5 sessions
- RTX 3090/3080/3070: 3 sessions
- Datacenter GPUs (A100, T4, etc.): Unlimited

### Intel Arc/QuickSync Workers

Prerequisites:
- [Intel GPU Device Plugin](https://github.com/intel/intel-device-plugins-for-kubernetes) installed
- Nodes with Intel GPUs labeled `intel.feature.node.kubernetes.io/gpu=true`
- For Battlemage GPUs (Arc B580): Use the Rocky Linux 10 container image (requires intel-media-driver 25.x)

```bash
# Deploy Intel GPU workers
kubectl apply -f k8s/worker-deployment-intel.yaml

# (Optional) Apply PodDisruptionBudget for Intel workers
kubectl apply -f k8s/worker-pdb-intel.yaml
```

Supported encoders: `h264_vaapi`, `hevc_vaapi`, `av1_vaapi`

Intel Arc GPUs have excellent AV1 encoding quality and support:
- **Battlemage (B580)**: Requires intel-media-driver 25.x (Rocky Linux 10 image)
- **Alchemist (A770, A380)**: Works with intel-media-driver 23.x+

## Scaling

Manual scaling:
```bash
kubectl scale deployment vlog-worker --namespace vlog --replicas=5
```

Auto-scaling (requires metrics-server):
```bash
kubectl apply -f k8s/worker-hpa.yaml
```

## Health Probes

Workers include an HTTP health server for Kubernetes liveness and readiness probes.

### Endpoints

| Endpoint | Purpose | Success Criteria |
|----------|---------|------------------|
| `GET /health` | Liveness probe | Process is running |
| `GET /ready` | Readiness probe | FFmpeg available AND API connected |
| `GET /` | Info endpoint | Returns service info and API URL |

### Configuration

The health server runs on port 8080 by default, configurable via `VLOG_WORKER_HEALTH_PORT`:

```yaml
# In configmap.yaml
data:
  VLOG_WORKER_HEALTH_PORT: "8080"
```

### Probe Configuration in Deployments

All worker deployment manifests include configured probes:

```yaml
livenessProbe:
  httpGet:
    path: /health
    port: 8080
  initialDelaySeconds: 10
  periodSeconds: 30
  failureThreshold: 3

readinessProbe:
  httpGet:
    path: /ready
    port: 8080
  initialDelaySeconds: 5
  periodSeconds: 10
  failureThreshold: 3
```

### Readiness Checks

The `/ready` endpoint verifies:
- **FFmpeg Available**: Checks `ffmpeg` is in PATH
- **API Connected**: Worker has connected to Worker API and sent a heartbeat

Response example:
```json
{
  "status": "ready",
  "checks": {
    "ffmpeg": true,
    "api_connected": true
  }
}
```

If any check fails, returns HTTP 503 (Service Unavailable).

## Monitoring

```bash
# View worker pods
kubectl get pods -n vlog

# View worker logs
kubectl logs -n vlog -l app.kubernetes.io/component=worker -f

# View worker status
curl http://your-vlog-server:9002/api/workers

# Check individual pod health
kubectl exec -n vlog <pod-name> -- curl -s localhost:8080/ready
```

## Resource Tuning

### CPU Workers

Workers are CPU-intensive during transcoding. Adjust resources in `worker-deployment.yaml`:

- **Small videos (720p)**: 1 CPU, 2GB RAM
- **HD videos (1080p)**: 2 CPU, 4GB RAM
- **4K videos (2160p)**: 4 CPU, 8GB RAM

### GPU Workers

GPU workers are less CPU-intensive. Adjust resources in `worker-deployment-nvidia.yaml` or `worker-deployment-intel.yaml`:

- **All resolutions**: 1-2 CPU, 4GB RAM, 1 GPU
- GPU encoding is 5-10x faster than CPU for equivalent quality

### Work Directory

The work directory (`emptyDir`) should be sized to hold source + output:
- Small: 10GB
- Large/4K: 50GB+
