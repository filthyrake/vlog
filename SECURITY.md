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

## Disclosure Policy

- We will coordinate disclosure timing with the reporter
- We aim to release fixes before public disclosure
- Security fixes will be released as soon as possible
- We will publish security advisories for significant vulnerabilities
