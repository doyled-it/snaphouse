# Cover matching design

Date: 2026-11-23. Status: approved by the owner 2026-11-23.

## Why

The owner reports that the hub still misses too many album covers on the frames and
the panel. Their reading: Apple Music credits a track to every featured artist and
decorates album names ("Deluxe Edition", "Remastered 2013"), while the lookup wants
one exact name. They asked for fuzzy matching across artists and albums that covers
the common failures.

## What happens today

`lib/snaphouse/artlookup.py` resolves a music cover from MusicBrainz plus the Cover
Art Archive (CAA) only. A release must have the exact normalized album title and an
artist credit equal to the full track credit, with two fallbacks: the lead artist
(cut at ", ", " & ", "feat.") and the album base before its first " (", " - ", ": "
or " | ". The first matching release's CAA `front-500` is fetched; if that one fetch
returns nothing, the whole lookup is an error, which is not cached and is retried on
the next track change.

## What failed, and why

The owner asked for every recorded miss to be characterized before the design. The
hub keeps the last 50 plays (`play-history.json`, 2026-11-22 14:52 to 2026-11-23
21:41; 11 have no cover) and its persistent journal starts 2026-11-22 (98 lookups).
The 43 older miss markers are stored as hashes and cannot be read back. Each distinct
miss was replayed through the real lookup code with every request logged:

| Album or show | Artist | What happened | Cause |
|---|---|---|---|
| Catfish | Four Tops | MusicBrainz answered 503 | 1. MusicBrainz busy |
| 100% Electronica | George Clanton | MusicBrainz answered 503 | 1. MusicBrainz busy |
| Cave World (Deluxe), THE MOUSE AND THE MASK (METALFACE EDITION), Kaya (Remastered 2013), NEHRUVIANDOOM (REDUX), House of Balloons (Original) | various | logged `failure` 1 to 10 times, found on other plays and on replay | 1. intermittent, almost certainly the same 503s: a featured or decorated album costs up to 4 searches, and a failure is retried on every track change |
| Metal Fingers Presents: Special Herbs, Vol. 1 & 2 | MF DOOM | release matched, its cover 404 | 2. no picture for that one release |
| Preacher's Son | YGTUT | release matched, its cover 404; the album-wide (release group) picture answers 200 | 2. no picture for that one release |
| Democracy Now! | Democracy Now! | podcast miss; Apple lists the show as "Democracy Now! Audio" | 3. catalogue name differs |
| Vacuum Wars, I'm Not Soder | (YouTube channels) | no album, titles like "Dan Soder on 9 11 \| On The Road" | 4. a video, no catalogue cover exists |
| Seven Sundays | unknown | one `failure`; artist not in history | not characterized |

Naming (featured artists, edition suffixes) did not cause a final miss in this
history: the lead-artist and base-title fallbacks found every one. Its cost is extra
searches, which feeds cause 1. Apple's catalogue has Catfish, 100% Electronica,
Special Herbs, NEHRUVIANDOOM (REDUX), Kaya (Remastered 2013) and THE MOUSE & THE
MASK under the exact names the phone sends (apart from "&" for "and"). Apple's search
also returns other artists' covers of the same title ("House of Balloons" covers by
unrelated acts), so the artist must still match.

## Goals

- Find the right cover for music whose names come from Apple Music, including
  multi-artist credits, edition suffixes and singles.
- Stop transient fetch errors from turning into missing covers, and log why a fetch
  failed.
- Retry everything the old matcher missed.

## Non-goals

- No change to audiobook lookups, beyond sharing the better fetch logging.
- No new outbound hosts. `itunes.apple.com` and `*.mzstatic.com` are already allowed.
- No UI change.

## Anti-goals

- A wrong cover is worse than no cover. A weak match must lose to a miss.
- No identifying data in requests: only artist, album and track names go out, as
  today.

## Approaches

1. **Apple first, fuzzy matching on both sources (chosen by the owner).** Search
   the iTunes Search API first, because the names arrive from Apple Music and match
   its catalogue. Fall back to MusicBrainz. Both use one shared fuzzy matcher, and
   the fetch errors are fixed.
2. MusicBrainz only, with the fuzzy matcher and fetch fixes. Smaller, but albums
   Apple names differently from MusicBrainz keep missing.
3. Fix the fetch errors first and look at a week of real misses before touching the
   matching. Cheapest, but delays the fix the owner asked for.

## Design

**Order.** Music lookup: cached cover, then Apple album search, then MusicBrainz
(full credit, then lead, as today). When the track has no album, search Apple songs
by title and artist and use the song's album artwork. Each design point below names
the cause it addresses. A found cover is cached under
the existing full-credit key, so the lead-key sharing across an album keeps working.

**Apple search (causes 1 and 2).** `https://itunes.apple.com/search` with `media=music`,
`entity=album` (or `song` with no album), `limit=10`, and a term of the lead artist
plus the album core title (a term carrying "(Deluxe)" or "(METALFACE EDITION)" came
back empty in testing, the core title did not). Artwork is `artworkUrl100` with the size segment
rewritten to `600x600bb`. Apple has no published rate limit for this API; one
search per new album is well inside it.

**Matcher**, one pure function used for both sources, scoring candidates:

- *Album.* Normalize as today, plus "&" reads as "and" ("THE MOUSE & THE MASK"). The core title drops trailing bracketed or dashed
  parts (`(...)`, `[...]`, ` - ...`) and Apple's ` - Single` and ` - EP`. A
  candidate matches when its normalized full title equals the track's, or its core
  equals the track's core. Exact full-title matches rank above core matches, so
  "NehruvianDOOM (Redux)" prefers the Redux release when one exists.
- *Artist.* Split both the track credit and the candidate's credit into names on
  ", ", " & ", " and ", " x ", " with ", "feat." and "featuring", case-insensitively,
  and drop a leading "the". A candidate matches when the full normalized credits
  are equal or at least one name appears in both. Splitting on " and " is safe here
  (it was not for search) because the album must match as well.
- Highest score wins; anything that fails either rule is not a match. No numeric
  similarity threshold in this version. Close spellings (typos, transliterations)
  stay a miss.

**Fetch errors (causes 1 and 2).**

- A MusicBrainz 503 waits 2 s and retries once before it counts as an error.
- The fetch reports why it failed (HTTP status or exception class), and the lookup
  logs the source, the reason and the matched names on every outcome.
- A CAA 404 for one release is a miss for that release. The lookup tries the next
  matching release, then the album-wide picture
  (`https://coverartarchive.org/release-group/<id>/front-500`; the release group id
  comes from the search result) before giving up. Preacher's Son is found this way.
- Image fetches allow 20 s, since CAA redirects to archive.org. Searches keep 10 s.
- After an error, the same lookup key is not retried for 10 minutes, so one bad
  album no longer refetches on every track change. Errors still write no miss
  marker.

**Podcasts (cause 3).** When no show matches its name exactly, accept one whose
publisher (`artistName`) equals the show name and whose collection name starts with
it ("Democracy Now!" to "Democracy Now! Audio"). Audiobooks are unchanged.

**Videos (cause 4).** A music track with no album whose search finds no song stays
a miss, as today. No catalogue has a cover for a YouTube video; the default art is
right there.

**Old misses.** After the deploy, delete the hub's `cover/lookup/*.none` markers
once by hand, so every earlier miss is searched again. Found covers stay.

**Tests.** Recorded fixtures for every row of the table above, plus
negative cases that must stay a miss: same album title by an unrelated artist, and
a compilation credited to "Various Artists".

**Docs.** Update the module docstring, AGENTS.md (the cover-lookup outbound line
names Apple for music too) and the now-playing design's lookup section.

## Left to the builder

The exact scoring weights between full-title and core-title matches, the
fixture-recording approach, and the logging format, as long as the log line names
the source, the reason and the outcome.
