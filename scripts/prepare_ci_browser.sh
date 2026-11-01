#!/usr/bin/env bash
set -euo pipefail

# GitHub's Linux runner can disable the user namespaces Chrome's sandbox needs.
# Only the CI wrapper adds these flags; normal app launches keep their sandbox.
browser="$(command -v google-chrome-stable || command -v google-chrome || true)"
if [[ -z "$browser" ]]; then
  echo 'A real Google Chrome installation is required for formula export tests.' >&2
  exit 1
fi
directory="${RUNNER_TEMP:?}/ai-video-note-browser"
mkdir -p "$directory"
printf '#!/usr/bin/env bash\nexec %q --no-sandbox --disable-dev-shm-usage "$@"\n' "$browser" > "$directory/chromium"
chmod +x "$directory/chromium"
printf '%s\n' "$directory" >> "${GITHUB_PATH:?}"
"$browser" --version
