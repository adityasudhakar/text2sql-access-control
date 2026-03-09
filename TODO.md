# To-Do List

## Pending

1. **Handle empty config.json** - Treat empty file as `{}` in `server.py`'s `_get_config()`. Currently crashes if file is empty (not valid JSON).

2. **Fix session persistence** - Agent doesn't remember previous queries despite `session_id` code in `cli.py`. Need to investigate:
   - Is `session_id` being captured correctly from `SystemMessage`?
   - Is the Agent SDK's `resume` parameter working?
   - Are sessions expiring between queries?

## Completed

- [x] Fix duplicate output in CLI (removed AssistantMessage printing, only print ResultMessage)
- [x] Fix MCP server empty responses (added `**os.environ` to subprocess env)
- [x] Add Q-SQL logging guidance to system prompt (agent logs to context.md via `write_context`)
- [x] Handle empty/invalid config.json gracefully in `cli.py`
