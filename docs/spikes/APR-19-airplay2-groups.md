# Spike: AirPlay 2 group streaming via pyatv

Linear: [APR-19](https://linear.app/apricotdotcool/issue/APR-19) — de-risks [APR-18](https://linear.app/apricotdotcool/issue/APR-18).

## TL;DR

pyatv 0.17.0 exposes no native AirPlay 2 group-streaming API. Fan-out must
happen at the application layer by running one independent `stream_file`
session per receiver, all fed from a single capture. This matches the
"keep playing even if some fail" requirement cleanly and lets us implement
APR-21 and APR-22 without touching pyatv internals.

## What I looked at (pyatv 0.17.0)

- `pyatv.connect(config, loop, ...)` accepts a single `BaseConfig`. No list
  form, no follower/group kwarg.
- `pyatv.interface.Stream.stream_file(file, ...)` — one file or one
  `asyncio.StreamReader`, returns when the stream ends. No group kwargs.
- `pyatv.protocols.raop.RaopStream.stream_file` wraps a single `StreamClient`
  + `StreamContext` per call: one RTP socket, one RTSP session, one
  miniaudio decoder per receiver.
- `pyatv.protocols.raop.protocols.airplayv2` — the only "group" reference
  in the tree is the hardcoded `"groupContainsGroupLeader": False` field
  in the RTSP SETUP body (sender self-description).
  `"senderSupportsRelay": False` is also hardcoded, explicitly disabling
  the receiver-to-receiver relay flow that Apple's native AirPlay 2 groups
  use.
- Searched the installed pyatv tree for `group`, `leader`, `follower` — no
  code paths, no unused hooks, nothing to piggyback on.

No branch of pyatv supports multi-receiver group sessions today.

## Recommended approach

Run N parallel single-device sessions sharing one capture:

- One `pyatv.scan` in `resolve_targets` (APR-21), matching each configured
  name and applying creds/password per device.
- One `pyatv.connect` + `StreamingSession`-equivalent per resolved target
  (APR-22).
- Each session gets its own `asyncio.StreamReader` prefixed with the WAV
  header — same `_wav_header` logic as today's `StreamingSession`.
- The capture loop writes each PCM chunk to every live session's reader
  via `feed_data`.
- A session's `stream_file` failing only cancels that session; siblings
  keep going. Hard-fail only when the surviving-session count hits zero.

This preserves the current fast-path for single-target config — N=1 is
identical to today's behavior.

## Constraints this puts on the rest of APR-18

- **APR-21 (resolve_targets).** Return `list[BaseConfig]` in configured
  order. Leader-first is a naming convention for log output, not a
  protocol requirement. Any device can fail resolution independently;
  log + skip and return whatever succeeded. Reject an empty surviving
  list so the pipeline still surfaces "all targets missing" as a hard
  error.
- **APR-22 (GroupStreamingSession).** Must hold one `StreamReader` per
  receiver, not a shared reader. `stream_file` consumes its reader until
  EOF, so a dropped follower needs `feed_eof()` on just that reader for
  its `stream_file` task to return cleanly without affecting siblings.
  Mid-stream failure bubbles out of the per-session consumer task —
  surface it via the same `failed()` / `exception()` contract that
  `StreamingSession` already exposes, evaluated per device. The group
  session as a whole only reports failure when the last surviving device
  drops.
- **Sync drift.** No shared timing anchor means receivers in the same
  physical room will drift enough to be audible (tens of ms within a
  few minutes). APR-18 explicitly accepts this tradeoff. Worth a callout
  in README + `cusp.toml.example` when APR-23 lands.
- **CPU + network.** N RTSP sessions + N RTP sockets + N miniaudio
  decoders. Cheap at 2-4 receivers on Pi-class hardware. If we ever need
  6+ receivers, revisit by pre-encoding once and fanning out at the RTP
  layer, which would require upstream pyatv changes.
- **Volume / password / paired credentials.** All are per-receiver in
  pyatv today. APR-21 should apply `config.airplay_password` to every
  resolved target's RAOP service, matching the current single-target
  behavior. Stored paired credentials are indexed by device identifier,
  so they fan out naturally.

## Explicitly out of scope

- Apple's "real" AirPlay 2 group (single timing anchor, receivers wired
  together by the sender's group session) — not reachable via pyatv today
  and would require significant upstream protocol work. Not worth
  blocking APR-18 on it.
