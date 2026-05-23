# Pi Attribution

The tree session design in `my_agent2.tree_session` is inspired by the Pi
coding-agent session and compaction model:

- https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/sessions.md
- https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/session-format.md
- https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/session-manager.ts
- https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/compaction.md
- https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/compaction/branch-summarization.ts
- https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/compaction/compaction.ts
- https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/compaction/utils.ts

Pi is MIT licensed:

```text
MIT License
Copyright (c) 2025 Mario Zechner

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

This repository does not copy Pi's TUI or full agent runtime. It adapts only
the minimal tree-session ideas needed by `my_agent2`: append-only entries,
`id`/`parentId`, active leaf branch context, labels, branch summaries, and
compaction entries.
