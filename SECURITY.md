# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| Latest  | :white_check_mark: |

## Reporting a Vulnerability

We take security vulnerabilities seriously. If you discover a security issue, please report it responsibly.

### How to Report

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, please send an email to **damen@knightspeed.com** with:

1. **Description** of the vulnerability
2. **Steps to reproduce** the issue
3. **Potential impact** of the vulnerability
4. **Suggested fix** (if you have one)

### What to Expect

- **Acknowledgment**: We will acknowledge receipt of your report within 48 hours
- **Updates**: We will keep you informed of our progress
- **Resolution**: We aim to resolve critical issues within 7 days
- **Credit**: We will credit you in the release notes (unless you prefer to remain anonymous)

### Scope

The following are in scope for security reports:

- Authentication/authorization bypasses
- SQL injection
- Cross-site scripting (XSS)
- Remote code execution
- Path traversal vulnerabilities
- Sensitive data exposure
- Server-side request forgery (SSRF)

### Out of Scope

- Issues requiring physical access to the server
- Social engineering attacks
- Denial of service attacks
- Issues in dependencies (please report to the upstream project)
- Issues only exploitable with user interaction in unlikely scenarios

## Security Best Practices

When deploying VLog, please follow these recommendations:

### Network Security

- **Admin API (port 9001)**: Should only be accessible from trusted internal networks
- **Worker API (port 9002)**: Should only be accessible to authorized workers
- **Public API (port 9000)**: Can be exposed to the internet behind a reverse proxy

### Authentication

- Worker API keys are generated securely and stored as SHA-256 hashes
- Rotate API keys periodically
- Revoke keys for workers that are no longer in use

### HTTPS

- Always use HTTPS in production
- Deploy behind a reverse proxy (nginx, Caddy) with valid SSL certificates
- Enable HSTS headers

### Rate Limiting

- Rate limiting is enabled by default
- For multi-instance deployments, use Redis storage for shared rate limit state
- Adjust limits based on your expected traffic

### Updates

- Keep dependencies updated
- Monitor security advisories for Python packages
- Subscribe to GitHub security alerts

---

## Security Features

### CI/CD Security Scanning

VLog's GitHub Actions pipeline includes automated security scanning:

| Tool | Purpose | When |
|------|---------|------|
| **Trivy** | Container image vulnerability scanning | On Docker builds |
| **pip-audit** | Python dependency vulnerability detection | On every PR |
| **Bandit** | Python static security analysis | On every PR |

Scans run automatically on pull requests and block merging if critical vulnerabilities are found.

### Container Security

Production container images include:

- **Multi-stage builds:** Smaller attack surface, no build tools in production
- **Non-root user:** Containers run as UID 1000, not root
- **Read-only filesystem:** Prevents runtime modifications
- **Dropped capabilities:** All Linux capabilities dropped
- **Seccomp profile:** RuntimeDefault seccomp profile applied

Example security context:
```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 1000
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  seccompProfile:
    type: RuntimeDefault
  capabilities:
    drop: ["ALL"]
```

### Kubernetes Security

For Kubernetes deployments:

- **NetworkPolicy:** Restricts pod-to-pod communication
- **PodDisruptionBudgets:** Ensures availability during updates
- **Resource limits:** Prevents resource exhaustion
- **Pinned image versions:** No `latest` tags in production
- **Secrets management:** Secrets via kubectl, never in git

### Audit Logging

VLog maintains audit logs for security-relevant operations:

- Admin authentication (login/logout)
- Video uploads and deletions
- Settings changes
- Worker registration and revocation

Audit logs include:
- Timestamp
- Event type
- User/IP information
- Operation details

Configure via:
```bash
VLOG_AUDIT_LOG_ENABLED=true
VLOG_AUDIT_LOG_PATH=/var/log/vlog/audit.log
VLOG_AUDIT_LOG_MAX_BYTES=10485760  # 10 MB rotation
```

### Input Validation

- File uploads validated for type and size
- SQL injection prevented via parameterized queries (SQLAlchemy)
- XSS prevented via Content Security Policy headers
- Path traversal prevented in file operations

### Secret Management

- API secrets stored as SHA-256 hashes
- Session tokens use cryptographically secure generation
- HTTP-only cookies for browser sessions
- Environment variables for sensitive configuration

---

## Disclosure Policy

- We will coordinate disclosure timing with the reporter
- We aim to release fixes before public disclosure
- Security fixes will be released as soon as possible
- We will publish security advisories for significant vulnerabilities
