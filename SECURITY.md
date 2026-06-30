# Security Policy

## Reporting

Please do not open a public issue for vulnerabilities or leaked credentials.

Email the maintainer or use GitHub private vulnerability reporting if enabled. Include:

- affected commit or release
- reproduction steps
- expected impact
- suggested fix, if known

## Secrets

Never commit real `.env`, API keys, database URLs with passwords, private keys, or copied request headers.

Before making the repository public, run a secret scan against the working tree and Git history. If a real key was ever committed, revoke and rotate it even if the current tree has been cleaned.

## Supported Branch

Security fixes target `main`.

## Notes

Aria Review (codename BiblioCN) is a research/demo system. The RunLog hash chain proves event self-consistency, not tamper-proof external notarization. Use external anchoring or signatures for stronger integrity requirements.
