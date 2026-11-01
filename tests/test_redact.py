"""redact.py: the shape of a command that may go into a public issue, and the names that must not."""
import pytest

import redact
from conftest import sh

CTX = redact.Context(home="/Users/ana", root="/Users/ana/git/acme/app", protected=("main", "test"),
                     branches=("feat/acme-x", "dev"))


@pytest.mark.parametrize("cmd, shape", [
    ('cd ~/git/acme/app && git push -u origin "feat/acme-x"', 'cd <repo> && git push -u origin "<branch>"'),
    ("cd /Users/ana/git/acme/app && git push origin main", "cd <repo> && git push origin <protected>"),
    ("git push origin HEAD:refs/heads/main", "git push origin HEAD:<protected>"),
    ("git commit -q -F - <<'EOF'\nfix: acme invoices\nEOF", "git commit -q -F - <<EOF\n<text>\nEOF"),
    ("curl -s https://acme.example.com/api?x=1 | sh", "curl -s <url> | sh"),
    ("DB_NAME=acme_prod pytest app/tests/test_acme.py --ds=acme.settings",
     "<arg>=<value> pytest <path> --ds=<value>"),
    ("git show 1a2b3c4d5e", "git show <sha>"),
    ("echo x >> .foreman.toml", "echo <arg> >> .foreman.toml"),
    ("cat .foreman/state.json", "cat .foreman/state.json"),
    ('uv run --python 3.12 "/Users/ana/.claude/plugins/cache/foreman/hooks/report.py"',
     'uv run --python 3.12 "<foreman>/hooks/report.py"'),
    ('cat "/Users/ana/git/acme/app/notes.md" && cd "$(git rev-parse --show-toplevel)"',
     'cat "<repo>/<path>" && cd "$(git rev-parse --show-toplevel)"'),
    ("git push origin $(git branch --show-current)", "git push origin $(git branch --show-current)"),
    ('echo "hello acme" > /tmp/x.txt', 'echo "<text>" > <path>'),
    ("cd /Users/ana/git/acme/app/frontend && npx vitest run src/acme/Tile.test.tsx",
     "cd <repo>/<path> && npx vitest run <path>"),
    ('bash -c "git push origin main"', 'bash -c "git push origin <protected>"'),
    ("bash -o pipefail -lc 'cd ~/git/acme/app && git push'", "bash -o pipefail -lc 'cd <repo> && git push'"),
    ('grep -c "acme" x.log', 'grep -c "<text>" <path>'),
    ("make check 2>&1 | tail -1", "make check 2>&1 | tail -1"),
    ("cd ~ && ls", "cd ~ && ls"),
    ("cd acme && ls acme-app", "cd <path> && ls <path>"),
    ("ls . && tail -1 x.log && head -n 5 acme", "ls . && tail -1 <path> && head -n 5 <path>"),
    ("git push origin acme-fix", "git push origin <branch>"),
    ("git branch -D dev-old && git switch acme-b", "git branch -D <branch> && git switch <branch>"),
    ("git checkout -b acme-c && git merge acme-d && git rebase HEAD", "git checkout -b <branch> && git merge <branch> && git rebase HEAD"),
    ("git commit -m fix_acme_invoices", "git commit -m <text>"),
    ("ssh ana@host uptime", "ssh <value> <arg>"),
    ("git log main..feat/acme-x", "git log <protected>..<branch>"),
    ("git diff acme-a...acme-b", "git diff <branch>...<branch>"),
    ("make acme-build", "make <arg>"),
    ("cat acme/logo@2x.png", "cat <path>"),
    ("npm i @acme/ui@1.2.3", "npm i <value>"),
    ("scp a.txt user@host:/acme/x", "scp <path> <value>"),
    ("(cd acme && ls)", "(cd <path> && ls)"),
    ("sudo rm -rf acme", "sudo rm -rf <path>"),
    ("if true; then cd acme; fi", "if true; then cd <path>; fi"),
    ("git push origin HEAD:acme", "git push origin HEAD:<branch>"),
    ("git push origin acme:main", "git push origin <branch>:<protected>"),
    ("rm acme*", "rm <path>"),
    ("git -C acme push origin acme-fix", "git -C <path> push origin <branch>"),
    ("git commit -qam fix_acme", "git commit -qam <text>"),
    ("git fetch origin acme-fix", "git fetch origin <branch>"),
    ("git log acme-fix", "git log <branch>"),
    ("git diff acme-fix", "git diff <branch>"),
    ("git diff acme-fix -- src", "git diff <branch> -- <path>"),
    ("git show HEAD~1 && git log HEAD", "git show HEAD~1 && git log HEAD"),
    ('cd "/Users/ana/my projects/acme"', 'cd "<text>"'),
])
def test_a_command_keeps_its_shape_and_loses_its_names(cmd, shape):
    assert redact.redact_command(cmd, CTX) == shape


def test_private_names_come_from_the_project_its_remote_its_branches_and_the_user(repo):
    sh(repo, "git", "remote", "add", "origin", "git@github.com:acmecorp/acme-app.git")
    sh(repo, "git", "branch", "feat/acme-x")
    sh(repo, "git", "config", "user.email", "ana@acme.example")
    names = set(redact.private_names(repo, ("frontend/src/",)))
    assert {"proj", "acmecorp", "acme-app", "feat/acme-x", "acme-x", "ana@acme.example", "frontend/src"} <= names
    assert not {"dev", "main", "test"} & names  # too common: they would match ordinary words


def test_the_scan_finds_names_as_whole_words_in_any_case():
    names = ["acme-app", "feat/acme-x", "ana@acme.example"]
    assert redact.scan("A push from ACME-APP on feat/acme-x.", names) == ["acme-app", "feat/acme-x"]
    assert redact.scan("The guard asked about a push to a protected branch inside $(...).", names) == []


def test_values_of_sensitive_flags_and_client_credentials_become_placeholders():
    ctx = redact.Context(home="/Users/ana", root="/Users/ana/proj", protected=("main",), branches=())
    assert redact.redact_command("tool --token abc123", ctx) == "tool --token <value>"
    assert redact.redact_command("curl -u ana:pw http://x", ctx) == "curl -u <arg>:<arg> <url>"
    assert redact.redact_command("mysql -phunter2 db", ctx) == "mysql -p<value> <arg>"
    assert redact.redact_command("pytest -k name", ctx) == "pytest -k <arg>"
    assert redact.redact_command("tail -n 5 f.log", ctx).startswith("tail -n 5 ")


def test_secrets_name_their_kind_and_ignore_placeholders():
    assert sorted(redact.secrets("Bearer abcdefghijklmnopqrstuvwx and AKIAABCDEFGHIJKLMNOP")) == [
        "AWS access key", "Bearer token"]
    assert redact.secrets("password=hunter22") == ["credential assignment"]
    assert redact.secrets("-----BEGIN RSA PRIVATE KEY-----") == ["private key"]
    assert redact.secrets("eyJhbGciOi.eyJzdWIiOiIx") == ["JWT"]
    assert redact.secrets("password=<value>, a token, Bearer <token>, sk-short") == []


def _rx(kind):
    return next(rx for k, rx in redact.SECRETS if k == kind)


def test_a_private_key_and_a_jwt_are_matched_whole():
    key = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk\nQQQQ\n-----END OPENSSH PRIVATE KEY-----"
    out = "before\n" + key + "\nafter"
    assert redact.secrets(out) == ["private key"]
    assert _rx('private key').sub("", out) == "before\n\nafter"  # the body goes with the header
    assert _rx('private key').sub("", "-----BEGIN PRIVATE KEY-----\nzzzz") == ""  # no END: to the end
    jwt = "eyJhbGciOiJI.eyJzdWIiOiIx.SIGNATURE_part-9"
    assert _rx('JWT').sub("", f"t={jwt};") == "t=;"


@pytest.mark.parametrize("text, kind", [
    ("DB_PASSWORD=abc123", "credential assignment"),
    ("MY_API_KEY: abc123", "credential assignment"),
    ('{"password": "abc123"}', "credential assignment"),
    ("password=changeme123", "credential assignment"),
    ("password=exampleS3cret", "credential assignment"),
    ("postgres://ana:p4ss@db.local/app", "URL with credentials"),
    ("redis://:pw@h", "URL with credentials"),
    ("postgres://ana:p@ss@db", "URL with credentials"),
    ("password=example-x", "credential assignment"),
])
def test_wider_credential_forms_are_secrets(text, kind):
    assert redact.secrets(text) == [kind]


@pytest.mark.parametrize("text", ["password=changeme", "password=<value>", "https://host/path", "postgres://ana:<pw>@db",
                                  "the token is rotated daily", '{"password": "<redacted>"}'])
def test_placeholders_and_prose_are_not_secrets(text):
    assert redact.secrets(text) == []


@pytest.mark.parametrize("cmd, shape", [
    ("/usr/bin/curl -su ana:pw http://x", "<path> -su <arg>:<arg> <url>"),  # the path pass takes the command word too
    ("(curl -u ana:pw http://x)", "(curl -u <arg>:<arg> <url>)"),
    ("curl -u ana:pw \\\nhttp://x", "curl -u <arg>:<arg> <url>"),
    ("X=1 curl -u ana:pw http://x", "<arg>=<value> curl -u <arg>:<arg> <url>"),
    ("curl --proxy-user a:b http://x", "curl --proxy-user <arg>:<arg> <url>"),
    ("echo `curl -U a:b http://x`", "echo `curl -U <arg>:<arg> <url>`"),
    ("/usr/bin/mysqladmin -phunter2 status", "<path> -p<value> status"),
    ("mariadb-dump -phunter2 db", "<arg> -p<value> <arg>"),
])
def test_client_credentials_in_more_forms(cmd, shape):
    assert redact.redact_command(cmd, CTX) == shape


@pytest.mark.parametrize("cmd, shape", [
    ('curl -H "$(x)" -u a:b url', 'curl -H "$(<arg>)" -u <arg>:<arg> <arg>'),
    ("mysql -h $(x) -phunter2", "mysql -h $(<arg>) -p<value>"),
    ("tool --token \\\nabc", "tool --token <value>"),
    ("docker login -u ana -p hunter2", "docker login -u <arg> -p <arg>"),
    ("sshpass -p hunter2", "sshpass -p <arg>"),
    ("sshpass -phunter2", "sshpass -p<value>"),
    ("echo Deploying acme to prod", "echo <arg> <arg> <arg> <arg>"),
    ("for c in acme beta; do echo $c; done", "for <arg> in <arg> <arg>; do echo $<arg>; done"),
    ("find acme", "find <arg>"),
    ("grep -r acme src", "grep -r <arg> <arg>"),
    ("pytest acme", "pytest <arg>"),
    ("kubectl -n acme", "kubectl -n <arg>"),
    ("ssh prod-acme", "ssh <arg>"),
    ("psql -d acme", "psql -d <arg>"),
    ("git stash push acme", "git stash push <arg>"),
    ("git remote add acme x", "git remote add <arg> <arg>"),
    ("sudo -u ana rm acme", "sudo -u <arg> rm <arg>"),
    ("sudo -uana rm acme", "sudo -u<value> rm <path>"),
    ("sudo -E rm acme", "sudo -E rm <path>"),
    ("env -i acme x", "env -i <arg> <arg>"),
    ("command -v acme", "command -v <arg>"),
    ("git status # acme plan", "git status"),
    ("git status # don't plan\ngit log", "git status\ngit log"),
    ('echo "a # b"', 'echo "<text>"'),
    ("curl http://x/#frag", "curl <url>"),
    ("curl -Ua:b x", "curl -U<value> <arg>"),
    ("curl -suana:pw x", "curl <arg>:<arg> <arg>"),
    ("tool -suana:pw", "tool <arg>:<arg>"),
    ("tool -Ua:b --max_depth=3 --dry-run -- x", "tool <arg>:<arg> --max_depth=<value> --dry-run -- <arg>"),
    ("git push acme-remote main", "git push <arg> <protected>"),
    ("cat <acme>out", "cat <<arg>><arg>"),
    ("tool >acme", "tool ><arg>"),
    ("tool <<< acme", "tool <<< <arg>"),
    ("x=$(cat acme) git status", "<arg>=$(cat <path>) git status"),
    ("timeout 30 git push origin acme-fix", "timeout 30 git push origin <arg>"),
    ("echo $(tool acme) acme", "echo $(tool <arg>) <arg>"),
    ("echo `tool acme` acme", "echo `tool <arg>` <arg>"),
    ("(tool acme) && tool acme", "(tool <arg>) && tool <arg>"),
    ("sshpass -p 123456", "sshpass -p <arg>"),
    ("tool HEADER HEAD~3 HEAD^", "tool <arg> HEAD~3 HEAD^"),
    ('echo "<acme>"', 'echo "<text>"'),
    ("git cherry-pick acme-a..acme-b", "git cherry-pick <path><arg>"),
])
def test_arguments_are_redacted_by_default(cmd, shape):
    assert redact.redact_command(cmd, CTX) == shape


@pytest.mark.parametrize("cmd, shape", [
    ("cat <<\\EOF\nacme\nEOF\nls", "cat <<EOF\n<text>\nEOF\nls"),
    ("cat <<-A <<'B' | sh\nacme\nA\nbeta\nB\nls", "cat <<EOF <<EOF | sh\n<text>\nEOF\n<text>\nEOF\nls"),
    ("cat <<EOF\nacme\nnever ends", "cat <<EOF\n<text>"),
    ("cat <<ACME_PLAN\nacme\nACME_PLAN", "cat <<EOF\n<text>\nEOF"),
])
def test_heredocs_lose_their_bodies(cmd, shape):
    assert redact.redact_command(cmd, CTX) == shape


@pytest.mark.parametrize("cmd, shape", [
    ("(see http://x/a(b)c)", "(<arg> <url>)"),
    ("(curl http://x)", "(curl <url>)"),
    ("echo `curl http://x`", "echo `curl <url>`"),
    ("curl http://x/a(b", "curl <url>"),
])
def test_a_url_loses_its_whole_tail_and_keeps_the_shells_closing_paren(cmd, shape):
    assert redact.redact_command(cmd, CTX) == shape


def test_a_branch_list_reads_origin_head_as_nothing(repo):
    sh(repo, "git", "update-ref", "refs/remotes/origin/feat-x", "HEAD")
    sh(repo, "git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/feat-x")
    assert "feat-x" in redact.branches(repo) and "origin" not in redact.branches(repo)


@pytest.mark.parametrize("cmd, shape", [
    ('X="$(date +%s)" curl -u ana:pw https://h/x -H "a: b"', '<arg>="$(date +%<arg>)" curl -u <arg>:<arg> <url> -H "<text>"'),
    ('B="$(git branch --show-current)"; sshpass -p hunter2 ssh prod-acme "uptime"',
     '<arg>="$(git branch --show-current)"; sshpass -p <arg> ssh <arg> "<text>"'),
    ('tool --name="$(cat a)" acme beta "z"', 'tool --name="$(cat <path>)" <arg> <arg> "<text>"'),
    ('X="$(y)"; tool acme "z"', '<arg>="$(<arg>)"; tool <arg> "<text>"'),
    ('BRANCH="$(git branch --show-current)" && git push origin "$BRANCH"',
     '<arg>="$(git branch --show-current)" && git push origin "<text>"'),
    ('X="a"acme curl -u a:b u', '<arg>="<text>"<arg> curl -u <arg>:<arg> <arg>'),
])
def test_a_quoted_value_in_an_assignment_leaves_the_rest_redacted(cmd, shape):
    assert redact.redact_command(cmd, CTX) == shape


def test_an_unknown_command_word_goes_like_any_other_word():
    assert redact.redact_command('X="$(a b)"\nhunter2 acme ana "z"', CTX) == '<arg>="$(<arg> <arg>)"\n<arg> <arg> <arg> "<text>"'
    assert redact.redact_command("acme-deploy --dry-run", CTX) == "<arg> --dry-run"


@pytest.mark.parametrize("cmd", ['x=a"$(b c)" acme', "tool --token a\"$(b c)\" acme \"z\" beta", "it's acme \"z\" beta 'q' ana",
                                 'tool " acme beta'])
def test_a_quote_cut_or_left_unpaired_does_not_switch_the_pass_off(cmd):
    shape = redact.redact_command(cmd, CTX)
    assert not {"acme", "beta", "ana"} & set(shape.replace('"', " ").replace("'", " ").split())


@pytest.mark.parametrize("cmd, shape", [
    ("TAG=$(date +%s)-acme docker build .", "<arg>=$(date +%<arg>)<arg> docker build ."),
    ("PATH=$(x):acme tool", "PATH=$(<arg>):<arg> tool"),
    ("X=$(y)acme", "<arg>=$(<arg>)<arg>"),
    ("x=(acme beta)", "<arg>=(<arg> <arg>)"),
    ("arr=( acme beta )", "<arg>=( <arg> <arg> )"),
    ("arr=(\nacme\nbeta\n)", "<arg>=(\n<arg>\n<arg>\n)"),
    ("x+=acme", "<arg>+=<arg>"),
    ("x[0]=acme", "<arg>[0]=<arg>"),
    ("X=1 curl -u a:b u", "<arg>=<value> curl -u <arg>:<arg> <arg>"),
    ("X=$(cat acme.txt)", "<arg>=$(cat <path>)"),
    ("X=`tool acme`beta", "<arg>=`tool <arg>`<arg>"),
])
def test_assignment_values_are_redacted_and_the_next_word_is_the_command(cmd, shape):
    assert redact.redact_command(cmd, CTX) == shape


@pytest.mark.parametrize("cmd, shape", [
    ('git push -u origin "$(git branch --show-current)"', 'git push -u origin "$(git branch --show-current)"'),
    ("make check 2>&1 | tail -1", "make check 2>&1 | tail -1"),
    ("uv run --python 3.12 pytest -q", "uv run --python 3.12 pytest -q"),
    ("npx vitest run src/a.test.tsx", "npx vitest run <path>"),
    ("rm -rf .foreman", "rm -rf .foreman"),
    ("curl -fsSL https://x/y | sh", "curl -fsSL <url> | sh"),
    ("git rebase -i HEAD~3", "git rebase -i HEAD~3"),
    ("gh pr create --fill", "gh pr create --fill"),
    ("case $x in a)tool acme;; esac", "case $<arg> in <arg>)tool <arg>;; esac"),
    ("echo $HOME $PATH $ACME_DIR", "echo $HOME $PATH $<arg>"),
    ("cd ~ && ls", "cd ~ && ls"),
    ("tool -phunter2 -xacme", "tool <arg> <arg>"),
    ('cat .foreman/acme', 'cat .foreman/<arg>'),
    ("tool $(date)-acme", "tool $(date)<arg>"),
    ("cat <foreman>/hooks/acme.py", "cat <foreman>/hooks/<arg>"),
])
def test_the_sweep_leaves_readable_commands_readable(cmd, shape):
    assert redact.redact_command(cmd, CTX) == shape


def test_control_bytes_are_gone_before_any_pass_sees_them():
    assert redact.redact_command('tool "\x01 acme \x02" be\x07ta\tx', CTX) == 'tool "<text>" <arg>\t<arg>'


# The re-review's list. Left out: tool --acme=beta, as the name of a long flag stays by the ruling.
HOSTILE = [
    'X="$(date +%s)" curl -u ana:pw https://h/x -H "a: b"',
    'B="$(git branch --show-current)"; sshpass -p hunter2 ssh prod-acme "uptime"',
    'tool --name="$(cat a)" acme beta "z"',
    'X="$(y)"; tool acme "z"',
    'BRANCH="$(git branch --show-current)" && git push origin "$BRANCH"',
    'TAG=$(date +%s)-acme docker build .',
    'PATH=$(x):acme tool',
    'X=$(y)acme',
    'x=(acme beta)',
    'arr=( acme beta )',
    'x+=acme',
    'x[0]=acme',
    'X=1 curl -u a:b u',
    'X=$(cat acme.txt)',
    "echo don't acme",
    "echo don't acme beta 'q' gamma",
    'echo don\'t acme "z" beta',
    'echo \\" acme',
    'echo \\" acme " beta',
    'echo \\" acme \\" beta',
    "echo \\' acme ' beta",
    'echo a\\"b acme "z" beta',
    'echo "a\\"b" acme "z" beta',
    "echo 'a\\' acme 'b' beta",
    "ls # it's acme",
    'ls # "acme',
    "echo x # don't",
    'echo a#b acme "z" beta',
    'echo "unterminated acme beta',
    "echo 'unterminated acme beta",
    'echo acme "unterminated beta',
    'tool "a" \'b acme',
    'tool \'a\' "b acme \\" beta',
    'X="$(echo "a b")" acme beta',
    'echo "$(echo "acme beta")" gamma',
    'echo "$(echo "a b")" acme "c" beta',
    'echo "$(echo \'a b\')" acme "c" beta',
    'echo "x $(echo "acme") y" beta',
    'echo "x $(echo "acme") y" beta "z" gamma',
    'tool "`echo acme`" beta',
    'tool "`echo "acme"`" beta',
    'tool "a`echo acme`b" beta "z" gamma',
    'X="`cat acme`" tool beta',
    'X=`cat "acme beta"` tool gamma',
    'echo $\'a\\\'b\' acme "z" beta',
    "echo $'acme' beta",
    "echo $'it\\'s' acme 'z' beta",
    'echo $"acme" beta',
    "X=$'acme' tool beta",
    "tool 'it'\\''s acme' beta",
    'tool \'a\'\\\'\' acme\' "b" beta',
    'tool "a"\'b\' acme',
    'tool "a""b" acme',
    'tool "a" acme',
    'tool"a" acme',
    "X='a'b acme beta",
    'X="a"b"c" acme beta',
    'X="a" Y="b" acme beta',
    'X="a";Y="b" acme beta',
    'acme"b" gamma',
    '"acme" beta gamma',
    "'acme' beta gamma",
    '"$(tool)" acme beta',
    '"$X" acme beta',
    '$(tool) acme beta',
    '$(tool)acme beta gamma',
    '`tool` acme beta',
    '`tool`acme beta',
    '(tool acme) beta',
    '(tool acme)beta gamma',
    '{ tool acme; } beta',
    'foo() { bar acme; }',
    'if tool acme; then bar beta; fi',
    'for x in acme beta; do echo $x; done',
    'case acme in beta) tool gamma;; esac',
    'tool <<<"acme" beta',
    'tool <<< acme beta',
    'tool < acme > beta',
    'tool 2>acme beta',
    'tool >acme beta',
    'tool &>acme',
    'tool <(cat acme) beta',
    'tool >(cat acme) beta',
    'x=$((1+acme))',
    'x=$(( acme + beta ))',
    'echo ${x:-acme}',
    'echo ${x:-acme beta}',
    'echo "${x:-acme}" beta',
    'X=${y:-acme} tool',
    'X=${y:-"acme"} tool beta',
    'X=${y:-"acme beta"} tool gamma "z" zeta',
    '${acme}beta gamma',
    'x[acme]=beta',
    'x[$(y)]=acme',
    'x[acme]=$(y)',
    'x[a b]=acme',
    'x[0]+=acme',
    'x+=(acme beta)',
    'x[0]=(acme)',
    'declare -a x=(acme beta)',
    'export X=acme',
    'export X="acme" Y=beta',
    'export X=$(y) acme',
    'local x=(acme) beta',
    'tool x=(acme) beta',
    'tool x=acme beta',
    'tool x= acme beta',
    'X= acme beta',
    'X= tool acme',
    'X=; tool acme',
    '=acme beta',
    'a=b=acme',
    'a=(b)acme gamma',
    'x=(a b)acme gamma',
    'x=(a b) acme gamma',
    'x=(a b); acme gamma',
    'x=(a $(b c) d) acme',
    'x=(a `b c` d)',
    'x=(a "b c" d) acme',
    'x=( $(cmd acme) beta ) gamma',
    'tool $(x)$(y) acme',
    'tool "a"$(y)acme beta',
    'tool a"b c"acme beta',
    'tool a"b"acme"c"beta gamma',
    'tool --x="a"acme beta',
    'tool --x=$(y)acme beta',
    'tool --x=`y`acme beta',
    'tool -x"acme" beta',
    'tool -xacme beta',
    'X="$(echo "a" acme "b")" beta',
    'X="$(a) b" acme beta "c" gamma',
    'X="a $(b)" acme beta',
    'X="$(a)$(b)" acme beta',
    'X="$(a)" "acme" beta',
    'X="$(a" acme beta',
    'X="$(a)" \'it\'\'s\' acme',
    '"X=$(a)" acme beta',
    'tool "--x=$(a b)" acme',
    'tool "$(a)"acme beta',
    'tool "$(a)"acme" beta gamma',
    'tool "$(a b)"; acme beta',
    'tool \'$(a b)\' acme "x" beta',
    'eval "tool acme" beta',
    'bash -c "tool acme \'beta\' gamma" zeta',
    'bash -c "X=\\"$(a)\\" tool acme" beta',
    'sh -c \'echo "acme" beta\' gamma',
    'bash -c \'it\'"\'"\'s acme\' beta',
    'tool \\ acme beta',
    'tool \\acme',
    'tool a\\ b acme',
    'echo \\\\" acme " beta',
    'echo \\\\\\" acme beta',
    'tool "\\\\" acme "\\\\" beta',
    "tool '\\' acme",
    'X="$(echo "acme beta")"',
    'X="$(echo "acme beta gamma")" tool zeta',
    'X="$(mysql -e "select acme from beta")"',
    'X="$(curl -u x "ana hunter2")"; ls',
    '"$(echo "acme beta")"',
    'tool; "$(echo "acme beta")"',
    'export X="$(echo "acme beta")"',
    'tool --x="$(echo "acme beta")"',
    'tool "$(echo "acme beta")"',
    'Y=1 X="$(echo "acme beta")" tool',
    'x=a"$(b acme)"',
    'x=a"$(b acme beta)" gamma',
    'x=a"$(b acme)" tool beta',
    "x=a'$(b acme)' gamma",
    'x=a"b acme"',
    'TAG=v"$(date +%s) acme" tool',
    'tool --token a"$(b acme)" beta',
    'curl -ua"$(b acme)" beta',
    'case $x in a)ls acme;; esac',
    'case $x in *)echo acme beta;; esac',
    'case $x in a) ls acme;; esac',
    '`a`b acme gamma',
    '$(tool)acme beta',
    '(a)b acme',
    'arr=(\n$(a)\nacme\nbeta\n)',
    'arr=($(a) acme\nbeta gamma)',
    'arr=(`a`\nacme beta\n)',
    'arr=("a"\nacme\n)',
    'arr=((a)\nacme\n)',
    'tool &>acme beta',
    'tool &>>acme',
    'tool |& tee acme',
    'tool 2>&1 >acme',
    'echo "$(a) acme beta $(c)"',
    'echo "$(a) | acme beta; gamma $(c)"',
    'echo "$(a) acme" "$(c)"',
    'x[acme]="z"',
    'x[acme]=`y`',
    'x[acme]=(beta)',
    "echo don't # acme 'x' beta",
    "echo don't\n# acme beta",
    "echo don't\ntool acme\ntool beta",
    "ls # it's\ntool acme 'z' beta",
    'ls # "\ntool acme "z" beta',
    'X="$(cat <<EOF\nacme beta\nEOF\n)" tool gamma',
    'X="$(cat <<\'EOF\'\nit\'s acme\nEOF\n)"; tool beta "z" gamma',
    'cat <<EOF "$(x)"\nacme\nEOF\ntool acme "z" beta',
    'cat <<\'EOF\'\nit\'s\nEOF\necho don\'t acme "z" beta',
    'tool "$(cat <<EOF\nbody "q\nEOF\n)" acme "z" beta',
    'tool <<EOF; X="$(a b)" tool acme "z"\nbody\nEOF',
    'tool \\\n  "$(a b)" \\\n  acme',
    'X="$(a\nb)" acme beta',
    'X="$(a)\nb" acme beta',
    'tool "\x01 acme \x02" beta',
    'tool \\"\x01 acme beta\x02\' gamma',
    'x=$(tool "acme beta" gamma)',
    'x=$(tool "$(y "acme beta")" gamma)',
    'x="$(tool "$(y "acme beta")" gamma)" zeta',
    'X="$(curl -H "X-Key: hunter2" https://h)"',
    'OUT="$(sshpass -p "$P" ssh "root prod-acme")"',
    'R="$(mysql -h db -e "use acme; select 1")"',
    'case $x in a)mysql hunter2 x;; esac',
    'case $x in a)tool acme;; b)ssh prod-acme uptime;; esac',
    'x=(a\n"$(b)"\nacme\n)',
    'x=(\n(a)\nacme\n)',
]
PRIVATE = ("acme", "beta", "gamma", "ana", "hunter2", "prod-acme")
ALL = HOSTILE + sorted({case[0] for f in list(globals().values()) for m in getattr(f, "pytestmark", [])
                        if m.name == "parametrize" and m.args[0] == "cmd, shape" for case in m.args[1]})


@pytest.mark.parametrize("cmd", ALL)
def test_no_private_word_survives_and_every_word_satisfies_the_sweep(cmd):
    shape = redact.redact_command(cmd, CTX)
    assert not [w for w in PRIVATE if w in shape]
    assert all(redact.kept(m) for m in redact.WORD.finditer(shape))
    assert redact.sweep(shape) == shape
