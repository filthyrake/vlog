# VLog Kubernetes Deployment

Kubernetes manifests for deploying remote transcoding workers.

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
- `secret.yaml` - Template for API key secret
- `worker-deployment.yaml` - Worker deployment with resource limits
- `worker-hpa.yaml` - Horizontal Pod Autoscaler for auto-scaling

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

Workers are CPU-intensive during transcoding. Adjust resources in `worker-deployment.yaml`:

- **Small videos (720p)**: 1 CPU, 2GB RAM
- **HD videos (1080p)**: 2 CPU, 4GB RAM
- **4K videos (2160p)**: 4 CPU, 8GB RAM

The work directory (`emptyDir`) should be sized to hold source + output:
- Small: 10GB
- Large/4K: 50GB+
