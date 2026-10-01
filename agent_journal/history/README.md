# history/ — superseded files

Kept for reference only. Nothing here is read by any script, and nothing
should be copied back into use without reading the note first.

| File | What it is |
|---|---|
| `docker-compose.yml.BEFORE` | The original `~/n8n/docker-compose.yml`, containing only n8n and a placeholder encryption key. Superseded by the current four-service version. |

## Why the encryption key in it is dangerous to restore

It reads `N8N_ENCRYPTION_KEY=change-me-to-a-long-random-string`. n8n persists
the key it saw on first boot in **plaintext** at `/home/node/.n8n/config`
inside the volume, and refuses to start if the env var does not match it.

That placeholder is what is currently stored. Restoring this file wholesale
would put the placeholder back and, at the same time, delete the current
service definitions. See JOURNAL.md §9 for the full incident, including the
occasion when replacing that key stopped n8n from starting at all.
