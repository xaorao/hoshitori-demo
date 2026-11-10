# The film. `make video` runs everything in order; FILM_DEV=1 lets the layout use stand-ins (every frame then carries a DEV stamp).
SHELL := /bin/bash
ROOT := $(abspath ..)
PACK := $(ROOT)/docs/record/record.tar.gz
OUT := out
FFMPEG ?= ffmpeg
PY := /usr/bin/python3
RENDER_FLAGS := --codec h264 --audio-codec aac --audio-bitrate 192k --concurrency 50% --gop 60 --pixel-format yuv420p $(if $(BROWSER),--browser-executable "$(BROWSER)",)
PROPS := $(if $(FILM_DEV),--props='{"dev":true}',)
SOUNDS := public/sound/tick.wav public/sound/tone.wav public/sound/buzz.wav public/sound/key.wav public/sound/room.wav

.PHONY: video record capture terminals meta sound standins render cuts srt poster stills master test typecheck clean-cache

video: record capture terminals meta sound render master cuts srt poster

record:
	mkdir -p public/record && tar -xzf $(PACK) -C public/record
	@echo "record extracted from $(PACK)"

capture:
	$(PY) $(ROOT)/video/capture.py

terminals:
	bash $(ROOT)/video/record_terminals.sh

# Commit, record id (sha256 of the tar) and render time, printed in scene 9's footer.
meta:
	node src/meta.ts $(PACK) $(ROOT)

# Sound design, generated (CC0 by construction): a wooden tick, a low tone, a short buzz, a soft key click and a room-tone bed.
sound: $(SOUNDS)

public/sound/tick.wav:
	mkdir -p public/sound
	$(FFMPEG) -y -loglevel error -f lavfi -i "aevalsrc='sin(2*PI*2200*t)*exp(-t*380)':d=0.012:s=48000" -af "volume=-10dB" $@
public/sound/tone.wav:
	$(FFMPEG) -y -loglevel error -f lavfi -i "sine=frequency=110:duration=0.4:sample_rate=48000" -af "afade=t=in:d=0.02,afade=t=out:st=0.2:d=0.2,volume=-12dB" $@
public/sound/buzz.wav:
	$(FFMPEG) -y -loglevel error -f lavfi -i "aevalsrc='0.6*sin(2*PI*82*t)+0.3*sin(2*PI*164*t)':d=0.25:s=48000" -af "afade=t=in:d=0.01,afade=t=out:st=0.12:d=0.13,volume=-14dB" $@
public/sound/key.wav:
	$(FFMPEG) -y -loglevel error -f lavfi -i "aevalsrc='sin(2*PI*1500*t)*exp(-t*300)':d=0.02:s=48000" -af "volume=-18dB" $@
public/sound/room.wav:
	$(FFMPEG) -y -loglevel error -f lavfi -i "anoisesrc=color=brown:duration=141:sample_rate=48000:amplitude=0.5" -af "lowpass=f=900,volume=-4dB" $@

standins:
	python3 src/dev_standins.py

render: sound meta
	mkdir -p $(OUT)
	npx remotion render src/index.ts Main $(OUT)/main.raw.mp4 $(RENDER_FLAGS) --crf 18 $(PROPS)
	npx remotion render src/index.ts Cut30 $(OUT)/cut30.raw.mp4 $(RENDER_FLAGS) --crf 18 $(PROPS)
	npx remotion render src/index.ts Cut10 $(OUT)/cut10.raw.mp4 $(RENDER_FLAGS) --crf 16 $(PROPS)
	npx remotion render src/index.ts Cut10Square $(OUT)/cut10_square.raw.mp4 $(RENDER_FLAGS) --crf 16 $(PROPS)

cuts: render

# Loudness: two-pass loudnorm to LUFS (default -16 integrated, true peak -1.5 dB), video untouched, AAC 192k at 48 kHz.
# Note: the sound design is a quiet room tone plus a few ticks, so -16 raises the room tone to conversational level; try LUFS=-23.
LUFS ?= -16
master:
	for f in main cut30 cut10 cut10_square; do \
	  FFMPEG=$(FFMPEG) bash src/master.sh $(OUT)/$$f.raw.mp4 $(OUT)/$$f.mp4 $(LUFS) && rm $(OUT)/$$f.raw.mp4; \
	done

srt:
	mkdir -p $(OUT)
	node src/srt.ts $(OUT)

poster:
	npx remotion still src/index.ts Main $(OUT)/poster.png --frame=75 $(if $(BROWSER),--browser-executable "$(BROWSER)",) $(PROPS)

stills: $(if $(FILM_DEV),standins,)
	node src/stills.ts $(if $(FILM_DEV),--dev,) $(if $(BROWSER),--browser "$(BROWSER)",)

test:
	node --test "test/*.test.ts"

typecheck:
	npx tsc --noEmit

clean-cache:
	rm -rf node_modules/.cache $(OUT)/*.raw.mp4 /tmp/react-motion-render* 2>/dev/null || true
