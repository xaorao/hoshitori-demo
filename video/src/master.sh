#!/usr/bin/env bash
# Loudness master: two-pass EBU R128 loudnorm to the target integrated loudness (default -16 LUFS, true peak -1.5 dB, linear),
# video copied untouched, AAC 192 kbps at 48 kHz. Usage: master.sh in.raw.mp4 out.mp4 [LUFS]
set -euo pipefail
in="$1"; out="$2"; target="${3:--16}"
ffmpeg_bin="${FFMPEG:-ffmpeg}"
stats="$("$ffmpeg_bin" -hide_banner -nostats -i "$in" -af "loudnorm=I=${target}:TP=-1.5:LRA=11:print_format=json" -f null - 2>&1 | sed -n '/^{/,/^}/p')"
get() { printf '%s' "$stats" | sed -n "s/.*\"$1\" *: *\"\\([^\"]*\\)\".*/\\1/p"; }
"$ffmpeg_bin" -y -loglevel error -i "$in" -c:v copy \
  -af "loudnorm=I=${target}:TP=-1.5:LRA=11:measured_I=$(get input_i):measured_TP=$(get input_tp):measured_LRA=$(get input_lra):measured_thresh=$(get input_thresh):offset=$(get target_offset):linear=true" \
  -c:a aac -b:a 192k -ar 48000 "$out"
printf '%s: measured %s LUFS, target %s LUFS\n' "$out" "$(get input_i)" "$target"
