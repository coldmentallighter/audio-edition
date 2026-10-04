"""Pin down the UNITS of every ebur128 number, and settle the remaining doc claims.

The key question: when you read ebur128 through `metadata=true` + `ametadata=print`
(which is what backend/audio.py does), what units come out? The stderr Summary prints
dBFS/dBTP, but the AVOption fields may not.

Also settles: LRA in the metadata path, the loudnorm-vs-ebur128 LRA claim, and the
192 kHz claim (done without the dodgy lavfi path that failed earlier).
"""
from __future__ import annotations

import math
import re
import subprocess
import tempfile
from pathlib import Path

TMP = Path(tempfile.gettempdir()) / "loudcheck2"
TMP.mkdir(parents=True, exist_ok=True)


def run(args, timeout=180):
    return subprocess.run(args, capture_output=True, text=True, cwd=str(TMP),
                          encoding="utf-8", errors="replace", timeout=timeout)


def gen(name: str, sr: int, dur: float, vol: str = "0dB") -> Path:
    p = TMP / name
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"sine=frequency=997:duration={dur}:sample_rate={sr}",
         "-af", f"volume={vol}", "-ac", "2", "-c:a", "pcm_s24le", str(p)])
    return p


def main():
    src = gen("t.wav", 48000, 6)
    print("test file: 997 Hz sine, 48 kHz, 6 s, pcm_s24le, volume 0dB\n")

    # ---------- 1. compare stderr Summary against the metadata keys -------------
    r = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(src), "-map", "0:a:0",
             "-af", "ebur128=peak=sample+true:framelog=verbose:metadata=true,"
                    "ametadata=mode=print:file=meta.txt",
             "-f", "null", "-"])
    meta = (TMP / "meta.txt").read_text(encoding="utf-8", errors="replace")
    last = {}
    for m in re.finditer(r"lavfi\.r128\.([A-Za-z_.0-9]+)=([-\d.]+)", meta):
        last[m.group(1)] = float(m.group(2))
    print("metadata keys (last frame):")
    for k in sorted(last):
        print(f"   {k:24s} = {last[k]}")

    print("\nstderr Summary:")
    for pat in (r"I:\s*([-\d.]+)\s*LUFS", r"LRA:\s*([-\d.]+)\s*LU",
                r"LRA low:\s*([-\d.]+)", r"LRA high:\s*([-\d.]+)",
                r"Peak:\s*([-\d.]+)\s*dBFS"):
        got = re.findall(pat, r.stderr)
        print(f"   {pat[:28]:30s} {got[-3:] if got else None}")

    print("\n=== unit audit ===")
    sp = last.get("sample_peak")
    tp = last.get("true_peak")
    m = re.search(r"Sample peak:\s*\n\s*Peak:\s*([-\d.]+)\s*dBFS", r.stderr)
    stderr_sp = float(m.group(1)) if m else None
    if sp is not None:
        as_db = 20 * math.log10(sp) if sp > 0 else float("-inf")
        print(f"  metadata sample_peak = {sp}")
        print(f"    20*log10(that)     = {as_db:.3f} dB")
        print(f"  stderr says          = {stderr_sp} dBFS")
        print(f"  -> metadata reports {'LINEAR AMPLITUDE, not dB' if abs(as_db - (stderr_sp or 0)) < 0.2 else 'dB'}")
    if tp is not None:
        print(f"  metadata true_peak   = {tp}  (20*log10 = "
              f"{20 * math.log10(tp) if tp > 0 else float('-inf'):.3f} dB)")
    i_val = last.get("I")
    print(f"  metadata I           = {i_val}  (this one IS dBFS: "
          f"{'looks right' if i_val and i_val < 0 else 'SUSPECT'})")
    print(f"  metadata LRA         = {last.get('LRA')}")
    print(f"  metadata sample_peaks_ch0/1 = "
          f"{last.get('sample_peaks_ch0')} / {last.get('sample_peaks_ch1')}")

    # ---------- 2. loudnorm vs ebur128, on genuinely dynamic material -----------
    print("\n" + "=" * 70)
    print("loudnorm vs ebur128 on dynamic material")
    print("=" * 70)
    dyn = TMP / "dyn.wav"
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=60:sample_rate=48000",
         "-af", "volume='if(lt(t,30),-35,-6)dB':eval=frame", "-ac", "2",
         "-c:a", "pcm_s24le", str(dyn)])
    e = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(dyn), "-map", "0:a:0",
             "-af", "ebur128=peak=true", "-f", "null", "-"])
    ln = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(dyn), "-map", "0:a:0",
              "-af", "loudnorm=print_format=json", "-f", "null", "-"])
    e_i = re.findall(r"I:\s*([-\d.]+)\s*LUFS", e.stderr)
    e_l = re.findall(r"LRA:\s*([-\d.]+)\s*LU", e.stderr)
    e_tp = re.findall(r"True peak:\s*\n\s*Peak:\s*([-\d.]+)", e.stderr)
    ln_i = re.findall(r'"input_i"\s*:\s*"([-\d.]+)"', ln.stderr)
    ln_l = re.findall(r'"input_lra"\s*:\s*"([-\d.]+)"', ln.stderr)
    ln_tp = re.findall(r'"input_tp"\s*:\s*"([-\d.]+)"', ln.stderr)
    print(f"  ebur128  I={e_i[-1] if e_i else None}  LRA={e_l[-1] if e_l else None}"
          f"  TP={e_tp[-1] if e_tp else None}")
    print(f"  loudnorm I={ln_i[-1] if ln_i else None}  LRA={ln_l[-1] if ln_l else None}"
          f"  TP={ln_tp[-1] if ln_tp else None}")
    if e_i and ln_i:
        print(f"  delta I   = {abs(float(e_i[-1]) - float(ln_i[-1])):.3f} LU")
    if e_l and ln_l:
        print(f"  delta LRA = {abs(float(e_l[-1]) - float(ln_l[-1])):.3f} LU")
    if e_tp and ln_tp:
        print(f"  delta TP  = {abs(float(e_tp[-1]) - float(ln_tp[-1])):.3f} dB")

    # ---------- 3. 192 kHz, built as a real file (no lavfi) --------------------
    print("\n" + "=" * 70)
    print("ebur128 integrated vs sample rate (real files)")
    print("=" * 70)
    base = 48000
    for sr in (48000, 96000, 192000):
        p = gen(f"s{sr}.wav", sr, 6)
        rr = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(p), "-map", "0:a:0",
                  "-af", "ebur128=peak=true", "-f", "null", "-"])
        got = re.findall(r"I:\s*([-\d.]+)\s*LUFS", rr.stderr)
        pk = re.findall(r"True peak:\s*\n\s*Peak:\s*([-\d.]+)", rr.stderr)
        print(f"  {sr:>7} Hz -> I={got[-1] if got else 'n/a':>8} LUFS"
              f"   TP={pk[-1] if pk else 'n/a'}")

    # ---------- 4. attached cover art -----------------------------------------
    print("\n" + "=" * 70)
    print("attached cover art: does a filter graph still work?")
    print("=" * 70)
    cov = TMP / "cover.flac"
    r = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
             "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1",
             "-map", "0:a", "-map", "1:v", "-c:v", "mjpeg", "-frames:v", "1",
             "-c:a", "flac", "-disposition:v", "attached_pic", str(cov)])
    print(f"  built cover.flac rc={r.returncode}")
    pr = run(["ffprobe", "-v", "error", "-show_entries",
              "stream=index,codec_type,codec_name", "-of", "csv=p=0", str(cov)])
    print("  streams: " + " | ".join(pr.stdout.split()))
    for label, extra in (("no -map", []), ("with -map 0:a:0", ["-map", "0:a:0"])):
        rr = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats",
                  "-i", str(cov)] + extra + ["-af", "ebur128=peak=true",
                                             "-f", "null", "-"])
        print(f"  {label:16s} rc={rr.returncode}  {rr.stderr.strip()[:90]}")


if __name__ == "__main__":
    main()
