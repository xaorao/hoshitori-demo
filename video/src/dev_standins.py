#!/usr/bin/env python3
"""Writes the DEV stand-ins into public/dev/: labelled placeholder screens, a short placeholder recording and plausible logs.

They exist only so the film can be laid out before the Real assets are captured. data.ts refuses them unless FILM_DEV=1, and every frame
rendered with them carries a DEV stamp. Nothing here is a measurement.
"""
import json, pathlib, subprocess, sys
from PIL import Image, ImageDraw, ImageFont

here = pathlib.Path(__file__).resolve().parent.parent
out = here / "public" / "dev"
out.mkdir(parents=True, exist_ok=True)
images = here.parent / "docs" / "images"


def font(size):
    for name in ("/System/Library/Fonts/SFNSMono.ttf", "/System/Library/Fonts/Menlo.ttc", "/System/Library/Fonts/Supplemental/Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def panel(name, size, label, base=None, box=None):
    if base is not None and (images / base).exists():
        im = Image.open(images / base).convert("RGB")
        if box is not None:
            w, h = im.size
            im = im.crop((int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)))
    else:
        im = Image.new("RGB", size, (250, 250, 247))
        d = ImageDraw.Draw(im)
        d.rectangle((0, 0, size[0] - 1, size[1] - 1), outline=(200, 200, 200), width=3)
        d.text((48, 48), "STAND-IN", fill=(10, 10, 10), font=font(56))
        d.text((48, 130), label, fill=(75, 85, 99), font=font(40))
    im.save(out / name)


panel("receipt_hero_head.png", (3000, 520), "receipt: head, lead sentence, state")
panel("receipt_hero_read.png", (3000, 700), "receipt: what it read")
panel("receipt_hero_prove.png", (3000, 640), "receipt: what would prove it wrong")
panel("receipt_hero_model.png", (3000, 700), "receipt: what the model did")
panel("today_top.png", (3000, 900), "", "today.png", (0, 0, 1, 0.5))
panel("today_changes.png", (3000, 900), "", "today.png", (0, 0.35, 1, 0.9))
panel("today_disagree.png", (3000, 560), "today: where the agents disagree")
panel("today_phone_light.png", (1170, 2532), "phone, light")
panel("today_phone_dark.png", (1170, 2532), "phone, dark")
panel("verify_page.png", (3000, 900), "", "verify.png")
panel("verify_forgery.png", (2000, 1100), "check a record: forged copy refused")
panel("record_page.png", (3000, 1500), "", "record.png")
panel("record_missed.png", (3000, 700), "record: cycles that never ran")
panel("drill_page.png", (2000, 2400), "failure drills page")
panel("judges_page.png", (3000, 1200), "for judges page")

# A 6 s placeholder recording for the keyboard run (the Real one is captured by capture.py).
src = out / "today_keyboard_poster.png"
Image.new("RGB", (1280, 720), (250, 250, 247)).save(src)
d = ImageDraw.Draw(Image.open(src))
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(src), "-t", "6", "-r", "30", "-c:v", "libvpx-vp9", "-b:v", "200k", "-pix_fmt", "yuv420p", str(out / "today_keyboard.webm")], check=True)
src.unlink()

logs = {
    "verify_python.log": "$ python3 -m hoshitori.cli cytpveczmrqcx44kzz67bts6mbjd --trusted-root record/trusted_root.json\nOK (dev stand-in)\nexit 0\n",
    "verify_node.log": "$ node verifier-js/verify_bundle.mjs cytpveczmrqcx44kzz67bts6mbjd --trusted-root record/trusted_root.json\nOK (dev stand-in)\nexit 0\n",
    "diff.log": "$ diff report.python.json report.node.json\nreports identical except \"verifier\"\nexit 0\n",
    "verify_forgery.log": "$ python3 -m hoshitori.cli forged --trusted-root record/trusted_root.json\nrefused (dev stand-in)\nexit 1\n",
    "skill_address.log": "$ address-flag-check check --address 0xE950DC316b836e4EeFb8308bf32Bf7C72a1358FF --chain ethereum\n0xE950…58FF on ethereum: flagged by 3 of 3 sources that answered (OFAC SDN list, USDT freeze, USDC freeze).\n$ address-flag-check check --address 0x000000000000000000000000000000000000dEaD --chain ethereum\n0x0000…dEaD on ethereum: no flag found in 3 of 3 sources checked (OFAC SDN list, USDT freeze, USDC freeze). This is not a safety rating.\nexit 0\n",
    "skill_envelope.log": "$ address-flag-check check --address 0x000000000000000000000000000000000000dEaD --chain ethereum --json\n{\"schema_version\": \"1\", \"tool\": \"x\", \"status\": \"ok\", \"data_mode\": \"live\", \"as_of\": \"t\", \"request\": {}, \"data\": {}, \"summary\": \"s\", \"availability\": {}, \"warnings\": []}\nexit 0\n",
    "skill_indicator.log": "$ indicator-condition-check \"RSI(14) < 30 on BTC 1h\"\ncondition: unknown (dev stand-in)\nexit 0\n",
    "resettle.log": "$ python3 scripts/resettle_published.py ../record\n1 of 1 settled up-or-down calls match (dev stand-in)\nexit 0\n",
}
for name, text in logs.items():
    (out / name).write_text(text, encoding="utf-8")
print("stand-ins written to", out)
