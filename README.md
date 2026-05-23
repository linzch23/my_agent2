# my_agent2

`my_agent2` is a compact, general-purpose Python agent inspired by
`TheSyart/claude-agent-examples`, but with the teaching-specific roleplay removed
and the project packaged for `uv`.

It includes:

- a provider adapter for DeepSeek, Anthropic, and OpenAI-compatible chat APIs
- streaming assistant text in the main CLI conversation
- workspace file tools: `read_file`, `write_file`, `edit_file`, `glob`, `grep`
- command and web fetch tools
- persistent conversation logs and lightweight long-term memory
- automatic history compression when context grows large
- a todo tool for explicit task planning
- loadable skills from `skills/{name}/SKILL.md`
- generic subagents for isolated research, analysis, coding, and review
- persistent multi-agent team collaboration with named teammates and inboxes
- parallel execution for read-only tools and independent subagent calls

## Quick Start

```bash
uv sync
cp .env.example .env
# edit .env and set DEEPSEEK_API_KEY

uv run my-agent2
```

On this Mac, the most reliable local command is:

```bash
./run.sh
```

`run.sh` calls `uv --directory ... run --no-editable my-agent2`, which avoids
terminal current-directory issues and editable-install import issues.

Default `.env` settings use DeepSeek:

```bash
MY_AGENT_PROVIDER=deepseek
MY_AGENT_MODEL=deepseek-chat
DEEPSEEK_BASE_URL=https://api.deepseek.com
MY_AGENT_MAX_CONTEXT_TOKENS=64000
MY_AGENT_COMPACT_THRESHOLD=0.7
MY_AGENT_COMPACT_KEEP_MESSAGES=8
```

Useful commands inside the CLI:

- `/help` - show commands
- `/tools` - list registered tools
- `/todos` - show the current todo list
- `/memory` - show long-term memory
- `/compact` - force conversation history compression
- `/team` - show persistent teammate status
- `/inbox` - read and clear the lead inbox
- `/exit` - quit

Compression is implemented in `src/my_agent2/compactor.py`. It is called by
`AgentRunner` after a complete assistant turn. Summaries are written to
`memory/compactions.md`, durable context is appended to `memory/MEMORY.md`, and
the live `history` is replaced by one summary message plus the latest safe
message window.

## Project Layout

```text
src/my_agent2/
  cli.py              CLI entry point
  loop.py             application wiring
  runner.py           model/tool execution loop
  compactor.py        history compression
  team.py             persistent teammate manager and inbox bus
  memory.py           logs and long-term notes
  skills.py           skill loader
  context.py          system prompt builder
  tools/              tool implementations
  subagents/          generic subagent registry
templates/
  system.md           main agent prompt
  subagents/*.md      role prompts
skills/
  summarize/SKILL.md  example skill
```

## Notes

By default, file tools are scoped to the configured workspace. Relative paths are
resolved from `MY_AGENT_WORKSPACE` or the current directory.
