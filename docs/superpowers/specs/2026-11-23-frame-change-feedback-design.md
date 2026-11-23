# Frame change feedback design

Date: 2026-11-23. Status: approved by the owner 2026-11-23.

## Intent

On the Frames screen (phone and panel) the owner tapped Next picture. The frame
did change, but nothing told them the tap took, what the frame was switching to,
or when it was done, until the mirror caught up "a bit later". They want the
page to show the next picture, that it is changing, and that it has finished
and is now on that picture.

Today the delay chain after a tap is: the hub records the command at once; the
frame reads `art-control.json` every 10 s (`check_seconds`); the e-ink push takes
about 30 s; the frame writes its mirror (`frame.png`, `frame.json`) after the
push; the hub republishes each frame's mirror once a minute
(`snaphouse-house-status.timer`, 60 s); the page reads the republished mirror
every 30 s (`FRAME_MS`). Confirmation can reach the page about 90 s after the
tap. The frame, not the hub, picks the next and previous piece (its `Rotation`
in nightstand), so nothing knows the target at tap time except for Show on the
frame.

## Goals

- A tap on Next picture, Back, or Show on the frame (gallery viewer) gives
  feedback at once, on phones and the panel.
- As soon as the frame takes the command, the page shows the picture it is
  switching to, with its title: "Changing to <title>".
- When the e-ink push finishes, the page shows the new mirror with "Now
  showing", for a few seconds, then returns to normal.
- Typical tap-to-"Now showing" on the page is within a few seconds of the frame
  finishing its push (no minute-long republish wait while a change is in
  flight).

## Non-goals

- No change to how the frame picks the next piece, the rotation, quiet hours,
  pins or the settle rules.
- No change to Keep this one (pin), beyond the same immediate "sent" feedback on
  its button.
- Not the per-frame gallery project.

## Anti-goals

- A spinner that never ends: every in-flight state has a timeout with a plain
  message.
- Claiming "Now showing" before the frame has actually pushed the picture.
- Polling fast all the time: fast reads happen only while a change is in flight.

## Approaches

Owner picked B on 2026-11-23.

- **A. Page only.** Instant "Changing..." and a countdown; the new mirror appears
  when it arrives. Cheap, but the next picture is unknown until done and "done"
  still lags up to a minute.
- **B. The frame announces its target (picked).** The frame publishes the piece
  it is switching to as soon as it applies the command, before the slow push;
  the hub republishes quickly while a change is in flight; the page shows the
  target, then confirms. Touches nightstand, the hub, and the page.
- **C. The hub picks the next piece.** Known at tap time, but moves rotation out
  of the frame. Rejected.

## The design, as decided

### Frame (nightstand)

- `frame.json` gains an optional `pending` object, additive to the "Frame mirror
  file" contract: `{"seq": <command seq>, "id": <piece id>, "since": iso,
  "state", "reason"}` (call 1). The frame writes it (the same atomic write) on the cycle it
  applies a next, previous, or show command for this frame, before the push.
  `id` is the piece id the rotation moved to (the same id as art-index and
  art-history). See call 1 for how the frame says it will not draw art now.
- After the push that shows that piece, the frame writes `frame.json` without
  `pending` (with the new `updated_at` and `screen`), as it does today. If the
  command does not lead to an art push, `pending` stays until the next mirror
  write (call 1).

### Hub (snaphouse)

- When `snaphouse-artadmin` accepts a next, previous, or show command, the hub
  republishes the affected frame's mirror every 2 s until its `applied_seq`
  reaches the command's seq, or 90 s pass (calls 6 and 7). Same hardening as the existing republish (no
  redirects, size caps, shape checks, atomic writes, last good copy kept).
- The once-a-minute timer stays as it is.
- The command reply gives the page the command's `seq` (if it does not already)
  so the page can match `pending.seq`.

### Page (snaphouse, phones and panel)

States for one frame after a tap:

1. **Sent** (at once): the tapped button shows a short busy state and the frame
   area shows "Sending to <frame>..." over the current mirror. For Show on the
   frame the target is known, so it goes straight to the Changing look with that
   picture.
2. **Changing**: when the republished `frame.json` carries `pending` with the
   command's seq, the frame area shows the target picture (its thumbnail from
   art-index, dimmed slightly or framed) with "Changing to <title>" and a gentle
   progress indicator (the push takes about 30 s). If `state` is `waiting`, it
   says "Up next: <title>. The frame shows <music, the game, the pinned
   screen> right now." (from `reason`) and ends there. If `state` is `failed`, it
   says "The frame could not show it. Try again." and ends there.
3. **Now showing**: when `frame.json`'s `applied_seq` reaches the command's seq
   (call 2), the new mirror replaces the old one with "Now showing <title>"
   for 3 s, then the screen returns to normal.
4. **No confirmation**: if nothing confirms in 90 s, "The frame did not confirm
   the change. It may be busy or offline." and the screen returns to normal.

- While a change is in flight the page reads that frame's republished
  `frame.json` every 2 s (call 7), back to `FRAME_MS` when it ends.
- Taps on Next or Back during a change are allowed; each new command restarts
  the flow with its own seq.
- Panel sizes follow the panel layout design (text 20 px and up, buttons 72 px
  and up). The phone uses its existing type scale.

## Left to the builder

- Where in nightstand's loop to write `pending` (after `Rotation.apply`, before
  the push) and how `state` and `reason` are derived from the decision.
- The exact visual treatment of the Changing state, within the sizes above.
- Tests: nightstand unit tests for `pending` (written on a command, cleared on the
  push, the waiting and failed cases); hub tests for the fast republish (stops on
  confirm, stops at 90 s, polls only the one frame); page model tests for the
  state machine (sent, changing, now showing, no confirmation, a second tap).
- Update both repos' AGENTS.md and the hub's `docs/cross-repo-features.md`
  ("Frame mirror file" gains `pending`).

## Calls made after the builder check

Level 1's calls after a builder read the draft; they refine the sections above
and win where they differ.

1. **`pending` carries a state, not just `on_screen`.** Shape:
   `{"seq", "id", "since", "state", "reason"}`. `state` is `changing` (the frame
   will push this piece now), `waiting` (the piece is next but the frame is not
   drawing art now; `reason` is one of `music`, `game`, `pin`, `settling`), or
   `failed` (the push raised; `reason` null). A command redraw already bypasses
   quiet hours, so quiet hours is not a reason. The frame clears `pending` on its
   next mirror write and replaces it when a newer command applies.
2. **Confirmation is by sequence, never by clocks.** `frame.json` gains
   `applied_seq`: the seq of the latest command whose piece is on the panel,
   written on every mirror write after a command push (absent until the first).
   The page treats a change as done when `applied_seq >= seq`. No comparison of
   `updated_at` with the tap time (the Pis' clocks can drift).
3. **The hub's mirror validator passes the new fields, checked.**
   `select_frame_meta` keeps `pending` only when `seq` is a non-negative int,
   `id` passes the existing piece-id check, `since` is a string of at most 40
   characters, `state` and `reason` are from the lists above; otherwise it drops
   `pending` alone. `applied_seq` must be a non-negative int or it is dropped.
   The rest of the file's checks are unchanged.
4. **Matching a command to frames.** The page always sends `frame` with Next,
   Back and Show (the Frames screen has a chosen frame), and takes the seq from
   the command reply (the builder confirms it is already returned; if not, the
   reply gains it). A command with `frame: null` (none from this page, but older
   clients) fast-polls every frame host. A frame that never applies the seq (for
   example a restart that only records it) ends in the 90 s "did not confirm"
   message; that is accepted.
5. **Title and thumbnail** come from the page's art index (loaded for the gallery
   already; the Frames screen loads it if it has not). An id missing from the
   index shows "the next picture" with no thumbnail.
6. **The fast republish is a separate one-shot, not inside artadmin.** A systemd
   path unit watches `art-control.json` (artadmin writes it on every command) and
   starts `snaphouse-frame-follow.service`, running as the same user as the
   house-status poller with the same write access to the frames directory and
   the same hardening code. It reads the latest command and polls the targeted
   frame (or every frame for `null`) every 2 s until that frame's `applied_seq`
   reaches the seq or 90 s pass from the latest command. A new command while it
   runs extends the window to 90 s from the new command (it re-reads the control
   file each loop; the path unit does not start a second copy).
7. **Cheap polling.** The hub's follower republishes `frame.png` only when
   `frame.json`'s `updated_at` changed. The page reads `frame.json` every 2 s
   during a change and fetches `frame.png` only when `updated_at` changes.
8. The draft's item about skipping the frame's sleep is dropped: the frame
   already acts on a command in the same 10 s cycle.
