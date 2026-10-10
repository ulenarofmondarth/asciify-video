# The state file

One JSON file records the most recent run. It serves two purposes:
`asciify_video --report` describes that run, and `--reuse` uses it to find the
directory a `--keep-temp` run left behind.

## Location

`$XDG_CACHE_HOME/asciify/state.json`, which is `~/.cache/asciify/state.json`
when `XDG_CACHE_HOME` is not set. The location is fixed when the package is
imported.

## Schema

```json
{
  "version": 1,
  "updated": "2026-10-06T14:22:31",
  "input": "/path/to/input.mp4",
  "temp_dir": "/tmp/asciify_xyz",
  "keep_temp": true,
  "completed": true,
  "stages_completed": ["probe", "key", "extract", "ascii", "render", "assemble"],
  "probe": {"width": 1920, "height": 1080, "fps": 24.0, "has_audio": true},
  "key": {
    "mode": "auto",
    "color": "0x00B140",
    "detected": true,
    "tolerance": 0.25,
    "edge_blending": 0.05
  },
  "grid": {"ascii_width": 240, "rows": 68, "stretch": null, "ramp": "default"},
  "render": {
    "target_width": 1920,
    "target_height": 1080,
    "font_size": 64,
    "font_color": "#FFB000",
    "colorized": false,
    "leave_black_empty": false,
    "black_threshold": 50,
    "bg_color": "#000000",
    "bg_alpha": 1.0
  },
  "counts": {"frames": 108, "ascii_files": 108, "rendered_images": 108},
  "output": "/path/to/output.mp4",
  "duration_s": 127.4
}
```

| Field | Meaning |
|-------|---------|
| `version` | Schema version, currently 1 |
| `updated` | When the file was last saved |
| `input`, `output` | Paths of the run; `output` is set only on completion |
| `temp_dir` | A directory usable with `--reuse`, or `null` |
| `keep_temp` | Whether the run was asked to keep its temp directory |
| `completed` | `true` only if the run finished |
| `stages_completed` | Stage names finished so far, in order |
| `probe` | Source properties; `fps` is the effective output rate |
| `key.mode` | `auto` (detected), `manual` (given, or detection fell back), or `none` (no keying, or reused) |
| `grid` | Character grid; `stretch` is `[low, high]` when histogram stretching was applied; `ramp` is `default`, `jp2a`, or the custom ramp string |
| `render` | Output size and appearance settings |
| `counts` | Number of frames, text files and rendered images |
| `duration_s` | Wall-clock seconds, set on completion |

## Lifecycle

1. At the start of a run the record is reset: `completed` becomes `false`,
   `stages_completed` and `counts` are emptied, and `temp_dir` is cleared
   unless the run is reusing a directory.
2. After each stage its section is filled in and the file is saved.
3. On completion `completed`, `output` and `duration_s` are set. `temp_dir`
   becomes the new temp directory if `--keep-temp` was given, stays as the
   reused directory on a reuse run, and is `null` otherwise.

So `temp_dir` always answers one question: is there a directory that a later
`--reuse` could start from?

## Compatibility

Reading the file fills in any missing fields with defaults and preserves any
unknown ones, so a record written by an older or newer version loads without
error. A file that is not valid JSON is ignored with a warning and treated as
empty.

## Limitations

There is one record, not a history, and no locking. Two runs at the same time
overwrite each other's record; the conversions themselves are unaffected
because each uses its own temp directory.
