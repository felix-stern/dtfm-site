# dtfm-site

Static website bundle for [dtfm.dev](https://dtfm.dev), the web presence and Magic Link helper for [`iinozemtsev-lab/dtfm`](https://github.com/iinozemtsev-lab/dtfm).

## Design

- **Zero bundler / zero dependencies**: Plain semantic HTML, CSS custom properties (adaptive light/dark), inline SVGs, and vanilla JavaScript.
- **Zero cookies / zero analytics / zero external assets**: No third-party scripts, fonts, or trackers.
- **Zero-network Magic Link helper (`c.html`)**: Reads `window.location.href` (including `#<key>`) strictly in local browser memory with `Referrer-Policy: no-referrer` and zero `fetch`/`XHR`/`WebSocket` calls.

## Layout

- `index.html` — Main landing page
- `c.html` — Zero-network Magic Link helper card (`/c/<session-id>`)
- `agent.md` — Operational bootstrap manual for AI agents (`/agent.md`, `/agents.md`, `/llms.txt`), kept byte-for-byte in sync with `docs/agent.md` in `iinozemtsev-lab/dtfm`
- `favicon.svg`, `favicon.ico`, `assets/` — Brand icons and logos
- `scripts/verify_site.py` — Hermetic stdlib Python validation suite

## Local Verification

```sh
python3 -W error scripts/verify_site.py
```

## License

Apache-2.0 — see [LICENSE](LICENSE).
