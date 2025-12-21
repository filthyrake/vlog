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

# 7. (Optional) Enable auto-scaling
kubectl apply -f k8s/worker-hpa.yaml
```

## Files

- `namespace.yaml` - Creates the `vlog` namespace
- `configmap.yaml` - Worker configuration (API URL, intervals)
- `worker-deployment.yaml` - CPU-only worker deployment
- `worker-deployment-nvidia.yaml` - NVIDIA GPU worker deployment (NVENC)
- `worker-deployment-intel.yaml` - Intel Arc/QuickSync worker deployment (VAAPI)
- `worker-hpa.yaml` - Horizontal Pod Autoscaler for auto-scaling
- `cleanup-cronjob.yaml` - CronJob for cleaning up stale transcoding jobs

## Secrets Management

**Important:** Kubernetes secrets should never be committed to version control.

### Creating Secrets via kubectl (Recommended)

After registering a worker, create the secret directly:

```bash
# Register a worker to get an API key
vlog worker register --name "k8s-worker"
# Or via curl:
curl -X POST http://your-vlog-server:9002/api/worker/register \
  -H "Content-Type: application/json" \
  -d '{"name": "k8s-worker"}'

# Create the secret (replace with your actual API key)
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

## Monitoring

```bash
# View worker pods
kubectl get pods -n vlog

# View worker logs
kubectl logs -n vlog -l app.kubernetes.io/component=worker -f

# View worker status
curl http://your-vlog-server:9002/api/workers
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
