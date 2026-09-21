# Deployment contract

The repository intentionally separates the public relay from the private
ntfy broker.

```text
Codex host
    │ outbound HTTPS + relay bearer token
    ▼
Cloudflare tunnel → codex-notify-gateway:8080
    │ exact JSON schema; fixed status body
    ▼
private Docker network → codex-notify-ntfy:80 + publisher token
    │ topic ACL and cache
    ▼
ntfy canonical HTTPS host → iPhone reader account
```

The gateway's accepted request is exactly
`{"type":"agent-turn-complete"}`. It rejects unknown keys, prompt text,
assistant output, paths, and oversized bodies. Its ntfy call has no dependency
on the incoming request body and always publishes `Codex turn completed.`.

The gateway must be the only public route for work-computer publishing. The
ntfy service can be externally reachable for the iPhone subscription route,
but its auth policy must be `deny-all` by default. Keep the relay token and
ntfy publisher token separate so a work-computer credential cannot subscribe
or publish directly to arbitrary topics.

The canonical ntfy host is a protocol identity for iOS. `base-url`, the iOS
Default Server, and the URL used for the subscription must match exactly. A
Tailscale-only hostname is suitable only when it is the same canonical name
resolved through split-horizon DNS; a second hostname changes the iOS wakeup
topic hash.

The existing server-wide `cloudflare_ingress` network is the expected tunnel
attachment point. The deployment agent should add the gateway and ntfy
services to that network according to the host's current tunnel route, without
adding host-published ports unless required for local administration.

The base stack gives ntfy a dedicated non-internal egress network for its
HTTPS connection to `https://ntfy.sh` during iOS wakeup delivery. The gateway
does not join that network. Optional Tailscale diagnostics use a separate
project-local bridge for their private host bindings.

Use the unique shared-network service aliases in tunnel ingress rules:

- `https://notify.luisdourado.com` → `http://codex-notify-ntfy:80`;
- `https://codex-notify.luisdourado.com` →
  `http://codex-notify-gateway:8080`.

The base Compose file does not use generic `ntfy`, `gateway`, or
`codex-notify` aliases because the network is shared with other applications.
While the existing remote tunnel still points at the former generic names,
the temporary `compose.cloudflare-compat.yaml` overlay may add only `ntfy` and
`gateway` after a collision check. Remove that overlay after migrating the
dashboard routes to the unique aliases above.

The configured public names are `notify.luisdourado.com` for the ntfy
canonical host and `codex-notify.luisdourado.com` for the relay. The latter's
publish path is `/v1/codex/turn-complete`. DNS and remote tunnel ingress are
control-plane prerequisites and are not represented by local Compose health.
