#!/usr/bin/env bash
# Downloads the long-context document: Moby-Dick (public domain) from Project Gutenberg.
set -euo pipefail
mkdir -p data
for url in https://www.gutenberg.org/cache/epub/2701/pg2701.txt https://www.gutenberg.org/files/2701/2701-0.txt; do
  if curl -fsSL --max-time 120 -o data/moby-dick.txt "$url"; then
    echo "saved data/moby-dick.txt ($(wc -c < data/moby-dick.txt) bytes)"
    exit 0
  fi
done
echo "download failed; get Moby-Dick (ebook 2701) as plain text from gutenberg.org into data/moby-dick.txt" >&2
exit 1
