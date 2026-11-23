#!/usr/bin/env python3
"""Dub the example's NASA clip with the real free voices, then listen back with Whisper.

    python3 tests/dub_check.py    # needs ffmpeg, Pillow, numpy, kokoro-onnx, soundfile, piper-tts, faster-whisper

Spanish with Kokoro and German with Piper: the voices download once, every line must fit its
slot, the soundtrack must be as long as the video, Whisper (tiny) must hear the key words back,
and the rendered video must carry the dubbed sound.
"""
import json, os, shutil, subprocess, sys, tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "ai-dubbing-skill")
ex = os.path.join(SKILL, "examples", "crew-dragon")
tmp = tempfile.mkdtemp(prefix="dub-check-")
shutil.copytree(os.path.join(ex, "clips"), os.path.join(tmp, "clips"))
os.makedirs(os.path.join(tmp, "work", "crew"))
for f in ("transcript.json", "transcript.txt", "fixes.json", "translate.es.json"):
    shutil.copy(os.path.join(ex, "work", "crew", f), os.path.join(tmp, "work", "crew", f))
job = json.load(open(os.path.join(ex, "dub.json"), encoding="utf-8"))
job["videos"] = [v for v in job["videos"] if v["id"] == "crew"]
job["translations"] = ["es", "de"]
job["voices"] = {"es": "kokoro:em_alex", "de": "piper:de_DE-thorsten-high"}
job["outputs"][0]["languages"] = ["es", "de"]
jp = os.path.join(tmp, "dub.json")
json.dump(job, open(jp, "w"))
problems = []


def run(name, *args):
    r = subprocess.run([sys.executable, os.path.join(SKILL, "scripts", name), jp] + list(args),
                       capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=tmp)
    print(r.stdout[-1500:], r.stderr[-1500:])
    if r.returncode != 0 or "Traceback" in r.stderr:
        problems.append(f"{name} {' '.join(args)} failed")
    return r


run("captions.py")
de = ["Ein weiterer Teil ist ein Konzept dafür,",
      "bei einem stark automatisierten Fahrzeug nie aus den Augen zu verlieren,",
      "dass die Crew immer einen sicheren Weg nach Hause hat.",
      "Man muss nicht die Mission mit der Crew retten,",
      "sondern die Crew mit der Crew:",
      "das ist unsere Denkweise."]
tp = os.path.join(tmp, "work", "crew", "translate.de.json")
t = json.load(open(tp, encoding="utf-8"))
for c, txt in zip(t["cues"], de):
    c["text"] = txt
json.dump(t, open(tp, "w", encoding="utf-8"), ensure_ascii=False)
run("captions.py")
run("dub.py")

from faster_whisper import WhisperModel  # noqa: E402
import numpy as np  # noqa: E402
model = WhisperModel("tiny", device="cpu", compute_type="int8")
for lang, must in (("es", ["tripulación", "misión", "casa", "nave"]), ("de", ["crew", "mission", "hause", "weg"])):
    wav = os.path.join(tmp, "work", "crew", f"dub.{lang}.wav")
    if not os.path.exists(wav):
        problems.append(f"{lang}: no soundtrack")
        continue
    rep = json.load(open(os.path.join(tmp, "work", "crew", f"dub.{lang}.json"), encoding="utf-8"))
    if rep["too_long"] or any(ln["seconds"] > ln["slot"] + 0.03 for ln in rep["lines"]):
        problems.append(f"{lang}: a line doesn't fit its slot")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", wav, "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                         capture_output=True).stdout
    audio = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    if abs(len(audio) / 16000 - 21.4) > 0.1:
        problems.append(f"{lang}: soundtrack is {len(audio) / 16000:.2f} s, not as long as the video")
    segs, _ = model.transcribe(audio, language=lang)
    heard = " ".join(s.text for s in segs).lower()
    print(lang, "heard:", heard)
    got = [m for m in must if m in heard]
    if len(got) < 2:
        problems.append(f"{lang}: Whisper heard only {got} of {must}")
run("render.py")
for lang in ("es", "de"):
    out = os.path.join(tmp, "dubbed", "crew", f"crew.vertical.{lang}.mp4")
    if not os.path.exists(out):
        problems.append(f"{lang}: no rendered video")
        continue
    p = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,sample_rate",
                                   "-of", "json", out], capture_output=True, text=True).stdout)
    if not any(s["codec_type"] == "audio" and s.get("sample_rate") == "48000" for s in p["streams"]):
        problems.append(f"{lang}: the video doesn't carry the 48 kHz dubbed sound")
shutil.rmtree(tmp, ignore_errors=True)
if problems:
    sys.exit("FAILED\n" + "\n".join(problems))
print("dub check passed")
