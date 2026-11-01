"""Builders for Claude Code transcript entries, in the shapes Claude Code 2.1.31x writes."""


def at(ts, **e):
    return {"timestamp": ts, "entrypoint": "cli", "cwd": e.pop("cwd", "/tmp"), **e}


def said(ts, text, context=60000):
    return at(ts, type="assistant", message={"content": [{"type": "text", "text": text}],
                                             "usage": {"input_tokens": 10, "cache_read_input_tokens": context}})


def ran(ts, cmd, id="t1", cwd="/tmp", **inp):
    return at(ts, type="assistant", cwd=cwd,
              message={"content": [{"type": "tool_use", "id": id, "name": "Bash", "input": {"command": cmd, **inp}}]})


def launched(ts, what, id="a1"):
    return at(ts, type="assistant", message={"content": [{"type": "tool_use", "id": id, "name": "Agent",
                                                         "input": {"description": what, "prompt": "..."}}]})


def result(ts, id="t1", text="ok", error=False):
    return at(ts, type="user", message={"content": [{"type": "tool_result", "tool_use_id": id, "content": text,
                                                     "is_error": error}]})


def typed(ts, text, **kw):
    return at(ts, type="user", message={"content": text}, **kw)


def reported(ts, agent="a1"):
    return typed(ts, f'Another Claude session sent a message:\n<agent-message from="{agent}">\n  done', isMeta=True)


def queued(ts, prompt, mode="task-notification"):
    return at(ts, type="attachment", attachment={"type": "queued_command", "prompt": prompt, "commandMode": mode})


def stopped(ts):
    return at(ts, type="system", subtype="stop_hook_summary")


def told(ts, text):
    return at(ts, type="attachment", attachment={"type": "hook_system_message", "hookEvent": "Stop", "content": text})


def blocked(ts, reason):
    return at(ts, type="attachment", attachment={"type": "hook_blocking_error", "hookEvent": "Stop",
                                                 "blockingError": {"blockingError": reason}})
