# Audiobooks in the play history: design

## Why
The owner listens to audiobooks from their phone. The phone reports a book chapter by chapter, each with the book's title and the chapter's length, so the house page's play history shows one book as many "tracks" (one per session or chapter, each with a chapter length that looks like the book's). A book should read as one book.

## Goals
- One history row per book: listening again moves the row to the top instead of adding a new one.
- Long-form plays (books and podcasts) merge by identity the same way; music is unchanged (a replayed song is a new play).
- A book row says "Audiobook" and when it was last listened to, with no length. Podcasts keep their real episode length.
- The now-playing card labels a book's progress as the chapter.
- Existing duplicate book rows in play-history.json collapse on the hub's next load.

## Non-goals
Total book length or overall book progress (the phone never sends them); per-chapter rows; any change to music history.

## Decided
- Identity is the track key (title, artist, album). When a play is counted and an existing row has the same key and either it or the new play is long_form, the old row is removed and the merged row inserted at the top, with kind long_form (a short chapter can no longer be filed as music).
- The merged row keeps the first `started_at`, gains `last_played_at` (ISO, when the newest session started), and takes the newest non-null art_url, genre, podcast_id and duration.
- A row is a book on the page when kind is long_form, the genre names no podcast, and podcast_id is null.

## Approaches
1. Merge on the hub when a play is counted (chosen): one source of truth for the page and the frame, and the file stays short.
2. Merge only on the page: rejected, the hub's capped history would still fill with chapter rows and push real plays out.
