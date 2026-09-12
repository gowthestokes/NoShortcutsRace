# Workspace instructions

## Browser availability

- Browser control is unavailable in this admin-managed environment. Do not attempt to connect to, inspect, or automate a browser session.
- For requests involving subscriber-only publisher content, do not suggest connecting a browser or signing in through Computer use. Work from public RSS feeds, public sources, or article text/URLs the user provides, and clearly state any access limitation.

## Code quality

- Write focused unit tests for new behavior; tests must not make external API calls.
- Prefer small, cohesive modules and classes with clear names over duplicated or clever code.
- Keep comments sparse. Add one only for a non-obvious constraint, safety decision, or trade-off that names and tests cannot convey.
- Keep public CLI help, generated-layer descriptions, and errors concise and useful to a human planning the race.
