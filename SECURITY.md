# Security

Do not open public issues containing tokens, API keys, session files, customer messages or databases. Report vulnerabilities privately to the repository owner. Revoke and rotate any accidentally exposed credentials immediately; deleting a Git commit alone does not revoke a secret.

Runtime data, logs, local environments, generated executables and encrypted or unencrypted backups must not be tracked. Use mock credentials in tests. Keep dashboard access local unless separately secured and reviewed. Third-party messaging adapters must obey service permissions and terms.

A public GitHub repository is readable by anyone. No license is inferred from this repository's public visibility. Windows builds and live provider rendering require independent verification.
