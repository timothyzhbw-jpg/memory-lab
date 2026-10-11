# Multimodal evidence: transcripts and OCR sidecars

MemFalsify and orgmemory do not read images, audio, video or PDF, and they have no speech-to-text or OCR
model. You bring your own transcript or OCR output as a *sidecar* file next to the media file. Each cue or
OCR line then counts as one evidence passage, and every citation names the media file and a position in it:

```text
- contradicts (candidate, unconfirmed) · `recordings/standup-2026-10-06.mp4#t=12.5,17` (00:00:12.500-00:00:17.000) · transcript `recordings/standup-2026-10-06.mp4.vtt:7` · sha256 `92e1e06a69ee` · media sha256 `4c4369d2dc7b` · different values: claim ['100'] vs source ['60']
  > <v Priya>The public API rate limit is 60 requests
  > per minute per key.
```

The quote is an exact range of the sidecar's bytes, and the first hash is the sidecar's, so the usual
evidence checks (SHA-256, character offsets) apply unchanged. The media hash shows which media file the
sidecar was checked against. A full example is in
[`examples/memfalsify-media`](../examples/memfalsify-media/) with its [report](../examples/memfalsify-media/report.md).

## Naming

A sidecar describes a media file in the same directory:

| Sidecar | Media file |
|---|---|
| `clip.mp4.vtt`, `clip.mp4.srt`, `scan.pdf.ocr.json` | the file it names: `clip.mp4`, `scan.pdf` |
| `clip.vtt`, `clip.en.vtt`, `clip.pt-BR.srt`, `scan.ocr.json` | the one media file with the same stem, optionally followed by a language tag |

Media files are `.png .jpg .jpeg .gif .webp .heic .bmp .tiff .mp3 .wav .m4a .flac .ogg .mp4 .mov .webm .mkv
.pdf`. If two media files fit (`clip.mp4` and `clip.wav` for `clip.vtt`), the sidecar is skipped: name it
after one of them. A stem-named transcript with no media file next to it, such as a meeting transcript
exported without the recording, is used on its own and cited by its own path (`call.vtt#t=2,6.5`).

A media file is listed under *Skipped evidence* unless a sidecar was indexed for it.

## Binding to the media file

When the media file is present, the sidecar must record the media file's SHA-256 as `source_sha256`, and
it must match. Otherwise the sidecar is skipped with the reason:

- `stale: its source_sha256 does not match clip.mp4`: the media file changed after the sidecar was made
  (re-encoded, trimmed, replaced). Transcribe it again.
- `not bound to clip.mp4: the sidecar records no source_sha256`: add the hash.
- The media file is a symlink out of the evidence root, a broken symlink or not a regular file.

Compute the hash with `shasum -a 256 clip.mp4` (macOS), `sha256sum clip.mp4` (Linux) or
`(Get-FileHash clip.mp4).Hash` (PowerShell); upper or lower case both work.

When the media file is not present, the sidecar is used as it is, and reports say "media file not present,
binding not checked". A matching hash ties the sidecar to the media file's bytes; it does not show that the
transcription or OCR is accurate.

## WebVTT (`.vtt`) and SubRip (`.srt`)

```text
WEBVTT

NOTE source_sha256: 4c4369d2dc7b32e51f731a2c608d75ef6955a7bc8452967823c1f054518575c7

1
00:00:12.500 --> 00:00:17.000
<v Priya>The public API rate limit is 60 requests
per minute per key.
```

- Each cue is one passage: its text from the first to the last non-blank character, exactly as written,
  including markup such as `<v Priya>` or `<i>` and line breaks. A quote must lie inside one cue.
- The locator is a [Media Fragments](https://www.w3.org/TR/media-frags/) time range in seconds,
  `t=12.5,17`. Reports also print it as `00:00:12.500-00:00:17.000`.
- WebVTT: line 1 is `WEBVTT`. Header lines such as `Kind: captions`, `STYLE` and `REGION` blocks, cue
  settings and `NOTE` blocks are allowed and are not evidence. The binding is a `NOTE` block with the line
  `source_sha256: <hex>`.
- SubRip: cue numbers, then `00:00:01,000 --> 00:00:04,500` (a dot instead of the comma also works).
  SubRip has no comment syntax, so MemFalsify accepts the same `NOTE source_sha256: <hex>` block as an
  extension, before the first cue. Lenient players skip a block without a timing line, but a strict SubRip
  parser may reject the file; if yours does, use WebVTT.
- UTF-8 (a BOM is fine) with LF, CRLF or CR line endings.
- The whole file is skipped, with the line number, for a missing `WEBVTT` line, a timing line that does
  not parse, a cue that ends before it starts, `-->` inside cue text (usually a missing blank line) and a
  malformed or repeated `source_sha256`.

## OCR JSON (`.ocr.json`, format `memlab-ocr/1`)

```json
{
  "format": "memlab-ocr/1",
  "source_sha256": "c50b7ba5f6c2c6b1c98a453e51caed354730e3019f1479de2eab6ea87a41869b",
  "engine": "tesseract 5.4",
  "pages": [
    {"page": 1, "width": 1600, "height": 1200, "lines": [
      {"text": "Feature A (bulk export): rejected for the November release", "bbox": [40, 120, 820, 158]}
    ]}
  ]
}
```

| Field | Rule |
|---|---|
| `format` | Required, exactly `memlab-ocr/1` |
| `source_sha256` | SHA-256 of the media file (needed whenever the media file is present) |
| `pages[].page` | Integer from 1, unique; `1` for a single image |
| `pages[].width`, `height` | Optional page size in pixels; boxes must fit inside it |
| `pages[].lines[].text` | The line's text |
| `pages[].lines[].bbox` | `[x0, y0, x1, y1]`, integer pixels from the top left, `x0 < x1`, `y0 < y1` |

Other keys (`engine`, `confidence`, ...) are ignored; a repeated key rejects the file. Each line is one
passage, trimmed of surrounding blanks. Its locator is `page=1&xywh=40,120,780,38`: the page, then x, y,
width and height in pixels, as in the Media Fragments `xywh` dimension.

Quotes are exact ranges of the sidecar's bytes, so a line's text must appear literally in the file. Write
the JSON as UTF-8 without escape sequences (in Python, `json.dumps(data, ensure_ascii=False)`). A line that
needs an escape (a straight double quote, a backslash, a control character) or was written with `\uXXXX`
is left out, and the sidecar is listed as "partly indexed" with the first such line. To convert another
engine's output, take its line-level results (for tesseract's TSV, the level-4 rows, with the words of each
line joined by spaces) and write one `lines` entry per line.

hOCR is not supported yet. In hOCR, a line's text is spread over word elements and HTML entities, so it is
not one contiguous range of the file, and exact quotes would need a mapping from the rendered text back to
the bytes. Until then, convert hOCR to `memlab-ocr/1`. An `.hocr` file is indexed as plain text today,
markup included, without locators.

## Reviews and the evidence manifest

- In `reviews.jsonl`, `path` may name a sidecar. The quote must lie inside one cue or line; the optional
  `locator` (for example `"t=12.5,17"`) picks which one, otherwise the first that contains it is used. A
  review of a stale or malformed sidecar stops the audit with exit code 2.
- `orgmemory evidence add <dataset> --path media/clip.mp4.vtt --quote "..." [--locator t=12.5,17] ...`
  records the cue's `locator` in `evidence.jsonl`. Every load (`validate`, `replay`, `explain`, `audit`)
  checks it again, including the binding: if the media file changes later, the dataset stops validating.
  Records without a sidecar have no `locator` key, exactly as before.
- The `memlab` MCP server reads sidecars and hashes media files through the same confined reads as other
  evidence, so a media file swapped for a symlink out of the root is never read.

## Limits

- A transcript or OCR sidecar is someone's reading of the media. MemFalsify checks only that it is well
  formed and, when the media file is present, that it was made from those bytes.
- Sidecars count as evidence text, so the 2 MB limit applies to them. Media files are hashed in chunks,
  never loaded or parsed, whatever their size; a large video only makes the scan slower.
- Binding is by file name within one directory. Text embedded in a media container (subtitle tracks, PDF
  text layers) and formats other than WebVTT, SubRip and `memlab-ocr/1` (hOCR, ALTO, ASS) are not read.
