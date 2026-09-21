# Codex status notifications

This repository contains a small status-only relay for Codex completion
alerts. A Codex host sends one fixed event to the authenticated relay. The
relay validates the exact event schema and publishes the fixed message
`Codex turn completed.` to a private self-hosted [ntfy](https://ntfy.sh)
topic.

The notification says that a turn ended. It does not mean that a task
succeeded, and it intentionally carries no prompt, assistant text, code,
working directory, session title, or transcript.

The public boundary is the relay at `/v1/codex/turn-complete`. It accepts only
the following JSON object:

```json
{"type":"agent-turn-complete"}
```

Unknown fields and arbitrary message text are rejected before ntfy is called.
The relay's ntfy client also has a fixed message body, so request content can
never become a notification body.

## Components

- `src/codex_notify.py` is the dependency-free Codex `notify` command. It
  reads Codex's one JSON argument, filters to `agent-turn-complete`, and sends
  the fixed event over outbound HTTPS.
- `src/codex_notify_gateway.py` is the authenticated HTTP relay. It should be
  the only public application route.
- `src/ntfy_client.py` publishes the fixed message to ntfy over the private
  service network.
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
NTFY_BASE_URL=http://ntfy
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

Configure ntfy as a private instance with `auth-default-access: deny-all`.
Use separate regular users and tokens:

- a publisher user with `write-only` access to the one topic, used only by
  the relay;
- a reader user with `read-only` access to the one topic, used by the iPhone.

ntfy documents that access tokens currently grant the full access of their
user account. Dedicated users and topic ACLs are therefore necessary; do not
reuse an admin token. See the [ntfy access-control documentation](https://docs.ntfy.sh/config/#access-control)
and [token documentation](https://docs.ntfy.sh/config/#access-tokens).

## Configure the Codex host

The public relay URL and token are local user settings. Prefer a token file so
the token does not appear in shell history or process listings. The file must
contain only the relay bearer token and should be readable only by the user.

Set these user environment variables on the work computer, replacing the
placeholders with the deployment's public relay URL and the relay token file:

```text
CODEX_NOTIFY_URL=https://<relay-host>/v1/codex/turn-complete
CODEX_NOTIFY_TOKEN_FILE=<path-to-user-token-file>
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

The official Codex configuration uses `notify` for an external program and
currently documents the `agent-turn-complete` event. `notify` is a host-side
configuration: the ChatGPT desktop application's own turn and permission
alerts remain controlled by its Settings > Notifications panel. If a
particular Desktop build does not invoke the host `notify` command, keep its
built-in notifications enabled or use the connected Codex CLI/IDE host for
this relay.

Official references:

- [Codex advanced configuration: notifications](https://developers.openai.com/docs/config-file/config-advanced#notifications)
- [Codex notifications by surface](https://developers.openai.com/docs/notifications)

## Configure the iPhone

Install the [ntfy iOS app](https://apps.apple.com/app/ntfy/id1625396347). Use
the same canonical HTTPS base URL everywhere:

1. Set ntfy's Default Server to `https://<ntfy-host>` exactly as configured by
   the server's `base-url`.
2. Add the topic name and authenticate with the reader account. The app can
   use the reader username/password; recent versions also support a custom
   `Authorization: Bearer ...` header under Settings > Advanced > Custom
   headers. Do not use the relay or publisher token on the phone.
3. Allow notifications and send one test completion event from the work
   host.

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

Verify that the iPhone receives exactly `Codex turn completed.`. An event that
contains a prompt, code, `cwd`, or any extra field must receive a client error
from the relay and must not create an ntfy message.

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
