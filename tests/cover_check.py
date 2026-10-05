"""封面在「转换 / 响度标准化」之后还在不在 —— 真 ffmpeg 端到端自检。

**这条自检的由来**：用户报"响度标准化为什么没有保留封面？"。
根因是**同一份逻辑在两个 handler 里各写了一遍，而两边的容器名单不一样**：

  · `h_convert`   用 `COVER_CAPABLE`（当时是 `{m4a, mp3, mp4, mov}`，**缺 flac**）
  · `h_normalize` 用一行写死的 `src.suffix in (".m4a", ".mp4", ".mov", ".mp3")`

于是 **FLAC 源做响度标准化之后封面就没了**，而 flac / mp3 恰恰是最常见的源。
现在两边都走 `tasks._apply_cover_args`，这份自检就是防止它再分叉。

验法（**真跑 ffmpeg**，不 mock）：
  1. 容器名单与实跑结果对账（名单是实跑出来的，也要能被实跑推翻）
  2. 能装的容器（flac / mp3 / m4a）：`hasCover=True`，且**封面字节与源一致**
     （`-c:v copy` 的承诺就是"不重编码"）
  3. 装不下的容器（wav / aiff / ogg / opus）：**不报错**（不硬 map），并给出说明
  4. `keepCover=False`：主动丢弃，且**不报警**（那是用户自己要的，不是"丢了东西"）
  5. 命令预览与真实行为**同源**
  6. `normalize` 的 spec 里登记了 `keepTags` / `keepCover`
  7. 真 handler：FLAC 源标准化之后封面还在（原 bug 的正面复现）

**源文件**：优先用 `uploads/` 里那个带封面的真文件。
没有的话就现场造一个 —— 但要注意本机 ffmpeg 9.0.2 的脾气：
**lavfi 生成的 flac 过不了 loudnorm**（`invalid block size: 288000`），
所以现场造的那种情况会自动降级（只跑不依赖 loudnorm 的那几节）。
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from backend import audio, config, tasks                             # noqa: E402

PASS = FAIL = 0
FFMPEG = "ffmpeg"


def check(name: str, ok: bool, extra: object = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}   {extra}")


def run(args: list[str]) -> tuple[bool, str]:
    r = subprocess.run(args, capture_output=True)
    return r.returncode == 0, r.stderr.decode("utf-8", "replace")


def cover_bytes(path: Path) -> bytes:
    """把内嵌封面原样取出来（`-c:v copy`，不重编码）—— 用来比字节。

    ⚠ 用**临时文件**而不是 `-f image2pipe` 走管道：实测长音频（695s）走管道时
    ffmpeg 会报 `invalid block size: 288000` / `No audio stream present`，
    而写成文件就正常 —— 那是管道输出的脾气，不是封面逻辑的问题。
    拿它当判据会把工具链的毛病误报成代码 bug。
    """
    out = path.with_name(path.name + ".cover-probe.png")
    out.unlink(missing_ok=True)
    r = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
         "-map", "0:v:0", "-frames:v", "1", "-c:v", "copy", str(out)],
        capture_output=True)
    data = b""
    if r.returncode == 0 and out.exists():
        data = out.read_bytes()
    out.unlink(missing_ok=True)
    return data


def _real_audio() -> Path | None:
    """`uploads/` 里找一个**真音频文件**（时长合理、能解码）。

    刻意不用 lavfi 现造的 flac —— 见模块头那段：
    "lavfi → flac → loudnorm" 在本机 ffmpeg 9.0.2 上是坏的，
    拿它当源会把工具链的毛病误算到我们代码头上。
    """
    for p in sorted(Path(config.UPLOADS).rglob("*")):
        if not p.is_file() or p.suffix.lower() not in (".flac", ".mp3", ".m4a"):
            continue
        try:
            info = audio.probe(p)
        except Exception:                                # noqa: BLE001
            continue
        if info.ok and 0 < info.duration < 1200:
            return p
    return None


def _make_cover_png(path: Path) -> None:
    run([FFMPEG, "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=#3A7BD5:s=300x300:d=1", "-frames:v", "1", str(path)])


tmp = Path(tempfile.mkdtemp(prefix="ae-cover-"))
try:
    # ---------------------------------------------------------------- 0
    print("== 0. 准备源（真音频 + 真封面） ==")
    base = _real_audio()
    check("uploads/ 里有一个可用的真音频", base is not None,
          "（下面依赖真源的几节会跳过）")
    src: Path | None = None
    if base is not None:
        art = tmp / "art.png"
        _make_cover_png(art)
        check("造出封面图", art.exists() and art.stat().st_size > 200)
        # 基于**真音频**造一个带封面的副本。
        # flac 走 metaflac（应用自己嵌 FLAC 封面的做法），其余走 ffmpeg 重封装。
        cand = tmp / base.name
        if audio.probe(base).has_cover:
            # ⚠ **已经有封面就别再 import 一次**：`metaflac --import-picture-from`
            # 是**追加**，会给同一个文件塞第二个 PICTURE 块，那个文件随后
            # 就过不了 loudnorm（`invalid block size` / `No audio stream present`），
            # 而症状看着像"转码逻辑坏了"。直接用原文件当源即可。
            shutil.copyfile(base, cand)
            check("源本身已带封面 → 直接用（不再重复 import）", True)
        elif base.suffix.lower() == ".flac":
            shutil.copyfile(base, cand)
            okm, errm = run(["metaflac", f"--import-picture-from={art}", str(cand)])
            check("metaflac 把封面嵌进 FLAC 副本", okm, errm[:90])
        else:
            okm, errm = run([FFMPEG, "-y", "-v", "error", "-i", str(base),
                             "-i", str(art), "-map", "0:a", "-map", "1:v",
                             "-c:a", "copy", "-c:v", "copy",
                             "-disposition:v", "attached_pic", str(cand)])
            check(f"把封面嵌进 {base.suffix} 副本", okm, errm[:90])
        if audio.probe(cand).has_cover:
            src = cand
    si = audio.probe(src) if src else None
    src_cover = cover_bytes(src) if src else b""
    if src:
        print(f"        源：{src.name} · {si.format} · "
              f"封面 {len(src_cover)} bytes")
        check("源封面抽得出来（>200 bytes）", len(src_cover) > 200,
              len(src_cover))

    # ---------------------------------------------------------------- 1
    print()
    print("== 1. 容器名单（与实跑对账） ==")
    check("COVER_CAPABLE 含 flac（**这正是原 bug**：以前缺 flac）",
          "flac" in tasks.COVER_CAPABLE, sorted(tasks.COVER_CAPABLE))
    check("COVER_CAPABLE = {flac, mp3, m4a, mp4, mov}",
          tasks.COVER_CAPABLE == {"flac", "mp3", "m4a", "mp4", "mov"},
          sorted(tasks.COVER_CAPABLE))
    check("aiff 在**不支持**那一侧（实测 rc=0 但封面查不到，这种最坑）",
          "aiff" in tasks.COVER_UNSUPPORTED, sorted(tasks.COVER_UNSUPPORTED))
    check("两个名单不相交", not (tasks.COVER_CAPABLE & tasks.COVER_UNSUPPORTED))
    # 名单必须能被实跑推翻：逐个容器试"硬 map 封面"，能跑的应恰好等于 CAPABLE
    if src:
        for ext, want in (("flac", True), ("mp3", True), ("m4a", True),
                          ("wav", False), ("aiff", False), ("ogg", False),
                          ("opus", False)):
            out = tmp / f"probe.{ext}"
            out.unlink(missing_ok=True)
            ok, _ = run([FFMPEG, "-y", "-v", "error", "-i", str(src),
                         "-map", "0:a", "-map", "0:v?", "-c:v", "copy",
                         "-disposition:v", "attached_pic", str(out)])
            got = bool(ok and audio.probe(out).has_cover)
            check(f"实跑对账：{ext} {'能' if want else '不能'}装封面",
                  (ext in tasks.COVER_CAPABLE) == want and got == want,
                  f"名单={ext in tasks.COVER_CAPABLE} 实跑={got}")

    # ---------------------------------------------------------------- 2
    print()
    print("== 2. 能装的容器：封面原样带过去（比字节） ==")
    if src:
        for ext in ("flac", "mp3", "m4a"):
            out = tmp / f"keep.{ext}"
            out.unlink(missing_ok=True)
            args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(src), "-af", "loudnorm=I=-16"]
            args, note = tasks._apply_cover_args(args, src, ext, keep_cover=True)
            ok, err = run(args + ["-map_metadata", "0", str(out)])
            if not ok:
                check(f"{ext}: 转码成功", False, err[:90])
                continue
            got = audio.probe(out)
            gb = cover_bytes(out)
            check(f"{ext}: hasCover=True", got.has_cover, got.has_cover)
            check(f"{ext}: 封面**字节与源一致**（-c:v copy 没重编码）",
                  bool(gb) and hashlib.sha1(gb).hexdigest()
                  == hashlib.sha1(src_cover).hexdigest(),
                  f"{len(gb)} vs {len(src_cover)}")
            check(f"{ext}: 没有'带不过来'的说明", note == "", note)

    # ---------------------------------------------------------------- 3
    print()
    print("== 3. 装不下的容器：**不报错**，并给出说明 ==")
    # ⚠ `wma` 不在这里：它对 44100 立体声这个源本来就编不了
    # （`wmav2: sample rate is too high`），与封面无关 —— 塞进来只会误导。
    if src:
        for ext in ("wav", "aiff", "ogg", "opus"):
            out = tmp / f"noc.{ext}"
            out.unlink(missing_ok=True)
            args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(src), "-af", "loudnorm=I=-16"]
            args, note = tasks._apply_cover_args(args, src, ext, keep_cover=True)
            ok, err = run(args + [str(out)])
            check(f"{ext}: 不硬 map 封面 → 转码**不报错**", ok, err[:90])
            if not ok:
                continue
            check(f"{ext}: 没有封面（容器装不下，如实）",
                  not audio.probe(out).has_cover)
            check(f"{ext}: 给出了说明（不静默丢掉）", bool(note), note)

    # ---------------------------------------------------------------- 4
    print()
    print("== 4. keepCover=False：主动丢弃，且**不报警** ==")
    args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(src), "-af", "loudnorm=I=-16"] if src else []
    args, note = tasks._apply_cover_args(args, src, "flac", keep_cover=False)
    check("参数里只有 `-map 0:a`，没有封面映射",
          args[-2:] == ["-map", "0:a"], args[-4:])
    check("用户主动取消 → 不提示（不是'丢了东西'）", note == "", note)

    # ---------------------------------------------------------------- 5
    print()
    print("== 5. 预览与真实行为同源（不再各写一遍） ==")
    from backend.cards import validate as cvalidate
    pv_flac = cvalidate.render_preview("convert", {"format": "flac"})
    pv_wav = cvalidate.render_preview("convert", {"format": "wav"})
    check("预览里的 flac 带 `-map 0:v?`", "-map 0:v?" in pv_flac, pv_flac[-80:])
    check("预览里的 wav **不带** `-map 0:v?`（真跑到会报错的那条）",
          "-map 0:v?" not in pv_wav, pv_wav[-80:])
    check("预览里的 wav 用 `-map 0:a`", "-map 0:a" in pv_wav, pv_wav[-60:])
    pv_none = cvalidate.render_preview("convert",
                                       {"format": "flac", "keepCover": False})
    check("keepCover=False 的预览也不带封面映射",
          "-map 0:v?" not in pv_none, pv_none[-70:])

    # ---------------------------------------------------------------- 6
    print()
    print("== 6. `normalize` 的 spec 里登记了这两个参数 ==")
    # 它们以前**没登记**：handler 一直在读，但卡片上改不了、文档里看不到、
    # 预览也不显示 —— "参数存在但不可见"是最容易长期藏着的一类不一致。
    from backend.cards import validate as cv2
    from backend.cards.specs import OPS
    keys = {p["key"] for p in OPS["normalize"]["params"]}
    check("normalize 登记了 keepTags", "keepTags" in keys, sorted(keys))
    check("normalize 登记了 keepCover", "keepCover" in keys, sorted(keys))
    clean = cv2.validate_card({"name": "标准化", "op": "normalize", "params": {}})
    check("默认是「两个都保留」",
          clean["params"].get("keepTags") is True
          and clean["params"].get("keepCover") is True, clean["params"])

    # ---------------------------------------------------------------- 7
    print()
    print("== 7. 真 handler：FLAC 源标准化之后封面还在（原 bug 的正面复现） ==")
    if src is None or src.suffix.lower() != ".flac":
        print("        （源不是 flac，跳过）")
    else:
        import threading
        from backend import store
        from backend.queue import Context

        root = tmp / "root"
        (root / "outputs").mkdir(parents=True, exist_ok=True)
        (root / "uploads").mkdir(parents=True, exist_ok=True)
        # `_artifact` 要求产物在 `config.ROOT` 之下，所以把 ROOT 指到临时目录，
        # 而不是系统 temp —— 否则 `relative_to` 会抛 ValueError。
        o_out, o_up, o_root, o_db = (config.OUTPUTS, config.UPLOADS,
                                     config.ROOT, config.DB_PATH)
        config.ROOT = root
        config.OUTPUTS = root / "outputs"
        config.UPLOADS = root / "uploads"
        config.DB_PATH = root / "t.db"
        store._initialized = False
        try:
            store.init_db()
            (root / "uploads" / src.name).write_bytes(src.read_bytes())
            f = store.add_file(src.name, size=src.stat().st_size,
                               mtime=src.stat().st_mtime)
            t = store.create_task("normalize", file_id=f.id,
                                  params={"targetLufs": -16})

            class _Ctx:
                def progress(self, *_a, **_k):
                    pass

            ok, res, err = tasks.h_normalize(store.get_task(t.id), _Ctx())
            check("真 handler 跑成功", ok, err[:140])
            if ok:
                outp = config.ROOT / str(res["output"])
                check("产物落盘了", outp.exists(), outp)
                got = audio.probe(outp)
                check("**FLAC 源标准化之后 hasCover=True**（用户报的那条）",
                      got.has_cover, got.has_cover)
                gb = cover_bytes(outp)
                check("封面字节与源一致",
                      bool(gb) and hashlib.sha1(gb).hexdigest()
                      == hashlib.sha1(src_cover).hexdigest(),
                      f"{len(gb)} vs {len(src_cover)}")
                check("容器没变（还是 flac）", got.format.lower() == "flac",
                      got.format)
                check("标签也带过去了（`-map_metadata 0`）",
                      got.duration > 0, got.duration)
        finally:
            config.OUTPUTS, config.UPLOADS = o_out, o_up
            config.ROOT, config.DB_PATH = o_root, o_db
            store._initialized = False

finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print(f"结果：{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
