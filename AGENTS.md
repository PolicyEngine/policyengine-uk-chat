# Agent Instructions

These instructions apply repository-wide.

Canonical AI-facing engineering guidance lives under `docs/engineering/skills/`.
Use those files as the source of truth across Codex, Claude, Copilot, and other
AI tools.

When adding, moving, or reviewing tests, read
`docs/engineering/skills/testing.md`.

When changing the chat model pathway, system prompts, tool definitions, or
calculation boundaries, read `docs/engineering/skills/uk-chat-runtime.md`.

Keep this file thin. Do not duplicate durable engineering guidance here; update
the canonical docs first, then adjust this adapter only when an entry point
needs to point at new guidance.
