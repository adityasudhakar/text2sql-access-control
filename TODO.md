# To-Do List

## Pending

_(none)_

## Completed

- [x] Fix duplicate output in CLI (removed AssistantMessage printing, only print ResultMessage)
- [x] Fix MCP server empty responses (added `**os.environ` to subprocess env)
- [x] Add Q-SQL logging guidance to system prompt (agent logs to context.md via `write_context`)
- [x] Handle empty/invalid config.json gracefully in `cli.py`
- [x] Handle empty config.json in `server.py`'s `_get_config()` - now treats empty/invalid file as `{}`
- [x] Fix session persistence - switched from `query()` to `ClaudeSDKClient` which maintains conversation state across queries
