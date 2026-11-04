# Original source data

This directory holds the original, unfiltered Endomondo records:

| File | Records | SHA-256 of uncompressed data |
| --- | ---: | --- |
| `endomondoHR.json.gzip` | 253,020 | `1c021e3f8cb6428aefcb14ceff9d58ab2b6b2163bcbedfca71cb9f55616836e5` |
| `endomondoMeta.json.gzip` | 962,190 | `90461964a32557bea0ade6f5c9f46714a83606e88e22bde4cd7430a120031410` |

`endomondoHR.json.gzip` and `endomondoMeta.json.gzip` are gzip-compressed
copies of these complete sources. Extraction was checked against the SHA-256
values and record counts above. The uncompressed JSON copies have been removed.
Extract these archives to their original `.json` filenames before running the
filter, which currently accepts uncompressed sources.

The extracted files use one Python dictionary per line. Raw datasets are ignored
by Git; the HR archive exceeds GitHub's 2 GiB LFS per-file limit. On this checkout they
were restored from the complete original objects retained in `.git/lfs/objects`.
Fresh clones must obtain the original datasets separately or retrieve those
historical LFS objects if the remote retains them; `git lfs pull` at the current
revision only downloads the tracked processed data.

Keep these sources unchanged. Run the filter to write outdoor-running records
to `../processed/`; treadmill running and all other sports are excluded.
