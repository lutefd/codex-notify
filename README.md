# Codex and Claude status notifications

This repository contains a small status-only relay for Codex and Claude Code
completion alerts. Each host sends one bounded event to the authenticated
relay. The relay validates the event schema and publishes a fixed message to
one private self-hosted [ntfy](https://ntfy.sh) topic. Both tools use the same
topic, reader account, publisher credential, and public relay route.

The notification says that a turn ended. It does not mean that a task
succeeded, and it intentionally carries no prompt, assistant text, code,
working directory, session title, or transcript.

The public boundary is the relay at `/v1/codex/turn-complete`. It accepts only
these status-only forms:

```json
{"type":"agent-turn-complete"}
{"type":"agent-turn-complete","source":"claude"}
{"type":"agent-turn-complete","source":"claude","label":"Build API"}
```

The `source` value is either `codex` or `claude`; the source may be omitted for
backward-compatible Codex events. The optional label is a user-chosen local
name, limited to 48 letters, numbers, spaces, `.`, `_`, and `-`, without
leading or trailing spaces. Unknown fields and arbitrary message text are
rejected before ntfy is called. The relay's ntfy client uses fixed source
messages and appends only the validated label, so request content can never
become a notification body.

The fixed messages are `Codex turn completed.` and `Claude turn completed.`.
When a label is present, the notification body appends it in brackets, for
example `Claude turn completed. [Research]`.

## Components

- `src/codex_notify.py` is the dependency-free Codex `notify` command. It
  reads Codex's one JSON argument, filters to `agent-turn-complete`, resolves
  an optional local label from Codex's opaque `thread-id`, and sends the fixed
  event over outbound HTTPS.
- `src/claude_notify.py` is the dependency-free Claude Code `Stop` hook. It
  reads only the hook name, loop-guard flag, and opaque `session_id`; it never
  reads response text or transcript files.
- `src/codex_notify_gateway.py` is the authenticated HTTP relay. It should be
  the only public application route.
- `src/ntfy_client.py` publishes the fixed message to ntfy over the private
  service network.
- `src/status_labels.py` and `bin/codex-notify-label` keep the optional
  per-session label map on the host. The map is never sent to the server.
- `compose.yaml`, ntfy configuration, and tunnel route files are deployment
  owned. Keep tokens and passwords outside Git.

Run the tests with Python 3.10 or newer:

```sh
make test
```

The helper has no third-party Python dependency. It works on Linux, macOS,
and Windows when Python is installed and the Codex host can execute a Python
command.

## Server contract

The deployment should provide these values to the relay without committing
them:

```text
CODEX_NOTIFY_GATEWAY_TOKEN_FILE=/run/secrets/codex_notify_gateway_token
NTFY_BASE_URL=http://codex-notify-ntfy
NTFY_TOPIC=<private-topic>
NTFY_TOKEN_FILE=/run/secrets/ntfy_publisher_token
NTFY_ALLOW_INSECURE_HTTP=1
```

`NTFY_ALLOW_INSECURE_HTTP=1` is only for the isolated relay-to-ntfy Docker
network. The work-computer endpoint must remain HTTPS at the Cloudflare
tunnel. The relay should listen on `0.0.0.0:8080` inside its container and
expose only that service through the tunnel. `/healthz` returns `ok`; the
publish route returns `204` on success and no response body containing event
data.

The current tunnel route contract is:

- ntfy subscription/default server: `https://notify.luisdourado.com`;
- work-computer relay endpoint:
  `https://codex-notify.luisdourado.com/v1/codex/turn-complete`.

Cloudflare DNS records and remote tunnel ingress must resolve these hostnames
before a work computer or iPhone can connect.

The cloudflared connector already joins the shared Docker network. Configure
its public-hostname services with the unique network aliases below:

- `notify.luisdourado.com` → `http://codex-notify-ntfy:80`;
- `codex-notify.luisdourado.com` → `http://codex-notify-gateway:8080`.

If the existing remote tunnel still targets the former generic names during
the migration, temporarily include `compose.cloudflare-compat.yaml` when
starting the stack. It adds `ntfy` and `gateway` only after the current shared
network was checked for collisions. Remove that overlay as soon as the remote
routes use the unique names above.

The current server uses that compatibility overlay while the remote dashboard
routes still point at the former names. The intended migration is to change
the two dashboard targets to `codex-notify-ntfy:80` and
`codex-notify-gateway:8080`, restart without the compatibility overlay, and
then remove the legacy aliases. The overlay is a temporary bridge rather than
part of the permanent network contract.

The default Compose stack publishes no host ports. While Cloudflare routes are
pending, an optional `compose.tailscale.yaml` override can bind diagnostics to
the server's Tailscale address only. That gives Tailscale clients the gateway
health URL `http://<tailscale-ip>:18080/healthz` and ntfy health URL
`http://<tailscale-ip>:18081/v1/health`; it does not create a public WAN
listener. The alternate ntfy URL is for diagnostics or private polling only.
For iOS instant delivery, keep the canonical `https://notify.luisdourado.com`
server name, or use split-horizon DNS so the same name resolves privately with
a matching certificate.

The base stack also attaches ntfy to the dedicated
`codex-notify-ntfy-egress` bridge. This gives ntfy outbound HTTPS access to
`https://ntfy.sh` for iOS wakeups while the gateway remains on the internal
shared network. The optional Tailscale bridge is reserved for private
diagnostics and should be enabled only when those ports are needed.

The active server currently uses all three files while the remote tunnel still
targets the legacy aliases. Start or recreate it with the current Tailscale
IPv4:

```sh
TAILSCALE_BIND_IP=100.90.77.90 \
  docker compose \
    -f compose.yaml \
    -f compose.tailscale.yaml \
    -f compose.cloudflare-compat.yaml \
    up -d --build
```

To force a full container refresh while keeping the same active overlays:

```sh
TAILSCALE_BIND_IP=100.90.77.90 \
  docker compose \
    -f compose.yaml \
    -f compose.tailscale.yaml \
    -f compose.cloudflare-compat.yaml \
    up -d --build --force-recreate
```

The shared `cloudflare_ingress` network is internal, so the override adds a
small project-local bridge solely to make those Tailscale-bound ports work.
The base file remains the Cloudflare-only deployment contract.

Configure ntfy as a private instance with `auth-default-access: deny-all`.
Use separate regular users and tokens:

- a publisher user with `write-only` access to the one topic, used only by
  the relay;
- a reader user with `read-only` access to the one topic, used by the iPhone.

ntfy documents that access tokens currently grant the full access of their
user account. Dedicated users and topic ACLs are therefore necessary; do not
reuse an admin token. See the [ntfy access-control documentation](https://docs.ntfy.sh/config/#access-control)
and [token documentation](https://docs.ntfy.sh/config/#access-tokens).

## Retrieve credentials safely

The deployment host keeps connection material outside this repository. The
protected files and their purposes are:

- `/home/luis/.config/codex-notify/gateway.token` — the bearer token the work
  computer sends to the public relay;
- `/home/luis/.config/codex-notify/publisher.token` — the relay's internal
  ntfy publisher credential; keep it on the server;
- `/home/luis/.config/codex-notify/connection.env` — server-side connection
  details, including `NTFY_PHONE_USERNAME`, `NTFY_PHONE_PASSWORD`, and
  `NTFY_TOPIC` for the iPhone reader.

Retrieve only the values needed for a device through an approved secure
channel or password manager. Do not print these files, paste their contents in
chat, place values in shell history, or commit them. The work computer needs a
protected copy of `gateway.token`; the iPhone needs the reader username,
reader password, and topic from `connection.env`. The publisher token is not
used by either device.

## Configure the Codex host

The public relay URL and token are local user settings. Prefer a token file so
the token does not appear in shell history or process listings. The file must
contain only the relay bearer token and should be readable only by the user.

Set these user environment variables on the work computer, using a protected
local copy of `gateway.token`:

```text
CODEX_NOTIFY_URL=https://codex-notify.luisdourado.com/v1/codex/turn-complete
CODEX_NOTIFY_TOKEN_FILE=<path-to-user-token-file>
# Optional: use one explicit path for the local per-session label map.
CODEX_NOTIFY_LABELS_FILE=<path-to-user-labels-file>
```

Then put this in the user-level Codex configuration file (`~/.codex/config.toml`
on Unix-like systems, or the corresponding `%USERPROFILE%/.codex/config.toml`
on Windows):

```toml
notify = ["python3", "/absolute/path/to/codex-notify/src/codex_notify.py"]
```

Keep this key at the user level. Codex ignores `notify` in a project-local
`.codex/config.toml` because notification commands are host-wide settings.

On Windows, use `python` or `py` and a forward-slash path, for example:

```toml
notify = ["py", "C:/Users/you/codex-notify/src/codex_notify.py"]
```

Restart the Codex host after changing user environment variables. On Unix
systems, a desktop app launched from a graphical session may not inherit a
shell's exports, so set persistent user environment variables or launch the
host from a session that has them. The token file is supported on all three
systems.

### Label simultaneous Codex threads

Codex's documented `notify` payload includes an opaque `thread-id`. The helper
uses that ID only as a key in the local labels file; it never sends the ID or
reads the prompt, response, working directory, or transcript. After a
completion has been observed, list the local IDs and assign the labels you
choose:

```sh
python3 /absolute/path/to/codex-notify/bin/codex-notify-label list
python3 /absolute/path/to/codex-notify/bin/codex-notify-label set \
  --source codex --id <opaque-thread-id> --label "Build API"
```

The helper records an opaque ID with an unlabeled entry when it first sees a
completion. If you already know a thread ID, register it before the next turn
to label that notification too. A user-level Codex Desktop `notify` setting is
global, so a single global label cannot distinguish simultaneously running
threads; the ID map is the per-thread mechanism. The first event for an ID
that has not been registered yet is therefore intentionally unlabeled. The
default map is `~/.config/codex-notify/labels.json` on Unix-like systems and
`%APPDATA%/codex-notify/labels.json` on Windows, unless
`CODEX_NOTIFY_LABELS_FILE` is set.

For a separately launched Codex CLI process, `CODEX_NOTIFY_LABEL="Build API"`
can be set in that process's environment. Do not set it globally for a
Desktop installation with multiple threads, because the same value would be
used for every unregistered thread.

The official Codex configuration uses `notify` for an external program and
currently documents the `agent-turn-complete` event. `notify` is a host-side
configuration: the ChatGPT desktop application's own turn and permission
alerts remain controlled by its Settings > Notifications panel. If a
particular Desktop build does not invoke the host `notify` command, keep its
built-in notifications enabled or use the connected Codex CLI/IDE host for
this relay.

The helper and gateway are covered by automated fixture tests. Actual
execution by the user's work Codex Desktop host remains untested until that
host is configured; after DNS and the tunnel route are available, complete one
turn and verify the single fixed notification on the iPhone.

Official references:

- [Codex advanced configuration: notifications](https://developers.openai.com/codex/config-advanced/#notifications)
- [Codex configuration reference](https://developers.openai.com/codex/config-reference/)

## Configure Claude Code

Claude Code uses the same `CODEX_NOTIFY_URL`, protected
`CODEX_NOTIFY_TOKEN_FILE`, optional `CODEX_NOTIFY_LABELS_FILE`, public relay
path, ntfy topic, and iPhone reader account as Codex. It does not need a new
topic or credential. Keep the relay token in a user-readable protected file;
do not place it directly in the hook command.

Add a user-level `Stop` hook to `~/.claude/settings.json` on Unix-like
systems, or `%USERPROFILE%/.claude/settings.json` on Windows:

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /absolute/path/to/codex-notify/bin/claude-notify"
          }
        ]
      }
    ]
  }
}
```

On Windows, use a command such as
`py C:/Users/you/codex-notify/bin/claude-notify`. Ensure the Claude process
inherits the persistent `CODEX_NOTIFY_URL` and `CODEX_NOTIFY_TOKEN_FILE`
variables before restarting it. Run `/hooks` in Claude Code to verify that the
user-level command is loaded.

The official Claude `Stop` event runs when the main agent has finished
responding. It does not run after a user interrupt; API failures use
`StopFailure`, which this status-only setup does not subscribe to. The helper
ignores `stop_hook_active` so it cannot create a continuation loop and always
exits successfully, even if delivery fails, so a notification outage cannot
block Claude. A `Claude turn completed.` alert means that the turn ended; it
does not mean that the task succeeded.

Claude's hook input includes an opaque `session_id`. The helper uses it only
as the local label-map key and ignores `last_assistant_message`,
`transcript_path`, `cwd`, and all other hook fields. After the first Stop event
for a session, assign a label without exposing its content:

```sh
python3 /absolute/path/to/codex-notify/bin/codex-notify-label list
python3 /absolute/path/to/codex-notify/bin/codex-notify-label set \
  --source claude --id <opaque-session-id> --label "Research"
```

Labels are local, bounded, and optional. Both sources continue to publish to
the same ntfy topic, with the same iPhone subscription and reader credential.
When launching separate Claude Code processes, `CLAUDE_NOTIFY_LABEL` can be set
in each process environment for first-turn labeling; a shared global value
would label every unregistered process the same way.

Official references:

- [Claude Code hooks guide](https://code.claude.com/docs/en/hooks-guide)
- [Claude Code hooks reference](https://code.claude.com/docs/en/hooks)

## Configure the iPhone

Install the [ntfy iOS app](https://apps.apple.com/app/ntfy/id1625396347). Use
the same canonical HTTPS base URL everywhere:

1. Set ntfy's Default Server to `https://notify.luisdourado.com` exactly as
   configured by the server's `base-url`.
2. Add the `NTFY_TOPIC` value retrieved from the protected
   `/home/luis/.config/codex-notify/connection.env` and authenticate with the
   `NTFY_PHONE_USERNAME` and `NTFY_PHONE_PASSWORD` values from that file. The
   app's reader username/password fields are the recommended setup. The
   `NTFY_PHONE_PASSWORD` value is an account password, not a bearer token. Do
   not also configure a custom `Authorization` header under Settings >
   Advanced > Custom headers: ntfy does not allow a user account and a custom
   Authorization header for the same server at the same time. Do not use the
   relay or publisher token on the phone.
3. Allow notifications and send one test completion event from the work
   host. Codex and Claude Code publish to this same subscription.

If the app reports that `codex-phone` is not authorized to read the topic,
check the server and topic before changing permissions. The topic must be the
exact `NTFY_TOPIC` value from `connection.env`, with no quotes, spaces, or
trailing characters, and the server must be exactly
`https://notify.luisdourado.com`. The deployed `codex-phone` account is
already read-only for that one topic; it does not have a wildcard read grant.
If using a custom Authorization header instead of the reader account, it must
contain an ntfy access token, not `NTFY_PHONE_PASSWORD`, and the reader account
must be removed from that server entry in the app.

For instant iOS delivery, the ntfy server must set
`upstream-base-url: "https://ntfy.sh"`. The phone must be able to reach the
canonical ntfy URL when it receives the upstream wakeup. ntfy hashes the
exact topic URL for its iOS wakeup flow, so subscribing to a different
Tailscale hostname while publishing through the public hostname can break
instant delivery.

If the iPhone should use Tailscale privately, keep the same canonical hostname
in the app and use split-horizon DNS to resolve that hostname to the
Tailscale-reachable address for the phone. The certificate must still be valid
for the canonical hostname. If split DNS is unavailable, use the canonical
HTTPS Cloudflare URL with ntfy authentication; an alternate private hostname
is a separate iOS identity.

These details follow ntfy's [iOS instant-notification guidance](https://docs.ntfy.sh/config/#ios-instant-notifications),
[known-issues guidance](https://docs.ntfy.sh/known-issues/#ios-app-not-receiving-notifications-anymore),
and [phone app settings](https://docs.ntfy.sh/subscribe/phone/#custom-headers).

## Manual smoke test

The helper can be tested without contacting a server by using the test suite.
For an end-to-end test, send a completion event through the relay with a
placeholder event object. Keep the relay token in a protected file and do not
put it in the command line:

```sh
python3 src/codex_notify.py '{"type":"agent-turn-complete"}'
```

Verify that the iPhone receives exactly `Codex turn completed.` for Codex, or
`Claude turn completed.` for Claude. A registered label appears only in the
fixed bracket suffix. An event that contains a prompt, code, `cwd`, response
text, or any other extra field must receive a client error from the relay and
must not create an ntfy message.

## Secret and history checks

Before committing or publishing, inspect only names and tracked content:

```sh
git diff --check
git status --short
git grep -n -I -E '(^|[^A-Za-z])(tk_[A-Za-z0-9]{29}|Bearer[[:space:]]+[A-Za-z0-9._-]{12,})' -- . ':!tests'
```

Do not print environment values, token files, Docker secrets, or ntfy auth
databases. Revoke and replace a token if it was ever pasted into a command,
log, issue, or commit.
