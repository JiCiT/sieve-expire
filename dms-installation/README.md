# Persistent DMS startup integration

The public manager chains preserved user-patches and stages the offline payload.
Normal, fallback and neither selection are validated at fresh/recreated startup.
See [installation](../docs/INSTALLATION.md) and [architecture](../docs/ARCHITECTURE.md).
The legacy package.py format remains metadata-only; only an authenticated public
format with GateD may select maintenance. No compiler or download runs at startup.
