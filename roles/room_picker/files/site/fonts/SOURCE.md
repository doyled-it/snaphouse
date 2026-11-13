# Font source

Instrument Sans, SIL Open Font License 1.1 (see OFL.txt).

- Upstream repo: https://github.com/google/fonts
- Path: `ofl/instrumentsans/` (`InstrumentSans[wdth,wght].ttf` and `OFL.txt`)
- Commit: 3e5667764ccfea32ce0c3d58a3a4756de890adc1
- Retrieved: 2026-11-12

Commands (run on the downloaded variable TTF):

```
python -I -m fontTools.varLib.instancer "InstrumentSans[wdth,wght].ttf" wdth=100 wght=400:700 -o inst.ttf

pyftsubset inst.ttf --flavor=woff2 --layout-features='*' \
  --unicodes=U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD \
  --output-file=instrument-sans-latin.woff2

pyftsubset inst.ttf --flavor=woff2 --layout-features='*' \
  --unicodes=U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF \
  --output-file=instrument-sans-latin-ext.woff2
```

Both ran under `uv run --with fonttools --with brotli`. The width axis is pinned
at 100 and weight kept at 400-700.

Hanken Grotesk was removed in the Dusk redesign.
