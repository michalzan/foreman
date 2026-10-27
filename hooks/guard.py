"""PreToolUse hook: force a permission prompt before a protected push, a merge, or a change to Foreman's own files.

A Bash command is read the way bash reads it, as far as a short program can: one pass over quotes,
comments and heredocs, then command substitutions, words, separators and redirects; nested `bash -c`,
`eval` and string-running programs, and what a shell is fed, are read in turn. Every `cd`, branch
switch and variable set in the same command is followed and every path a writing command touches is
resolved. Each gate then looks at one simple command at a time. Where the answer matters and the guard
cannot work it out, it asks. It is still not a shell; the README lists what it cannot see.
"""
from __future__ import annotations

import glob
import itertools
import os
import re
import shlex
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import declaration
    import gitutil
    import hookio
    import trust
except Exception as e:  # fail closed: a guard that cannot load must not wave calls through
    sys.stderr.write(f"foreman guard could not load, blocking this call: {e!r}")
    raise SystemExit(2)

gitutil.TIMEOUT = 8  # below the hook's 10 s
# This plugin's own directory: wherever Claude Code loads it from (a cache copy, or a local marketplace in place).
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
MAX_COMMAND = 200_000  # longer commands are not read at all, they ask (one line this long reads in 0.4 s)
MAX_DEPTH = 4  # nested strings and substitutions read this deep; deeper asks
MAX_GLOB = 2000  # matches looked at per glob
DEADLINE = 7  # seconds; then the guard blocks (a hook that times out at 10 s would let the call through)

UNAPPROVED = ("this project's .foreman.toml is not approved (or does not load), "
              "so the guard cannot tell which branches are protected")
UNKNOWN = "this push goes somewhere the guard cannot work out"

PUNCT = set("();<>|&")
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}
# Programs that run a command given to them as a string argument.
RUNNERS = {"watch", "trap", "flock", "su", "tmux", "screen", "parallel", "concurrently", "nohup"}
DECLARERS = {"export", "declare", "local", "readonly", "typeset"}
# Words in front of the command itself. The ones in WITH_ARGS may take options or a number first.
WRAPPERS = {"command", "builtin", "time", "then", "do", "else", "sudo", "exec", "nohup", "nice", "env", "timeout",
            "stdbuf", "caffeinate", "xargs", "!", "{", "}", "if", "elif", "while", "until"}
WITH_ARGS = {"sudo", "nice", "env", "timeout", "stdbuf", "xargs", "caffeinate"}
# `uv run [options] CMD`: the options that take a separate value.
UV_VALUE_OPTIONS = {"-p", "--python", "--with", "--project", "--directory", "--env-file", "--extra", "--group",
                    "--package", "--index", "--with-requirements"}
GIT_VALUE_OPTIONS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
PUSH_VALUE_OPTIONS = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}
# git subcommands that rewrite the work tree.
GIT_WRITERS = {"checkout", "restore", "reset", "pull", "stash", "merge", "rebase", "switch", "apply", "am",
               "cherry-pick", "revert", "rm", "mv", "clean"}
# Commands whose arguments are files they change; for REMOVERS a directory holding Foreman's files counts too.
REMOVERS = {"rm", "rmdir", "unlink", "mv"}
WRITERS = REMOVERS | {"cp", "ln", "install", "rsync", "chmod", "chown", "truncate", "touch", "tee", "sponge", "dd",
                      "patch"}
# For programs the guard does not know: arguments that usually mean "write here" or "change in place".
WRITE_SIGNS = {"-o", "-O", "-i", "--output", "--in-place", "--inplace", "inplace", "-w", "--write"}
READERS = {"cat", "less", "more", "head", "tail", "grep", "egrep", "fgrep", "rg", "wc", "diff", "cmp", "ls", "file",
           "stat", "bat", "jq"}
PLUGIN_OFF = {"disable", "uninstall", "remove", "rm"}

HEREDOC_OP = re.compile(r"<<(-?)[ \t]*(?:'([^'\n]*)'|\"([^\"\n]*)\"|\\?([A-Za-z_][\w.-]*))")
PIPED_TO_SHELL = re.compile(r"\|\s*(?:\S+\s+)*?(?:\S*/)?(?:bash|sh|zsh|dash|ksh|fish)\b")
CURRENT_BRANCH = re.compile(r"^git\s+(?:branch\s+--show-current|rev-parse\s+--abbrev-ref\s+HEAD"
                            r"|symbolic-ref\s+(?:-q\s+)?--short\s+(?:-q\s+)?HEAD)$")
TOPLEVEL = re.compile(r"^git\s+rev-parse\s+--show-toplevel$")
ASSIGNMENT = re.compile(r"^[A-Za-z_]\w*=")
VARIABLE = re.compile(r"\$\{([A-Za-z_]\w*)\}|\$([A-Za-z_]\w*)")
# One of Foreman's places named in a word or path (not .claude-test or .foreman-notes).
NAMES = re.compile(r"(?:^|/)(?:\.foreman(?:\.toml)?|\.claude|foreman-trusted\.json)(?:/|$)")
# The same, anywhere in a command's text: the fallback when a writing command has a path the guard cannot resolve.
# Not .claude on its own: a script read from ~/.claude/plugins/cache in the same command is common (superpowers).
MENTION = re.compile(r"""(?:^|[\s/'"=(])(?:\.foreman(?:\.toml)?|foreman-trusted\.json|\.claude/settings[\w.]*)(?=[\s/;&|)'"]|$)""")


@dataclass
class Simple:
    """One simple command: its words, the files its redirects write, and where it runs (None: unknown)."""
    words: list[str]
    writes: list[str] = field(default_factory=list)
    cwd: Path | None = None
    env: dict[str, str] = field(default_factory=dict)  # variables set earlier in the same command
    unreadable: str = ""  # set when the shell would run code the guard could not read: the guard asks
    branch: str | None = None  # checked out earlier in the same command; "?" unknown; None: as on disk
    stdin_unknown: bool = False  # its arguments come from xargs
    raw: str = ""  # the whole command as given, for the text fallback


# Reading a command

def _prepare(text: str) -> tuple[str, list[str], bool]:
    """One pass that reads quotes as bash does: drops comments, takes heredoc bodies out (returning the ones
    fed to a shell, which are commands) and says whether a quote was left open."""
    out: list[str] = []
    fed: list[str] = []
    pending: list[tuple[str, bool, bool]] = []
    stack: list[str] = []  # open contexts: ' " $( ( ((
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        top = stack[-1] if stack else ""
        if top == "'":
            out.append(c)
            i += 1
            if c == "'":
                stack.pop()
            continue
        if c == "\\" and i + 1 < n:
            out.append(text[i:i + 2])
            i += 2
            continue
        if top == '"':
            if text.startswith("$((", i):
                stack.append("((")
                out.append("$((")
                i += 3
            elif text.startswith("$(", i):
                stack.append("$(")
                out.append("$(")
                i += 2
            else:
                out.append(c)
                i += 1
                if c == '"':
                    stack.pop()
            continue
        if top == "((":
            if text.startswith("))", i):
                stack.pop()
                out.append("))")
                i += 2
            else:
                out.append(c)
                i += 1
            continue
        # unquoted, at the top level or inside $( ) or ( )
        if c in "'\"":
            stack.append(c)
            out.append(c)
            i += 1
        elif text.startswith("$((", i):
            stack.append("((")
            out.append("$((")
            i += 3
        elif text.startswith(("$(", "<(", ">("), i):
            stack.append("$(")
            out.append(text[i:i + 2])
            i += 2
        elif c == "(":
            stack.append("(")
            out.append(c)
            i += 1
        elif c == ")" and top in ("$(", "("):
            stack.pop()
            out.append(c)
            i += 1
        elif c == "#" and (not out or out[-1][-1:] in " \t\n;&|()"):  # a comment starts a word
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("<<", i) and not text.startswith("<<<", i) and (m := HEREDOC_OP.match(text, i)):
            line = "".join(out).rsplit("\n", 1)[-1]
            before = re.split(r"[;&|(]", line)[-1]  # the simple command the heredoc belongs to
            end = text.find("\n", m.end())
            after = text[m.end():n if end < 0 else end]
            to_shell = any(Path(w).name in SHELLS for w in before.split()) or bool(PIPED_TO_SHELL.search(after))
            pending.append((m.group(2) or m.group(3) or m.group(4) or "", m.group(1) == "-", to_shell))
            out.append(text[i:m.end()])
            i = m.end()
        elif c == "\n" and pending:
            out.append(c)
            i += 1
            for delim, strip, to_shell in pending:
                body = []
                while i < n:
                    j = text.find("\n", i)
                    j = n if j < 0 else j
                    line, i = text[i:j], min(j + 1, n)
                    if (line.lstrip("\t") if strip else line) == delim:
                        break
                    body.append(line)
                if to_shell:
                    fed.append("\n".join(body))
            pending = []
        else:
            out.append(c)
            i += 1
    return "".join(out), fed, any(s in ("'", '"') for s in stack)


def _closing(text: str, start: int) -> int:
    """Index of the parenthesis closing the one opened just before `start`, skipping quoted text; -1 if none."""
    depth, quote, i = 1, "", start
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\" and quote == '"':
                i += 1
            elif c == quote:
                quote = ""
        elif c == "\\":
            i += 1
        elif c in "'\"":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _stand_in(inner: str, cwd: Path | None) -> str:
    """What a substitution's output stands for: the current branch, the top level and pwd are known."""
    command = " ".join(inner.split())
    if CURRENT_BRANCH.match(command):
        return "HEAD"
    if command == "pwd" and cwd is not None:
        return str(cwd)
    if TOPLEVEL.match(command) and cwd is not None:
        try:
            return gitutil.git(cwd, "rev-parse", "--show-toplevel").strip()
        except Exception:
            return "$SUBST"
    return "$SUBST"


def _substitutions(text: str, cwd: Path | None) -> tuple[str, list[str], dict[str, str]]:
    """The command with each $(...), `...`, <(...) and >(...) replaced by a stand-in, the commands inside them,
    and the process substitutions by the path that stands for them (a shell may run one as a script)."""
    out, inner, procs, quote, i = [], [], {}, "", 0
    while i < len(text):
        c = text[i]
        if quote == "'":
            out.append(c)
            quote = "" if c == "'" else quote
            i += 1
            continue
        if c == "\\" and i + 1 < len(text):
            out.append(text[i:i + 2])
            i += 2
            continue
        if c in "'\"" and (not quote or quote == c):
            quote = "" if quote else c
            out.append(c)
            i += 1
            continue
        if text.startswith(("$(", "<(", ">("), i) and not (quote and c != "$"):
            end = _closing(text, i + 2)
            end = len(text) if end < 0 else end
            body = text[i + 2:end]
            inner.append(body)
            if c == "$":
                out.append(_stand_in(body, cwd))
            else:
                stand = f"/dev/fd/{63 + len(procs)}"
                procs[stand] = body
                out.append(stand)
            i = end + 1
            continue
        if c == "`":
            end = text.find("`", i + 1)
            end = len(text) if end < 0 else end
            body = text[i + 1:end]
            inner.append(body)
            out.append(_stand_in(body, cwd))
            i = end + 1
            continue
        out.append(c)
        i += 1
    return "".join(out), inner, procs


def _tokens(text: str) -> list[str] | None:
    """Words and operators, quotes respected; a newline separates commands as `;` does. None if unreadable.
    The file descriptor in front of a redirect (2>, 1>>) goes: it would read as a word, e.g. a branch."""
    text = re.sub(r"(?<![\w$-])\d+(?=[<>])", "", text.replace("\n", " ; "))
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        return None


@dataclass
class Part:
    """One simple command as split from the tokens, with what feeds its standard input."""
    words: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    strings: list[str] = field(default_factory=list)  # here-strings (<<<)
    heredoc: bool = False
    piped: bool = False  # stdin is the previous command's output


def _split(tokens: list[str]) -> list[Part]:
    parts, part, i = [], Part(), 0
    while i < len(tokens):
        t = tokens[i]
        operator = bool(t) and set(t) <= PUNCT
        if operator and "<" not in t and ">" not in t:  # ; & && || | ( ) and the like
            if part.words or part.writes:
                parts.append(part)
            part = Part(piped=t.endswith("|") and not t.endswith("||"))
            i += 1
            continue
        if operator:  # a redirect; the next word is its target
            op = t[min(k for k in (t.find("<"), t.find(">")) if k >= 0):]
            target = tokens[i + 1] if i + 1 < len(tokens) else None
            duplicate = op.endswith("&") and target is not None and (target.isdigit() or target == "-")
            if op.startswith("<<<") and target is not None:
                part.strings.append(target)
            elif op.startswith("<<"):
                part.heredoc = True  # the target is the delimiter; the body was taken out before
            elif ">" in op and not duplicate and target is not None:
                part.writes.append(target)
            i += 2 if target is not None else 1
            continue
        part.words.append(t)
        i += 1
    if part.words or part.writes:
        parts.append(part)
    return parts


def _command_index(words: list[str]) -> int | None:
    """Index of the command itself, past wrappers (sudo, env, timeout 60, xargs -I{}, ...) and VAR=value."""
    i, after_wrapper = 0, False
    while i < len(words):
        t, name = words[i], Path(words[i]).name
        if name == "uv" and words[i + 1:i + 2] == ["run"]:
            i += 2
            while i < len(words) and words[i].startswith("-"):
                i += 2 if words[i] in UV_VALUE_OPTIONS else 1
        elif name in WRAPPERS or ASSIGNMENT.match(t):
            after_wrapper = after_wrapper or name in WITH_ARGS
            i += 1
        elif after_wrapper and (t.startswith("-") or re.match(r"^\d+(?:\.\d+)?[smhd]?$", t)):
            i += 1
        elif i > 0 and words[i - 1] in ("-n", "-u", "-I", "-s"):
            i += 1  # the value of a wrapper's option
        else:
            return i
    return None


def _cd(cwd: Path | None, words: list[str], home: Path, env: dict[str, str]) -> Path | None:
    """Where `cd`/`pushd` leads. A directory that does not exist yet is fine, it is only a path."""
    target = next((w for w in words if not w.startswith("-")), None)
    if cwd is None or target == "-":
        return None
    return _resolve(target, cwd, home, env) if target is not None else home


def _switched_branch(g: Git, cwd: Path | None) -> str | None:
    """The branch a `git checkout`/`git switch` moves to: a name, "?" when unknown, None when it switches none."""
    if g.sub not in ("checkout", "switch") or "--" in g.args:
        return None
    args = g.args
    for k, a in enumerate(args):
        if a in ("-b", "-B", "-c", "-C", "--orphan"):
            new = args[k + 1] if k + 1 < len(args) else ""
            return "?" if not new or "$" in new or "`" in new else new
        if a.startswith("-"):
            continue
        if a == "-" or "$" in a or "`" in a:
            return "?"
        if g.sub == "checkout" and cwd is not None and (a == "." or (cwd / a).exists()):
            return None  # a path, not a branch
        return a
    return "?" if "-" in args else None


def _walk(command: str, cwd: Path | None, home: Path, depth: int = 0, raw: str | None = None):
    """Every simple command the shell would run, including those inside substitutions, nested strings and
    input fed to a shell. What it cannot read comes out as a command marked unreadable."""
    raw = command if raw is None else raw
    if depth > MAX_DEPTH:
        yield Simple([], cwd=cwd, unreadable="this command nests commands deeper than the guard reads", raw=raw)
        return
    text, fed, open_quote = _prepare(command.replace("\\\n", " "))
    if open_quote:
        yield Simple([], cwd=cwd, unreadable="this command leaves a quote open, so the guard cannot read it", raw=raw)
        return
    for body in fed:
        yield from _walk(body, cwd, home, depth + 1, raw)
    text, inner, procs = _substitutions(text, cwd)
    for body in inner:
        yield from _walk(body, cwd, home, depth + 1, raw)
    tokens = _tokens(text)
    if tokens is None:
        yield Simple([], cwd=cwd, unreadable="the guard cannot split this command into words", raw=raw)
        return
    env: dict[str, str] = {}
    branch: str | None = None
    previous: Part | None = None
    for part in _split(tokens):
        words = part.words
        i = _command_index(words)
        name = Path(words[i]).name if i is not None else ""
        if (i is None and words and all(ASSIGNMENT.match(w) for w in words)) or name in DECLARERS:
            for w in words:  # S=/tmp/x; ... $S   export D=.foreman; ... $D
                if ASSIGNMENT.match(w):
                    key, value = w.split("=", 1)
                    env[key] = _substitute(value, env)
            previous = part
            continue
        if name in ("cd", "pushd"):
            cwd = _cd(cwd, words[i + 1:], home, env)
            branch = None
            previous = part
            continue
        if name == "popd":
            cwd, branch = None, None
            previous = part
            continue
        through_xargs = i is not None and "xargs" in [Path(w).name for w in words[:i]]
        yield Simple(words, part.writes, cwd, dict(env), branch=branch, stdin_unknown=through_xargs, raw=raw)
        g = _git(words)
        if g is not None:
            switched = _switched_branch(g, cwd)
            branch = switched if switched is not None else branch
        for source in _run_strings(words, part, previous, procs):
            if source is None:
                yield Simple([], cwd=cwd, unreadable="a shell runs commands the guard cannot read", raw=raw)
            else:
                yield from _walk(source, cwd, home, depth + 1, raw)
        previous = part


def _run_strings(words: list[str], part: Part, previous: Part | None, procs: dict[str, str]):
    """Command text these words run in turn: `bash -c STR`, a shell's input, `eval`, `env -S`, `source <(...)`,
    and the string arguments of programs such as watch or trap. None for input that cannot be read."""
    for j, w in enumerate(words):
        name = Path(w).name
        if name in SHELLS:
            string, reads_stdin, script = _shell_options(words, j)
            if string is not None:
                yield string
            elif script in procs:
                yield _script_text(procs[script])
            elif reads_stdin:
                yield from part.strings
                if part.piped:
                    yield _literal_output(previous)
            return
        if name in ("source", ".") and j + 1 < len(words) and words[j + 1] in procs:
            yield _script_text(procs[words[j + 1]])
            return
        if name == "eval" and j + 1 < len(words):
            yield " ".join(words[j + 1:])
            return
        if name == "env":
            for k, a in enumerate(words[j + 1:], j + 1):
                if a in ("-S", "--split-string") and k + 1 < len(words):
                    yield words[k + 1]
                    return
                if a.startswith("--split-string="):
                    yield a.split("=", 1)[1]
                    return
        if name in RUNNERS:
            yield from (a for a in words[j + 1:] if any(ch.isspace() for ch in a))
            return


def _script_text(body: str) -> str | None:
    """What `<(echo ...)` hands a shell as a script, when it is plain text."""
    tokens = _tokens(_prepare(body)[0])
    parts = _split(tokens) if tokens is not None else []
    return _literal_output(parts[0]) if len(parts) == 1 else None


def _shell_options(words: list[str], j: int) -> tuple[str | None, bool, str]:
    """For the shell at words[j]: its -c string, whether it reads commands from standard input, its script file."""
    k = j + 1
    while k < len(words):
        flag = words[k]
        if flag in ("-o", "+o", "-O", "+O"):
            k += 2
        elif flag == "--":
            k += 1
            break
        elif flag.startswith("--"):
            k += 1
        elif flag[:1] in "-+" and len(flag) > 1:
            if flag[0] == "-" and "c" in flag[1:]:
                rest = words[k + 1:]
                if rest[:1] == ["--"]:
                    rest = rest[1:]
                return (rest[0] if rest else ""), False, ""
            if flag[0] == "-" and "s" in flag[1:]:
                return None, True, ""
            k += 1
        else:
            break
    script = words[k] if k < len(words) else ""
    return None, not script, script


def _literal_output(part: Part | None) -> str | None:
    """What the command before a pipe prints, when it is plain text: echo or printf of literals.
    "" for `cat <<EOF`, whose body was already read as commands. None when the guard cannot know."""
    if part is None:
        return None
    i = _command_index(part.words)
    if i is None:
        return None
    name, args = Path(part.words[i]).name, part.words[i + 1:]
    if name == "cat" and part.heredoc and not args:
        return ""
    if any("$" in a or "`" in a for a in args):
        return None
    if name == "echo":
        return " ".join(a for a in args if a not in ("-n", "-e", "-E"))
    if name == "printf" and args and "%" not in args[0]:
        return args[0].replace("\\n", "\n").replace("\\t", " ") + ("\n" + " ".join(args[1:]) if args[1:] else "")
    return None


# Paths

def _substitute(word: str, env: dict[str, str]) -> str:
    return VARIABLE.sub(lambda m: env.get(m.group(1) or m.group(2), m.group(0)), word) if env else word


def _resolve(word: str, cwd: Path, home: Path, env: dict[str, str] | None = None) -> Path | None:
    """The path a word names, or None when it depends on a variable or a substitution set elsewhere."""
    word = _substitute(word, {"PWD": str(cwd), **(env or {})})
    for prefix in ("~", "$HOME", "${HOME}"):
        if word == prefix or word.startswith(prefix + "/"):
            word = str(home) + word[len(prefix):]
            break
    if "$" in word or "`" in word:
        return None
    path = Path(word)
    return (path if path.is_absolute() else cwd / path).resolve()


def _inside(path: Path, root: Path) -> bool:
    low, top = str(path).lower(), str(root.resolve()).lower()
    return low == top or low.startswith(top + "/")


def _inside_plugin(path: Path) -> bool:
    return _inside(path, PLUGIN_ROOT)


def _is_foreman_file(path: Path, home: Path) -> bool:
    """Case-insensitively, as macOS file systems are: .FOREMAN.TOML is the same file."""
    path = path.resolve()
    low = str(path).lower()
    name = path.name.lower()
    if low == str(trust.store_path().resolve()).lower() or _inside_plugin(path):
        return True
    claude = str((home / ".claude").resolve()).lower()
    if low in (claude + "/hooks/harness", claude + "/plugins") or low.startswith(
            (claude + "/hooks/harness/", claude + "/skills/harness-", claude + "/plugins/")):
        return True
    if path.parent.name.lower() == ".claude" and name in ("settings.json", "settings.local.json"):
        return True
    parts = [p.lower() for p in path.parts]
    if ".foreman" in parts:
        below = parts[len(parts) - parts[::-1].index(".foreman"):]
        if not below or below[0] != "walkthrough":
            return True
    return name in (declaration.NAME, "foreman-trusted.json")


def _holds_foreman(path: Path, home: Path) -> bool:
    """Whether a directory contains Foreman's files, so removing or moving it removes them."""
    claude = (home / ".claude").resolve()
    protected = [PLUGIN_ROOT, claude / "plugins", claude / "hooks" / "harness", claude / "settings.json",
                 claude / "settings.local.json", trust.store_path().resolve()]
    if any(_inside(p, path) for p in protected):
        return True
    return (path / declaration.NAME).is_file() or (path / ".foreman").is_dir()


def _plugin_named(low: str, home: Path) -> bool:
    plugin = str(PLUGIN_ROOT).lower()
    home_low = str(home.resolve()).lower()
    return plugin in low or (plugin.startswith(home_low + "/") and plugin[len(home_low):] in low)


def _names_foreman(word: str, home: Path) -> bool:
    """For a path that cannot be resolved: whether its text names one of Foreman's places."""
    low = word.lower()
    return bool(NAMES.search(low)) or _plugin_named(low, home)


def _expand(path: Path) -> list[Path]:
    """What a glob matches now, as bash would (no dotfiles); one level of {a,b} braces."""
    text = str(path)
    m = re.search(r"\{([^{}]*,[^{}]*)\}", text)
    variants = [text[:m.start()] + alt + text[m.end():] for alt in m.group(1).split(",")] if m else [text]
    found: list[Path] = []
    for v in variants:
        if any(c in v for c in "*?["):
            found += [Path(p) for p in itertools.islice(glob.iglob(v), MAX_GLOB)]
        else:
            found.append(Path(v))
    return found


# The gates, one simple command at a time

@dataclass
class Git:
    sub: str
    args: list[str]
    directory: str
    configs: list[str]
    through_xargs: bool


def _git(words: list[str]) -> Git | None:
    """The git subcommand in these words, wherever git sits behind wrappers."""
    start = next((n for n, t in enumerate(words) if Path(t).name == "git"), None)
    if start is None:
        return None
    i, directory, configs = start + 1, "", []
    while i < len(words):
        t = words[i]
        if t in GIT_VALUE_OPTIONS:
            value = words[i + 1] if i + 1 < len(words) else ""
            if t == "-c":
                configs.append(value)
            if t in ("-C", "--work-tree"):
                directory = str(Path(directory) / value) if directory else value
            i += 2
        elif t.startswith("--work-tree="):
            directory = t.split("=", 1)[1]
            i += 1
        elif t.startswith("-"):
            i += 1
        else:
            sub, args = t, words[i + 1:]
            if sub == "subtree" and args[:1] == ["push"]:
                sub, args = "push", args[1:]
            return Git(sub, args, directory, configs, "xargs" in [Path(w).name for w in words[:start]])
    return None


def _short_flag(args: list[str], letters: str) -> bool:
    return any(a.startswith("-") and not a.startswith("--") and any(c in a[1:] for c in letters) for a in args)


def _push_reason(args: list[str], cwd: Path, protected: tuple[str, ...], branch: str | None) -> str | None:
    if any(a in ("--all", "--mirror") for a in args):
        return "this push sends every branch, including protected ones"
    positional, i = [], 0
    while i < len(args):
        if args[i] in PUSH_VALUE_OPTIONS:
            i += 2
            continue
        if not args[i].startswith("-"):
            positional.append(args[i])
        i += 1
    if any("$" in d or "`" in d or "*" in d or "{" in d for d in positional):
        return UNKNOWN
    dests = [r.split(":")[-1].lstrip("+").removeprefix("refs/").removeprefix("heads/") for r in positional[1:]]
    current = branch if branch is not None else gitutil.current_branch(cwd)
    dests = [current if d in ("HEAD", "@") else d for d in (dests or [current])]
    if "?" in dests:
        return UNKNOWN  # the branch was switched earlier in this command to one the guard cannot read
    hit = [d for d in dests if d in protected]
    return f"this pushes to a protected branch ({', '.join(hit)})" if hit else None


def _protection(cwd: Path) -> tuple[str, ...] | str | None:
    """Protected branches of the project at cwd; UNAPPROVED when its declaration cannot be trusted; None outside one."""
    root = declaration.find_root(cwd) if cwd.is_dir() else None
    if root is None:
        return None
    if not trust.is_trusted(root):
        return UNAPPROVED
    try:
        return declaration.load(root).protected
    except declaration.DeclarationError:
        return UNAPPROVED


def _push(cmd: Simple, g: Git, home: Path, start) -> str | None:
    if g.through_xargs:
        return UNKNOWN  # the refspec comes from stdin
    if any(c.lower().startswith(("alias.", "remote.", "push.", "branch.")) for c in g.configs):
        return UNKNOWN  # config on the command line may turn this into any push
    where = cmd.cwd
    if where is not None and g.directory:
        where = _resolve(g.directory, where, home, cmd.env)
    if where is None or not where.is_dir():  # fail closed: a push in an unknown place may be a push to main
        return "this push runs in a directory the guard cannot work out"
    protected = _protection(where)
    if protected is None:
        return ("this push leaves a Foreman project for a directory without a .foreman.toml"
                if start is not None else None)
    if isinstance(protected, str):
        return protected
    return _push_reason(g.args, where, protected, cmd.branch if not g.directory else None)


def _values(args: list[str]) -> list[str]:
    """Path-like arguments: operands, and the values of --option=value."""
    return [a.split("=", 1)[1] if a.startswith("--") and "=" in a else a
            for a in args if not a.startswith("-") or (a.startswith("--") and "=" in a)]


def _writes(cmd: Simple, g: Git | None, home: Path) -> str | None:
    words = cmd.words
    i = _command_index(words)
    name = Path(words[i]).name.lower() if i is not None else ""
    args = words[i + 1:] if i is not None else []
    paths = list(cmd.writes)
    writer = name in WRITERS
    if name in ("sed", "gsed"):
        writer = any(a == "--in-place" or a.startswith("--in-place=") for a in args) or _short_flag(args, "i")
    elif name in ("perl", "ruby"):
        writer = _short_flag(args, "i")
    elif name == "find":
        writer = any(a in ("-delete", "-exec", "-execdir", "-ok", "-okdir") for a in args)
    git_writer = g is not None and g.sub in GIT_WRITERS
    if writer:
        paths += [a.split("=", 1)[1] if name == "dd" and a.startswith("of=") else a for a in _values(args)]
    elif git_writer:
        paths += _values(g.args)
    if not writer and not git_writer and g is None and name not in READERS:
        signed = [a[2:] for a in args if re.match(r"^-[oO].", a)]  # -oFILE
        if signed or any(a in WRITE_SIGNS or a.startswith("--output=") for a in args):
            for word in signed + [a.split("=", 1)[-1] for a in args]:
                path = _resolve(word, cmd.cwd, home, cmd.env) if cmd.cwd is not None else None
                if (path is not None and _is_foreman_file(path, home)) or (
                        path is None and _names_foreman(_substitute(word, cmd.env), home)):
                    return f"{name} may write one of Foreman's files ({word}); the guard does not know {name}"
    if not paths and not git_writer and not (writer and cmd.stdin_unknown):
        return None
    if cmd.cwd is None:
        return "this command writes after a cd the guard cannot follow"
    if git_writer:
        repo = _resolve(g.directory, cmd.cwd, home, cmd.env) if g.directory else cmd.cwd
        if repo is None or _inside_plugin(repo):
            return "this git command may rewrite the Foreman plugin's own files"
    if name == "ln":
        link = next((a for a in reversed(args) if not a.startswith("-")), "")
        where = _resolve(link, cmd.cwd, home, cmd.env)
        if where is None or ".foreman" in [p.lower() for p in where.parts]:
            return "a link inside Foreman's run state could lead into it"
    unresolved = writer and cmd.stdin_unknown  # xargs hands it paths the guard cannot see
    for word in paths:
        path = _resolve(word, cmd.cwd, home, cmd.env)
        if path is None:
            if _names_foreman(_substitute(word, cmd.env), home):
                return f"this command may modify one of Foreman's files ({word})"
            unresolved = True
            continue
        parts = path.parts
        k = next((k for k, part in enumerate(parts) if any(c in part for c in "*?[{")), None)
        if k is not None and _is_foreman_file(Path(*parts[:k]) / "x", home):
            return f"this command may modify Foreman's files ({word})"  # a glob inside Foreman's places
        for p in _expand(path):
            if _is_foreman_file(p, home):
                return f"this command may modify one of Foreman's files ({p.name})"
            if name in REMOVERS and _holds_foreman(p, home):
                return f"this command removes or moves a directory that holds Foreman's files ({p.name})"
    low = cmd.raw.lower()
    if unresolved and (MENTION.search(low) or _plugin_named(low, home)):
        return "this command writes to a path the guard cannot resolve, and it names one of Foreman's files"
    return None


def _check(cmd: Simple, home: Path, start) -> str | None:
    if cmd.unreadable:
        return cmd.unreadable
    words = cmd.words
    low = [w.lower() for w in words]
    names = [Path(w).name for w in low]
    if any("bypasspermissions" in w or w.startswith("--dangerously-skip-permissions") for w in low):
        return "this starts a session that skips permission prompts, where the guard cannot ask"
    for j, n in enumerate(names):
        if "claude" in n:
            rest = low[j + 1:]
            k = next((k for k, w in enumerate(rest) if w in ("plugin", "plugins")), None)
            if k is not None and (rest[k + 1:k + 2] and rest[k + 1] in PLUGIN_OFF
                                  or rest[k + 1:k + 2] == ["marketplace"] and rest[k + 2:k + 3] in (["remove"], ["rm"])):
                return "this switches off or removes a plugin, which may be Foreman"
    if "gh" in names:
        rest = low[names.index("gh") + 1:]
        if "pr" in rest and next((w for w in rest[rest.index("pr") + 1:] if not w.startswith("-")), "") == "merge":
            return "this merges a pull request into its base branch"
        if "api" in rest and any(re.search(r"/pulls/[^/]+/merge\b", w) for w in rest):
            return "this merges a pull request into its base branch"
    if "run.py" in names and "stop" in low:
        return "this command ends the active run"
    if ("trust.py" in names and "approve" in low) or any("trust.approve" in w for w in low):
        return "this approves the project's declaration, including the check command the stop gate will run"
    g = _git(words)
    if g is not None and any(c.lower().startswith("alias.") for c in g.configs):
        return UNKNOWN  # an alias defined on the command line may be a push
    if g is not None and g.sub == "clean" and _short_flag(g.args, "xX"):
        return "git clean -x deletes ignored files, including Foreman's run state"
    if g is not None and g.sub == "stash" and ("--all" in g.args or _short_flag(g.args, "a")):
        return "git stash --all stashes and removes ignored files, including Foreman's run state"
    reason = _writes(cmd, g, home)
    if reason:
        return reason
    if g is not None and g.sub == "push":
        return _push(cmd, g, home, start)
    return None


def _bash_reason(command: str, cwd: Path, home: Path) -> str | None:
    if len(command) > MAX_COMMAND:
        return f"this command is longer than {MAX_COMMAND} characters, too long for the guard to read"
    start = _protection(cwd)
    for cmd in _walk(command, cwd, home):
        reason = _check(cmd, home, start)
        if reason:
            return reason
    return None


def decide(inp: dict, home: Path | None = None) -> str | None:
    home = home or Path.home()
    cwd = Path(inp.get("cwd") or os.getcwd())
    tool_input = inp.get("tool_input") or {}
    if inp.get("tool_name") == "Bash":
        return _bash_reason(str(tool_input.get("command", "")), cwd, home)
    target = tool_input.get("file_path") or tool_input.get("notebook_path")
    if target:
        target = str(target)
        path = Path(str(home) + target[1:]) if target == "~" or target.startswith("~/") else Path(target)
        path = path if path.is_absolute() else cwd / path
        if _is_foreman_file(path, home):
            return f"{path.name} is one of Foreman's files; changing it alters the gates"
    return None


def _out_of_time(signum, frame):
    raise TimeoutError(f"the guard took longer than {DEADLINE} s")


def main() -> int:
    # No FOREMAN_JUDGE short-circuit here: the judge runs with every hook off already, and the variable
    # in front of a nested `claude -p` would otherwise switch the guard off for that session.
    previous = signal.signal(signal.SIGALRM, _out_of_time)
    signal.alarm(DEADLINE)
    try:
        reason = decide(hookio.read_input())
        if reason:
            hookio.emit({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": f"foreman: {reason}",
            }})
    except Exception as e:  # fail closed: an unverified push is worse than a blocked call
        sys.stderr.write(f"foreman guard crashed, blocking this call: {e!r}")
        return 2
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
