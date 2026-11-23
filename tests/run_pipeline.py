#!/usr/bin/env python3
"""End-to-end test: a synthetic video with subtitles goes through every script.

    python3 tests/run_pipeline.py            # needs Python 3.9+, Pillow, numpy and ffmpeg (no Whisper, no voices)

Builds a 6-second test video, dubs it with a stand-in voice (a tone as long as each line would be
spoken, so no voice engine is needed), renders three outputs and two languages (plus Arabic when
arabic-reshaper and python-bidi are installed), plans posts, and checks every file. Also feeds the
scripts broken input and checks they fail with a message, not a traceback.
"""
import json, os, shutil, subprocess, sys, tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S = os.path.join(ROOT, "skills", "ai-dubbing-skill", "scripts")
PY = sys.executable
failed = []


def run(*args, ok=True, cwd=None):
    r = subprocess.run([PY, os.path.join(S, args[0])] + list(args[1:]), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=cwd)
    if ok and r.returncode != 0:
        print(r.stdout[-2000:], r.stderr[-3000:])
        fail(f"{' '.join(args)} exited {r.returncode}")
    if "Traceback" in r.stderr:
        print(r.stderr[-3000:])
        fail(f"{' '.join(args)} crashed with a traceback")
    return r


def fail(msg):
    failed.append(msg)
    print("FAIL", msg)


def check(cond, msg):
    if not cond:
        fail(msg)
    else:
        print("ok  ", msg)


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,codec_name",
                        "-show_entries", "format=duration", "-of", "json", path], capture_output=True, text=True)
    return json.loads(r.stdout)


def main():
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg is needed for this test")
    tmp = tempfile.mkdtemp(prefix="dubbing-test-")
    os.makedirs(os.path.join(tmp, "clips"))
    vid = os.path.join(tmp, "clips", "talk.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "6",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", vid], check=True)
    with open(os.path.join(tmp, "clips", "talk.srt"), "w", encoding="utf-8") as fh:
        fh.write("1\n00:00:00,200 --> 00:00:02,400\nHey everyone, I'm Bob Benkin and um this is\n\n"
                 "2\n00:00:02,600 --> 00:00:04,300\na test at 17,500 miles an hour\n\n"
                 "3\n00:00:04,500 --> 00:00:05,800\nabout 9 or 10 minutes.\n\n")
    job = {"videos": [{"file": "clips/talk.mp4", "id": "talk"}], "language": "en",
           "vocab": ["Behnken"], "translations": ["es"] + (["ar"] if has_rtl() else []),
           "outputs": [{"id": "vertical", "style": "pop", "canvas": "9:16", "fit": "crop", "focus_x": 0.4},
                       {"id": "square", "style": "highlight", "canvas": "1:1", "fit": "blur", "languages": ["en"]},
                       {"id": "wide", "style": "clean", "canvas": "source", "languages": ["en"], "sounds": True}]}
    jp = os.path.join(tmp, "dub.json")
    json.dump(job, open(jp, "w"))

    run("check_setup.py", ok=False)
    r = run("transcribe.py", jp)
    check("subtitles file" in r.stdout, "transcribe.py uses the .srt next to the video")
    tr = json.load(open(os.path.join(tmp, "work", "talk", "transcript.json"), encoding="utf-8"))
    check(any(w["w"] == "17,500" for w in tr["words"]), "a number in the subtitles stays one word ('17,500')")
    r = run("fix.py", jp, "--report")
    check("Benkin -> Behnken" in r.stdout, "fix.py --report spots a near-miss of a vocab name")
    fixes = {"lines": {"1": "Hey everyone, I'm Bob Behnken, and this is a test at 17,500 miles an hour, about 9 or 10 minutes."},
             "replace": {"test": "trial"}, "emphasis": ["17,500"], "sounds": {"5.9": "[beep]"}}
    json.dump(fixes, open(os.path.join(tmp, "work", "talk", "fixes.json"), "w"))
    r = run("captions.py", jp)
    srt = open(os.path.join(tmp, "dubbed", "talk", "talk.en.srt"), encoding="utf-8").read()
    check("Behnken," in srt and "um" not in srt.split() and "trial" in srt, "fixes applied (line, replace, fillers)")
    check("[beep]" in srt, "sound tag in the SRT")
    check(os.path.exists(os.path.join(tmp, "dubbed", "talk", "talk.en.vtt")), "VTT written")
    tpath = os.path.join(tmp, "work", "talk", "translate.es.json")
    check(os.path.exists(tpath), "translation template written")
    t = json.load(open(tpath, encoding="utf-8"))
    es = ["Hola a todos, soy Bob Behnken, y esto es", "una prueba a 28 000 km por hora", "unos 9 o 10 minutos."]
    for c, txt in zip(t["cues"], es):
        c["text"] = txt
    json.dump(t, open(tpath, "w", encoding="utf-8"), ensure_ascii=False)
    if has_rtl():
        ap = os.path.join(tmp, "work", "talk", "translate.ar.json")
        a = json.load(open(ap, encoding="utf-8"))
        for c, txt in zip(a["cues"], ["مرحبا بالجميع، أنا بوب", "تجربة بسرعة ٢٨ ألف كم", "حوالي ٩ أو ١٠ دقائق."]):
            c["text"] = txt
        json.dump(a, open(ap, "w", encoding="utf-8"), ensure_ascii=False)
    run("captions.py", jp)
    check(os.path.exists(os.path.join(tmp, "dubbed", "talk", "talk.es.srt")), "Spanish SRT after translating")

    # dubbing: the clean errors first (no voice engine is installed here)
    r = run("render.py", jp, "--preview")
    check("not dubbed yet" in r.stderr, "render.py says a translated version needs dub.py first")
    job["voices"] = {"es": "elevenlabs:test-voice"}
    json.dump(job, open(jp, "w"))
    os.environ.pop("ELEVENLABS_API_KEY", None)
    r = run("dub.py", jp, "--lang", "es", ok=False)
    check(r.returncode == 3 and "characters for ElevenLabs" in r.stdout and "--confirm-cost" in r.stdout,
          "an ElevenLabs voice stops at the character count until the user says yes")
    r = run("dub.py", jp, "--lang", "es", "--confirm-cost", ok=False)
    check(r.returncode != 0 and "ELEVENLABS_API_KEY" in r.stderr, "no ElevenLabs key: clean error")
    job["voices"] = {"es": "kokoro:not_a_voice"}
    json.dump(job, open(jp, "w"))
    r = run("dub.py", jp, "--lang", "es", ok=False)
    check(r.returncode != 0 and "Choose from" in r.stderr, "a voice that doesn't exist: clean error with the choices")
    job["voices"] = {"es": "kokoro:em_alex"}
    json.dump(job, open(jp, "w"))
    r = run("voices.py", "list")
    check("piper:de_DE-thorsten-high" in r.stdout and "kokoro:em_alex" in r.stdout and "CC0" in r.stdout,
          "voices.py list shows every engine with its licence")
    r = run("voices.py", "list", "ja")
    check("ElevenLabs" in r.stdout, "Japanese points to the user's own ElevenLabs key")

    # dubbing with a stand-in voice: a tone as long as each line would take to say
    sys.path.insert(0, S)
    import dub, voices, wave, math, struct
    def fake_synth(spec, lang, text, out, speed=1.0, model=None, context=None):
        n = int(voices.SR * 0.055 * len(text) / speed)
        with wave.open(out, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(voices.SR)
            w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i * 0.11))) for i in range(n)))
        return "fake"
    voices.synth = fake_synth
    from aclib import load_job, read_json as rj
    cwd = os.getcwd()
    os.chdir(tmp)
    jb = load_job(jp)
    state = dub.dub_one(jb, jb["videos"][0], "es", "en", False, False)
    check(state == "ok", f"dub.py makes the Spanish soundtrack ({state})")
    rep = rj(os.path.join(tmp, "work", "talk", "dub.es.json"))
    check(all(ln["start"] >= 0 and ln["seconds"] <= ln["slot"] + 0.03 for ln in rep["lines"]),
          "every dubbed line fits its slot")
    wav = os.path.join(tmp, "work", "talk", "dub.es.wav")
    with wave.open(wav) as w:
        check(w.getframerate() == 48000 and w.getnchannels() == 2 and abs(w.getnframes() / 48000 - 6.0) < 0.05,
              "the soundtrack is 48 kHz stereo and as long as the video")
    tp = os.path.join(tmp, "work", "talk", "translate.es.json")
    t = json.load(open(tp, encoding="utf-8"))
    long_t = dict(t)
    long_t["cues"] = [dict(c) for c in t["cues"]]
    long_t["cues"][1]["text"] = "una prueba muchísimo más larga de lo que cabe, a veintiocho mil kilómetros por hora, de verdad"
    json.dump(long_t, open(tp, "w", encoding="utf-8"), ensure_ascii=False)
    os.chdir(cwd)
    run("captions.py", jp)
    os.chdir(tmp)
    state = dub.dub_one(load_job(jp), load_job(jp)["videos"][0], "es", "en", False, False)
    check(state == "long" and not os.path.exists(wav), "a line too long to fit: listed, and no old soundtrack left behind")
    check(rj(os.path.join(tmp, "work", "talk", "dub.es.json"))["too_long"][0]["shorten_by"].endswith("%"),
          "the report says how much shorter the line must be")
    os.chdir(cwd)
    json.dump(t, open(tp, "w", encoding="utf-8"), ensure_ascii=False)
    run("captions.py", jp)
    os.chdir(tmp)
    check(dub.dub_one(load_job(jp), load_job(jp)["videos"][0], "es", "en", False, False) == "ok",
          "shortened again: the soundtrack is made")
    os.chdir(cwd)
    r = run("styles.py", jp, "--at", "2.7")
    check(os.path.exists(os.path.join(tmp, "dubbed", "styles-talk.jpg")), "styles sheet")
    r = run("render.py", jp, "--preview")
    check(os.path.exists(os.path.join(tmp, "dubbed", "talk", "review-talk.jpg")), "review sheet")
    r = run("render.py", jp)
    man = json.load(open(os.path.join(tmp, "dubbed", "manifest.json"), encoding="utf-8"))
    want = {("vertical", "en"), ("vertical", "es"), ("square", "en"), ("wide", "en")}
    got = {(m["output"], m["language"]) for m in man["files"]}
    check(want <= got, f"rendered {sorted(want)}")
    if has_rtl():   # Arabic has captions but no free voice: it is skipped until it is dubbed
        check(("vertical", "ar") not in got and "not dubbed yet" in (r.stdout + r.stderr),
              "a translation without a dub is skipped with a clear message")
    sizes = {"vertical": (1080, 1920), "square": (1080, 1080), "wide": (1280, 720)}
    for m in man["files"]:
        p = probe(os.path.join(tmp, "dubbed", m["file"]))
        v = next(s for s in p["streams"] if s["codec_type"] == "video")
        check((v["width"], v["height"]) == sizes[m["output"]], f"{m['file']} is {sizes[m['output']]}")
        check(any(s["codec_type"] == "audio" for s in p["streams"]), f"{m['file']} keeps the sound")
        check(abs(float(p["format"]["duration"]) - 6.0) < 0.15, f"{m['file']} keeps the length")
    es_entry = next(m for m in man["files"] if m["language"] == "es")
    check(es_entry.get("audio") == "dub" and all(m.get("audio") == "original" for m in man["files"]
                                                   if m["language"] == "en"),
          "the Spanish version has the dubbed sound, the English ones the original")
    r = run("render.py", jp)
    check(r.stdout.count("up to date") >= 4, "second render skips finished files")
    post = {"manifest": "dubbed/manifest.json", "start": "2030-01-07",
            "accounts": [{"name": "TikTok", "platform": "TIKTOK", "output": "vertical", "language": "en",
                          "privacyLevel": "SELF_ONLY", "timezone": "UTC"},
                         {"name": "Shorts", "platform": "YOUTUBE", "output": "vertical", "language": "en"},
                         {"name": "LinkedIn", "platform": "LINKEDIN", "output": "square", "language": "en"},
                         {"name": "X", "platform": "X", "output": "wide", "language": "en"}],
            "posts": {"talk": {"en": {"text": "A test of captions #test", "title": "Captions test"}}}}
    pp = os.path.join(tmp, "post.json")
    json.dump(post, open(pp, "w"))
    r = run("post_plan.py", pp)
    check(os.path.exists(os.path.join(tmp, "plan.md")), "plan.md written")
    reqs = os.listdir(os.path.join(tmp, "requests", "posts"))
    check(len(reqs) == 4, "one request per account")
    body = json.load(open(os.path.join(tmp, "requests", "posts", "talk.tiktok.json")))
    check(body["socialAccounts"][0]["privacyLevel"] == "SELF_ONLY", "TikTok privacy is the user's")
    check(body["socialAccounts"][0]["isAiGeneratedContent"] is False, "the original-language video has no AI label")
    es_post = {"manifest": "dubbed/manifest.json", "start": "2030-01-07",
               "accounts": [{"name": "TikTok ES", "platform": "TIKTOK", "output": "vertical", "language": "es",
                             "privacyLevel": "PUBLIC_TO_EVERYONE", "isAiGeneratedContent": False},
                            {"name": "Reels ES", "platform": "INSTAGRAM", "output": "vertical", "language": "es"},
                            {"name": "Shorts ES", "platform": "YOUTUBE", "output": "vertical", "language": "es"}],
               "posts": {"talk": {"es": {"text": "Una prueba #prueba", "title": "Prueba"}}}}
    ep = os.path.join(tmp, "post-es.json")
    json.dump(es_post, open(ep, "w"))
    r = run("post_plan.py", ep)
    tk = json.load(open(os.path.join(tmp, "requests", "posts", "talk.tiktok-es.json")))
    ig = json.load(open(os.path.join(tmp, "requests", "posts", "talk.reels-es.json")))
    check(tk["socialAccounts"][0]["isAiGeneratedContent"] is True and ig["socialAccounts"][0]["isAiGeneratedContent"] is True,
          "dubbed posts carry the AI-generated label on TikTok and Instagram, even if the account said no")
    plan = open(os.path.join(tmp, "plan.md"), encoding="utf-8").read()
    check("Altered or synthetic content" in plan, "plan.md lists YouTube's synthetic-content answer as a step")
    os.remove(ep)
    run("post_plan.py", pp)  # back to the main plan
    check(body["schedule"]["scheduledFor"].startswith("2030-01-07"), "scheduled on the start day")
    media = {k: f"m{i}" for i, k in enumerate(sorted({rel for rel in
             [l.split("`")[1] for l in open(os.path.join(tmp, "plan.md")) if l.startswith("- `dubbed")]}))}
    json.dump(media, open(os.path.join(tmp, "media.json"), "w"))
    run("post_plan.py", pp, "--media", "media.json", cwd=tmp)
    body = json.load(open(os.path.join(tmp, "requests", "posts", "talk.tiktok.json")))
    check(body["content"][0]["media"][0]["mediaId"].startswith("m"), "mediaIds filled from media.json")
    post["accounts"][0].pop("privacyLevel")
    post["accounts"][0]["socialAccountId"] = "456"
    json.dump(post, open(pp, "w"))
    r = run("post_plan.py", pp, ok=False)
    check(r.returncode == 1 and "privacyLevel" in r.stderr, "TikTok without a chosen privacy level is an error")

    # choices only the user can make, once an account is connected
    post["accounts"][0]["privacyLevel"] = "SELF_ONLY"
    post["accounts"][1]["socialAccountId"] = "123"
    json.dump(post, open(pp, "w"))
    r = run("post_plan.py", pp, ok=False)
    check(r.returncode == 1 and "madeForKids" in r.stderr, "connected YouTube without made-for-kids answer is an error")
    check(not os.listdir(os.path.join(tmp, "requests", "posts")), "no request files while the plan has errors")
    post["accounts"][1]["madeForKids"] = False
    post["accounts"].append(dict(post["accounts"][0], name="tik-tok", language="en"))
    post["accounts"][0]["name"] = "Tik Tok"
    json.dump(post, open(pp, "w"))
    r = run("post_plan.py", pp, ok=False)
    check(r.returncode != 0 and "become" in r.stderr and "Traceback" not in r.stderr,
          "two account names with the same file name: clean error")

    # subtitle cues never overlap
    def secs(t):
        h, m, rest = t.split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest.replace(",", "."))
    for srt in [f for f in os.listdir(os.path.join(tmp, "dubbed", "talk")) if f.endswith(".srt")]:
        times = [l.split(" --> ") for l in open(os.path.join(tmp, "dubbed", "talk", srt), encoding="utf-8")
                 if " --> " in l]
        spans = [(secs(a.strip()), secs(b.strip())) for a, b in times]
        check(all(e > s for s, e in spans) and all(spans[i][0] >= spans[i - 1][1] for i in range(1, len(spans))),
              f"{srt}: cues never overlap")

    # the manifest forgets files that are gone
    mp = os.path.join(tmp, "dubbed", "manifest.json")
    man = json.load(open(mp))
    man["files"].append(dict(man["files"][0], output="deleted-output", file="talk/gone.mp4"))
    json.dump(man, open(mp, "w"))
    run("render.py", jp)
    check(not any(m["output"] == "deleted-output" for m in json.load(open(mp))["files"]), "manifest drops removed outputs")
    check(not any(f.endswith(".part.mp4") for f in os.listdir(os.path.join(tmp, "dubbed", "talk"))),
          "no half-written files left")

    # caption grouping, in-process
    sys.path.insert(0, S)
    import render
    from aclib import load_style, split_text
    ws, t = [], 0.0
    for tok in "it takes about 9 or 10 minutes to cross the whole country".split():
        ws.append({"w": tok, "start": t, "end": t + 0.25})
        t += 0.3
    lay = render.Layout(load_style("lines"), 1080, 1920, "en", os.path.join(S, "..", "fonts", "Poppins-ExtraBold.ttf"))
    groups = [" ".join(w["w"] for w in g) for g in render.chunks(ws, lay)]
    check(any("9 or 10 minutes" in g for g in groups) and not any(g.endswith((" the", " 9", " or")) for g in groups),
          f"chunks keep '9 or 10' together and never end on 'the': {groups}")
    import transcribe, captions
    check(transcribe.detect_script("1\n00:00:01,000 --> 00:00:02,000\nこんにちは、宇宙ステーションです\n") == "ja"
          and transcribe.detect_script("Hello there, station") is None, "subtitle files: the writing system sets the language")
    toks = "So we'll actually only have about 9 or 10 minutes of good radio contact".split()
    ws2 = [{"w": tk, "start": 0.25 * k, "end": 0.25 * k + 0.2} for k, tk in enumerate(toks)]
    cues2 = captions.build_cues(ws2, "en", duration=10)
    check(all("9 or 10" in " ".join(captions.split_lines(c["tokens"], "en")).replace("\n", " ") or "9" not in c["text"]
              for c in cues2) and not any(l.rstrip().endswith((" 9", " or", " about"))
              for c in cues2 for l in captions.split_lines(c["tokens"], "en")),
          f"subtitles never break inside '9 or 10 minutes': {[captions.split_lines(c['tokens'], 'en') for c in cues2]}")
    check(split_text("東京で2026年", "ja").count("2") == 2 and
          any(len(u) == 4 for u in render.units([{"w": c, "start": i * 0.1, "end": i * 0.1 + 0.08}
                                                  for i, c in enumerate(split_text("東京で2026年", "ja"))], "ja")),
          "Japanese: the year 2026 stays in one piece")

    # a translation edited after dubbing: render.py won't pair new captions with the old voice
    tp = os.path.join(tmp, "work", "talk", "translate.es.json")
    t = json.load(open(tp, encoding="utf-8"))
    t["cues"][0]["text"] = "Hola, soy Bob Behnken, y esto es"
    json.dump(t, open(tp, "w", encoding="utf-8"), ensure_ascii=False)
    r = run("render.py", jp)
    check("changed after it was dubbed" in r.stderr, "render.py refuses a dub that no longer matches the translation")

    # broken input: a message, never a traceback
    bad = os.path.join(tmp, "bad.json")
    for content, what in (("{not json", "invalid JSON"), ('{"videos": []}', "no videos"),
                          ('{"videos": ["clips/none.mp4"]}', "missing video"),
                          ('{"videos": ["clips/talk.mp4"], "outputs": [{"id": "x", "style": "nope"}]}', "unknown style"),
                          ('{"videos": ["clips/talk.mp4"], "outputs": [{"id": "x", "canvas": "9:16", "focus_x": 4}]}', "bad focus_x"),
                          ('{"videos": ["clips/talk.mp4"], "translations": "es"}', "translations not a list")):
        open(bad, "w").write(content)
        r = run("render.py", bad, ok=False)
        check(r.returncode != 0 and "Traceback" not in r.stderr, f"render.py on {what}: clean error")
    open(os.path.join(tmp, "work", "talk", "fixes.json"), "w").write('{"lines": {"1": "Hey everyone."}}')
    r = run("captions.py", jp, ok=False)
    check(r.returncode != 0 and "replaces the whole line" in r.stderr, "a line fix that drops most words: clean error")
    open(os.path.join(tmp, "work", "talk", "fixes.json"), "w").write('{"lines": {"9": "nope"}}')
    r = run("captions.py", jp, ok=False)
    check(r.returncode != 0 and "doesn't exist" in r.stderr, "fixes.json with an unknown line: clean error")
    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if failed:
        print(f"{len(failed)} FAILED")
        sys.exit(1)
    print("all passed")


def has_rtl():
    try:
        import arabic_reshaper, bidi  # noqa: F401
        return True
    except ImportError:
        try:
            from PIL import features
            return features.check("raqm")
        except Exception:
            return False


if __name__ == "__main__":
    main()
