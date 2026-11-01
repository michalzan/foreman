"""Shapes of commands that can go into a public issue, and the private names to look for before it does."""
from __future__ import annotations

import re
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Branch names too common to count as private; they would match ordinary words in a description.
GENERIC = {"main", "master", "dev", "develop", "test", "staging", "production", "release", "head", "origin"}
FOREMAN_FILE = re.compile(r"^(?:\.foreman\.toml|\.foreman(?:/[\w.-]*)*|foreman-trusted\.json)$")
FOREMAN_SCRIPTS = {p.name for p in Path(__file__).resolve().parent.glob("*.py")}
HEREDOC = re.compile(r"(?<!<)<<-?[ \t]*[\\'\"]?(\w+)['\"]?")
URL = re.compile(r"\b[a-z][a-z0-9+.-]*://(?:[^\s'\"()`]|\([^\s'\"()`]*\)|\()+")  # one level of parens: a closing one may be the shell's
QUOTED = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")
COMMENT = re.compile(rf"({QUOTED.pattern})|(?:^|(?<=[;&|()`])|[ \t]+)#[^\n]*", re.M)
ASSIGN = re.compile(r"(?<![\w-])((?:--)?[A-Za-z_][\w-]*)=(?!<|[\"'(`]|\$\()([^\s;&|)]+)")
SHA = re.compile(r"\b(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b")
SENSITIVE = re.compile(r"(?<![\w-])(--?[\w-]*(?:token|password|passwd|secret|key|auth)[\w-]*)([ \t]+)(?![<'\"-])([^\s;&|]+)", re.I)
CLIENT = re.compile(r"(?<![\w.-])(?:curl|mysql[\w-]*|mariadb[\w-]*|psql|sshpass|docker|sudo)(?![\w.-])")
ATTACHED = re.compile(r"(?<![\w-])(-[pPuUhHdD])(?=[^\s<'\"])[^\s;&|)]+")  # mysql -phunter2, curl -uana, psql -hprod
SECRETS = [(kind, re.compile(rx)) for kind, rx in (
    ("GitHub token", r"\bgh[pousr]_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}"),
    ("API key (sk-)", r"\bsk-[A-Za-z0-9_-]{20,}"),
    ("AWS access key", r"\bAKIA[0-9A-Z]{16}\b"),
    ("Slack token", r"\bxox[bap]-[A-Za-z0-9-]{10,}"),
    ("Bearer token", r"\bBearer\s+(?!<)[A-Za-z0-9._~+/=-]{16,}"),
    ("private key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)"),
    ("JWT", r"\beyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}(?:\.[A-Za-z0-9_-]+)?"),
    ("URL with credentials", r"\b[a-z][a-z0-9+.-]*://[^\s/:@]*:(?!<)[^\s/]+@"),
    # the stoplist is the whole value: changeme is a placeholder, changeme123 is not
    ("credential assignment", r"""(?i)["']?\b[\w-]*(?:password|passwd|secret|token|api[_-]?key)[\w-]*["']?\s*[=:]\s*["']?(?![<{$\s])"""
                              r"""(?!(?:true|false|none|null|your|xxx|\*+|example|changeme)(?![^\s'",;]))[^\s'",;]{4,}"""),
)]
PATHCMDS = {"cd", "ls", "cat", "rm", "mkdir", "touch", "cp", "mv", "head", "tail"}
BRANCHCMDS = {"push", "branch", "switch", "checkout", "merge", "rebase", "fetch", "pull", "log", "diff", "show", "tag", "reset"}
REMOTECMDS = {"push", "fetch", "pull"}
GITPATH = {"-C", "--git-dir", "--work-tree"}
WRAPPERS = {"sudo", "env", "command", "time", "nohup", "exec", "if", "elif", "while", "until", "!", "then", "do", "else"}
AT = re.compile(r"(<\w+>)?([^\s'\"()<>;&|`]*@[^\s'\"()<>;&|`]*)")
PATHLIKE = re.compile(r"(?<![\w<.])(?:[~\w.${}-]*/[\w.${}-][\w./${}-]*|[\w.-]*\.[A-Za-z][A-Za-z0-9]{0,5})(?![\w>])")
PLACEHOLDER = r"<(?:arg|value|path|repo|url|sha|text|branch|protected|foreman)>"
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
# The sweep's list: a shape holds these and placeholders, whatever the passes before it made of the command.
KNOWN = set("""
    EOF HEAD HOME PATH PWD SHELL TMPDIR USER add api apply auth awk az bash branch build case cat cd check checkout
    checks cherry-pick chmod ci clean clone close command comment commit compose config cp create curl cut date diff
    do docker done down drop echo edit elif else env esac eval exec exit export false fetch fi find for gh git grep
    head i if in init install issue jq kubectl list lock log login logs ls lsof make merge mkdir mv mysql node nohup
    npm npx origin pgrep pip pipefail pop pr printf ps psql pull push pwd pytest python python3 rebase release remote
    repo
    reset restore rev-list rev-parse revert rg rm rsync run scp sed set sh show sleep sort ssh sshpass stash status
    subtree sudo switch sync tag tail tar tee test then time timeout tool touch tr true until up upstream uv venv
    view vitest wc wget which while worktree xargs
""".split())
KEEP = KNOWN | FOREMAN_SCRIPTS | {".foreman", ".foreman.toml", "foreman-trusted.json", "gate.log", "hooks", "run.json", "state.json"}
WORD = re.compile(rf"{PLACEHOLDER}|[\w.-]+")
NUMBER = re.compile(r"\d{1,4}(?:\.\d{1,4})?|[.-]+")  # 3.12, 5, and . .. - -- which are punctuation
FLAG_NAME = re.compile(r"-(?:[A-Za-z]{1,4}|\d{1,4})|--[\w-]+")


@dataclass
class Context:
    home: str
    root: str
    protected: tuple[str, ...] = ()
    branches: tuple[str, ...] = ()


def _path(token: str) -> str:
    if FOREMAN_FILE.match(token):
        return token
    if Path(token).name in FOREMAN_SCRIPTS and "/hooks/" in "/" + token:
        return "<foreman>/hooks/" + Path(token).name
    return "<path>"


def _at(m: re.Match) -> str:
    """A directory before the @ makes it a path (acme/logo@2x.png); otherwise it is a user, package or email."""
    if "/" in m[2].split("@")[0] and not m[2].startswith("@"):
        return (m[1] + "/" if m[1] else "") + "<path>"
    return (m[1] or "") + "<value>"


def redact_command(cmd: str, ctx: Context) -> str:
    """The shape of a command. The passes give the good placeholders; the sweep gives the guarantee, so a parsing
    mistake can only over-redact."""
    return sweep(_shape(CONTROL.sub("", cmd), ctx))


def kept(m: re.Match) -> bool:
    """Whether a WORD match may stay. A flag stands alone: -acme touching a word before it, or -ana:pw, is no flag."""
    w = m[0]
    if w.startswith("<") or w in KEEP or NUMBER.fullmatch(w):
        return True
    return (FLAG_NAME.fullmatch(w) is not None and not m.string[m.start() - 1:m.start()].strip()
            and re.fullmatch(r"[\s;&|)`\"'=<]?", m.string[m.end():m.end() + 1]) is not None)


def sweep(s: str) -> str:
    """The last step: every word in the string, quoted or not, stays only if kept says so. Punctuation stays."""
    return WORD.sub(lambda m: m[0] if kept(m) else "<arg>", s)


def _shape(cmd: str, ctx: Context) -> str:
    out = COMMENT.sub(lambda m: m[1] or "", _heredocs(cmd))
    out = re.sub(r"[ \t]*\\\n\s*", " ", out)  # a continued line is one command
    out = URL.sub("<url>", out)
    tilde_root = "~" + ctx.root[len(ctx.home):] if ctx.root.startswith(ctx.home + "/") else ctx.root
    for prefix in sorted({ctx.root, tilde_root}, key=len, reverse=True):
        out = re.sub(re.escape(prefix) + r"(?![\w.-])", "<repo>", out)
    out = re.sub(re.escape(ctx.home) + r"(?![\w.-])", "~", out)
    names = {b: "<protected>" for b in ctx.protected}
    names.update({b: "<branch>" for b in ctx.branches if b not in names})
    for name in sorted(names, key=len, reverse=True):
        out = re.sub(r"(?<![\w/-])(?<![^.]\.)(?:refs/heads/|refs/remotes/)?(?:[\w.-]+/)?" + re.escape(name) + r"(?![\w/-]|\.(?!\.))",
                     names[name], out)
    out = QUOTED.sub(lambda m: m[0][0] + _quoted(m[0][1:-1], ctx, m.string[:m.start()]) + m[0][0], out)
    out = ASSIGN.sub(lambda m: f"{m[1]}=<value>", out)
    out = SENSITIVE.sub(lambda m: f"{m.group(1)}{m.group(2)}<value>", out)
    if CLIENT.search(out):
        out = ATTACHED.sub(r"\1<value>", out)
    out = SHA.sub("<sha>", out)
    out = AT.sub(_at, out)  # user@host, emails, pkg@version: a token with @ loses all of it
    out = _bare(out)

    def path(m: re.Match) -> str:
        token = m.group(0)
        before = re.search(r"<\w+>$", out[:m.start()])
        if before and before.group(0) == "<repo>" and token.startswith("/"):
            return "/" + _path(token[1:])
        if before and before.group(0) != "<value>":  # already a placeholder with its own tail
            return token
        return token if token == "~" else _path(token)
    return PATHLIKE.sub(path, out)


def _heredocs(cmd: str) -> str:
    """Each heredoc's body becomes <text> and its delimiter EOF; one with no end loses the rest of the text."""
    out, lines = [], iter(cmd.split("\n"))
    for line in lines:
        ends = HEREDOC.findall(line)
        out.append(HEREDOC.sub("<<EOF", line))
        for end in ends:
            out.append("<text>")
            if any(body.strip() == end for body in lines):  # takes the body off the iterator
                out.append("EOF")
    return "\n".join(out)


def secrets(text: str) -> list[str]:
    """The kind of each secret-shaped match in text; never the matched value."""
    return [kind for kind, rx in SECRETS for _ in rx.finditer(text)]


def _sides(w: str) -> str:
    """Redact each side of a:b or a..b / a...b; sides that are placeholders, HEAD or empty stay."""
    return re.sub(r"[^:.<>]+(?:<\w+>)?|<\w+>", lambda m: m[0] if m[0].startswith(("<", "HEAD")) else "<branch>", w)


def _bare(s: str) -> str:
    """Names with no slash or dot: the arguments of path-taking commands, git's branch positions, refspecs,
    ranges and -m. Walks the words with the command in force, split at operators even when they touch a word."""
    words = re.split(r"(\s+|[;&|()`])", s)
    cmd = sub = opt = None
    n, msg, stop = 0, False, False
    for i, w in enumerate(words):
        if not w:
            continue
        if w.isspace() or w in ";&|()`":
            cmd = None if "\n" in w or not w.isspace() else cmd
            continue
        word = w.lstrip("\"'$")
        if cmd is None:
            if word not in WRAPPERS and not word.startswith("-") and not re.match(r"[\w-]+=", word):
                cmd, sub, opt, n, msg, stop = word, None, None, 0, False, False
            continue
        plain = not re.search(r"""[<>$"'`&:=(){}\\~]""", w) and w not in (".", "..") and not w.isdigit()
        if cmd == "git" and sub is None and opt:  # the value of -C, -c, --git-dir ...
            words[i], opt = _path(w) if plain else w, None
        elif w == "--":
            stop = True
        elif w.startswith("-"):
            if cmd == "git" and sub is None and w in GITPATH:
                opt = w
            msg = cmd == "git" and re.fullmatch(r"-[a-zA-Z]*m|--message", w) is not None
        elif cmd in PATHCMDS and plain and not FOREMAN_FILE.match(w):
            words[i] = _path(w)
        elif cmd == "git":
            if sub is None:
                sub = w
            elif msg:
                words[i], msg = ("<text>" if plain else w), False
            elif sub in BRANCHCMDS and stop and plain:
                words[i] = _path(w)
            elif sub in BRANCHCMDS and not stop:
                n += 1
                if sub in REMOTECMDS and n == 1:  # the first argument is the remote
                    continue
                if not re.search(r"""["'$`(){}\\~*]""", w) and w not in (".", ".."):
                    words[i] = _sides(w)
    return "".join(words)


def _quoted(inner: str, ctx: Context, before: str) -> str:
    """A quoted string becomes <text>, unless it is code (after -c or eval, or a $(...)), a placeholder or
    one path, which keep their shape."""
    if re.search(r"(?:(?:^|[\s;&|(/])(?:ba|z|da|k)?sh(?:\s+-\w+(?:\s+[a-z]+)?)*?\s+-\w*c|(?:^|[\s;&|(])eval)\s+$", before):
        return _shape(inner, ctx)
    sub = re.fullmatch(r"\$\((.*)\)", inner, re.S)
    if sub:
        return "$(" + _shape(sub.group(1), ctx) + ")"
    if re.fullmatch(PLACEHOLDER, inner):
        return inner
    if inner.startswith("<repo>/") and PATHLIKE.fullmatch(inner[7:]):
        return "<repo>/" + _path(inner[7:])
    if PATHLIKE.fullmatch(inner) and inner not in ("", "."):
        return _path(inner)
    return "<text>"


def _git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def branches(root: Path) -> list[str]:
    """Local and remote branch names, those of origin without its prefix. origin/HEAD reads as plain "origin"."""
    refs = _git(root, "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes").split()
    short = {r.split("/", 1)[1] if r.startswith("origin/") else r for r in refs}
    return sorted(s for s in short if s.lower() not in GENERIC)


def private_names(root: Path, frontend: tuple[str, ...] = ()) -> list[str]:
    """Names that must not reach a public issue: this project's, its remote's, its branches', the user's."""
    names = {root.name, str(Path.home()), Path.home().name, *(p.strip("/") for p in frontend)}
    url = _git(root, "remote", "get-url", "origin").strip()
    names.update(re.split(r"[:/]", re.sub(r"\.git$", "", url))[-2:] if url else [])
    for short in branches(root):
        names.update({short, short.rsplit("/", 1)[-1]})
    names.update({_git(root, "config", "user.name").strip(), _git(root, "config", "user.email").strip()})
    host = socket.gethostname()
    names.update({host, host.split(".")[0]})
    return sorted(n for n in names if len(n) >= 3 and n.lower() not in GENERIC)


def scan(text: str, names: list[str]) -> list[str]:
    """The names that appear in text, as whole words, ignoring case."""
    return [n for n in names if re.search(r"(?<![\w])" + re.escape(n) + r"(?![\w])", text, re.I)]
