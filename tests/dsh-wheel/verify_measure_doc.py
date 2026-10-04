"""Verify or refute the AI-written measurement doc's claims against THIS machine.

Every check runs a real ffmpeg command and prints what actually happened. Nothing is
taken on faith from the doc.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.gettempdir()) / "loudcheck"
TMP.mkdir(parents=True, exist_ok=True)
WAV = TMP / "tone.wav"


def run(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    # cwd matters: `ametadata=...:file=x.txt` writes relative to the PROCESS cwd,
    # which is exactly the Windows-absolute-path trap the doc never mentions.
    return subprocess.run(args, capture_output=True, text=True, cwd=str(TMP),
                          encoding="utf-8", errors="replace", timeout=timeout)


def sh(cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(["cmd", "/c", cmd], capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          cwd=str(TMP), timeout=120)


def head(title: str) -> None:
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


def main():
    # a signal with a real difference between sample peak and true peak:
    # a full-scale square-ish wave overshoots between samples.
    src = TMP / "hot.wav"
    r = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i",
             "sine=frequency=997:duration=6:sample_rate=48000",
             "-af", "volume=6dB", "-ac", "2", "-c:a", "pcm_s24le", str(src)])
    print("generated", src.name, "rc", r.returncode)

    # ---------------------------------------------------------------- 1
    head("CLAIM 1: sample peak needs a separate `astats` pass")
    a = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats", "-i", str(src),
             "-map", "0:a:0",
             "-af", "ebur128=peak=sample+true:framelog=verbose:metadata=true,"
                    "ametadata=mode=print:file=eb.txt",
             "-f", "null", "-"])
    txt = (TMP / "eb.txt").read_text(encoding="utf-8", errors="replace")
    eb_sample = [float(m) for m in re.findall(r"lavfi\.r128\.sample_peak=([-\d.]+)", txt)]
    eb_true = [float(m) for m in re.findall(r"lavfi\.r128\.true_peak=([-\d.]+)", txt)]
    ast = run(["ffmpeg", "-hide_banner", "-loglevel", "info", "-nostats", "-i", str(src),
               "-map", "0:a:0", "-af", "astats=measure_overall=Peak_level:measure_perchannel=0",
               "-f", "null", "-"])
    ast_peak = [float(m) for m in re.findall(r"Peak level dB:\s*([-\d.]+)", ast.stderr)]
    print(f"  ebur128 sample_peak : {eb_sample[-1] if eb_sample else None} dBFS")
    print(f"  ebur128 true_peak   : {eb_true[-1] if eb_true else None} dBTP")
    print(f"  astats Peak level   : {ast_peak[-1] if ast_peak else None} dB")
    if eb_sample and ast_peak:
        same = abs(eb_sample[-1] - ast_peak[-1]) < 0.01
        print(f"  -> agree: {same}   (delta {eb_sample[-1] - ast_peak[-1]:+.3f} dB)")
        print(f"  -> CLAIM REFUTED: the extra astats pass is unnecessary;"
              f" ebur128 already reports sample peak.")

    # ---------------------------------------------------------------- 2
    head("CLAIM 2: `volumedetect` is inaccurate on >16-bit audio")
    vd = run(["ffmpeg", "-hide_banner", "-loglevel", "info", "-nostats", "-i", str(src),
              "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"])
    vd_peak = [float(m) for m in re.findall(r"max_volume:\s*([-\d.]+)", vd.stderr)]
    print(f"  src is pcm_s24le, astats peak = {ast_peak[-1] if ast_peak else None}")
    print(f"  volumedetect max_volume      = {vd_peak[-1] if vd_peak else None}")
    if vd_peak and ast_peak:
        d = abs(vd_peak[-1] - ast_peak[-1])
        print(f"  -> delta {d:.3f} dB   claim {'SUPPORTED' if d > 0.01 else 'NOT observed'}")

    # ---------------------------------------------------------------- 3
    head("CLAIM 3: M/S export needs `ebur128=video=1` plus `ametadata=print:key=...`")
    cmd = ("ffmpeg -hide_banner -loglevel error -nostats -y -i hot.wav "
           "-filter_complex \"ebur128=video=1,ametadata=print:key=lavfi.r128.M:"
           "file=m.log\" -f null -")
    r = sh(cmd)
    mlog = TMP / "m.log"
    n = 0
    if mlog.exists():
        body = mlog.read_text(encoding="utf-8", errors="replace")
        n = len(re.findall(r"lavfi\.r128\.M=", body))
    print(f"  doc's exact command rc={r.returncode}, lines with M= : {n}")
    if r.returncode != 0:
        print(f"  stderr: {r.stderr.strip()[:300]}")
    print(f"  -> {'WORKS' if n else 'DOES NOT PRODUCE DATA as written'}")

    # ---------------------------------------------------------------- 4
    head("CLAIM 4: ebur128 and loudnorm report LRA differing by >2 LU")
    # build something with real dynamic range so LRA is non-zero
    dyn = TMP / "dyn.wav"
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=40:sample_rate=48000",
         "-af", "volume='if(lt(t,20),-30,-6)dB':eval=frame", "-ac", "2",
         "-c:a", "pcm_s24le", str(dyn)])
    e = run(["ffmpeg", "-hide_banner", "-loglevel", "info", "-nostats", "-i", str(dyn),
             "-map", "0:a:0", "-af", "ebur128=peak=true", "-f", "null", "-"])
    ln = run(["ffmpeg", "-hide_banner", "-loglevel", "info", "-nostats", "-i", str(dyn),
              "-map", "0:a:0", "-af", "loudnorm=print_format=json", "-f", "null", "-"])
    e_lra = re.findall(r"LRA:\s*([-\d.]+)\s*LU", e.stderr)
    ln_lra = re.findall(r'"lra":\s*"?([-\d.]+)"?', ln.stderr)
    e_i = re.findall(r"I:\s*([-\d.]+)\s*LUFS", e.stderr)
    ln_i = re.findall(r'"input_i"\s*:\s*"([-\d.]+)"', ln.stderr)
    print(f"  ebur128  I={e_i[-1] if e_i else None}  LRA={e_lra[-1] if e_lra else None}")
    print(f"  loudnorm I={ln_i[-1] if ln_i else None}  LRA={ln_lra[-1] if ln_lra else None}")
    if e_lra and ln_lra:
        d = abs(float(e_lra[-1]) - float(ln_lra[-1]))
        print(f"  -> LRA delta {d:.2f} LU   claim "
              f"{'SUPPORTED' if d > 2 else 'NOT observed at this delta'}")
    if e_i and ln_i:
        print(f"  -> Integrated delta {abs(float(e_i[-1]) - float(ln_i[-1])):.3f} LU"
              f"   (these SHOULD agree)")

    # ---------------------------------------------------------------- 5
    head("CLAIM 5: ebur128 integrated is wrong at 192 kHz")
    for sr in (48000, 96000, 192000):
        w = TMP / f"t{sr}.wav"
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", f"sine=frequency=997:duration=6:sample_rate={sr}",
             "-ac", "2", "-c:a", "pcm_s24le", str(w)])
        rr = run(["ffmpeg", "-hide_banner", "-loglevel", "info", "-nostats", "-i", str(w),
                  "-map", "0:a:0", "-af", "ebur128=peak=true", "-f", "null", "-"])
        got = re.findall(r"I:\s*([-\d.]+)\s*LUFS", rr.stderr)
        print(f"  {sr:>7} Hz -> I = {got[-1] if got else 'n/a'} LUFS")
    print("  (all three should be the same number for a full-scale sine)")

    # ---------------------------------------------------------------- 6
    head("What `-map 0:a:0` is really protecting against")
    cov = TMP / "cover.flac"
    r = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
             "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1",
             "-map", "0:a", "-map", "1:v", "-c:v", "mjpeg", "-frames:v", "1",
             "-c:a", "flac", "-disposition:v", "attached_pic", str(cov)])
    print(f"  built a flac with an attached picture: rc={r.returncode}")
    p = run(["ffprobe", "-v", "error", "-show_entries", "stream=index,codec_type,codec_name",
             "-of", "csv=p=0", str(cov)])
    print("  streams: " + " | ".join(p.stdout.split()))
    bad = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats", "-i", str(cov),
               "-af", "ebur128=peak=true", "-f", "null", "-"])
    good = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats", "-i", str(cov),
                "-map", "0:a:0", "-af", "ebur128=peak=true", "-f", "null", "-"])
    print(f"  without -map 0:a:0 : rc={bad.returncode}  {bad.stderr.strip()[:100]}")
    print(f"  with    -map 0:a:0 : rc={good.returncode}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
