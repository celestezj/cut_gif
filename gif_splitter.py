# -*- coding: utf-8 -*-
"""
GIF 播放器 / 分段截取工具

操作说明:
    空格  暂停 / 继续（在事件回调里立即取时间，主循环 240Hz，暂停误差约几毫秒）
    P     记录当前播放时刻为一个时间节点（最多 2 个，第 3 次按下无效）
    O     已有 2 个节点时，截取两节点之间的画面并导出为 原名_split.gif
    Esc   清空所有已记录的时间节点
    鼠标  点击底部进度条任意位置，立即跳转到该时刻并暂停，按空格继续
    Q     退出

用法:
    python gif_splitter.py [xxx.gif]
    不传参数时会弹出文件选择对话框。
"""

import os
import sys
import bisect
from pathlib import Path

import pygame
from PIL import Image, ImageSequence

# 进度条点击热区高度（像素），进度条本身在该区域底部
SEEK_BAND_H = 20


# ---------------------------------------------------------------- 帧解析

def gif_size(path):
    im = Image.open(path)
    return im.size, getattr(im, "n_frames", 1)


def decode_gif(path, progress_cb=None):
    """在窗口创建后调用：直接把 GIF 解码为 pygame 纹理。

    返回 (帧列表, 每帧时长ms, 每帧起始时刻ms, 各帧disposal, 总时长ms)。
    按 GIF 规范处理帧间 disposal（0/1 保留、2 清背景、3 恢复上一帧）；
    完全不透明的帧用 RGB 纹理（省一半内存），含透明的帧用 RGBA。
    """
    im = Image.open(path)
    size = im.size
    n_frames = getattr(im, "n_frames", 1)
    black = Image.new("RGB", size, (0, 0, 0))

    frames, durations, disposals = [], [], []
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    saved_before_dispose3 = None
    pending_disposal = 0       # 上一帧的 disposal，作用于下一帧绘制前
    pending_extent = (0, 0, size[0], size[1])

    for i, frame in enumerate(ImageSequence.Iterator(im)):
        # 先应用“上一帧”留下的 disposal 方式
        if pending_disposal == 2:        # 恢复为背景色（按透明处理）
            x0, y0, x1, y1 = pending_extent
            canvas.paste(Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0)),
                         (x0, y0))
        elif pending_disposal == 3:      # 恢复为上一帧绘制前的状态
            if saved_before_dispose3 is not None:
                canvas = saved_before_dispose3.copy()

        duration = frame.info.get("duration", 0) or im.info.get("duration", 100)
        dispose = frame.disposal_method or 0
        extent = frame.dispose_extent if frame.dispose_extent else (0, 0, size[0], size[1])

        if dispose == 3:
            saved_before_dispose3 = canvas.copy()

        # 注意：PIL 解出的每一帧都已是完整画布大小，局部区域外为透明，
        # 直接在 (0,0) 贴入即可（不要再按 extent 偏移，否则画面会错位闪烁）。
        rgba = frame.convert("RGBA")
        canvas.paste(rgba, (0, 0), rgba)

        if canvas.getchannel("A").getextrema()[0] == 255:
            rgb = canvas.convert("RGB")
            surf = pygame.image.fromstring(rgb.tobytes(), size, "RGB").convert()
        else:
            surf = pygame.image.fromstring(canvas.tobytes(), size, "RGBA").convert_alpha()
        frames.append(surf)
        durations.append(max(duration, 1))
        disposals.append(dispose if dispose in (0, 1, 2, 3) else 0)

        pending_disposal = dispose
        pending_extent = extent
        if progress_cb and (i % 10 == 0 or i == n_frames - 1):
            progress_cb(i + 1, n_frames)

    starts = [0]
    for d in durations[:-1]:
        starts.append(starts[-1] + d)
    total = starts[-1] + durations[-1]
    return frames, durations, starts, disposals, total


# ---------------------------------------------------------------- 导出

def export_segment(src_path, t1, t2, durations_meta=None):
    """导出 [t1, t2) 时间区间内的帧为新 GIF。"""
    t1, t2 = sorted((t1, t2))
    if t2 - t1 < 1:
        return None, "两个时间节点重合，无内容可导出"

    im = Image.open(src_path)
    selected, sel_dur, sel_disposal = [], [], []
    elapsed = 0
    for frame in ImageSequence.Iterator(im):
        if t1 <= elapsed < t2:
            selected.append(frame.copy())
            d = frame.info.get("duration", 0) or im.info.get("duration", 100)
            sel_dur.append(max(d, 1))
            dm = frame.disposal_method or 0
            sel_disposal.append(dm if dm in (0, 1, 2, 3) else 0)
        elapsed += frame.info.get("duration", 0) or im.info.get("duration", 100) or 1

    if not selected:
        return None, "该时间区间内没有完整帧（区间太短）"

    out = Path(src_path).with_name(Path(src_path).stem + "_split.gif")
    save_kwargs = dict(
        save_all=True,
        append_images=selected[1:],
        duration=sel_dur,
        loop=im.info.get("loop", 0),
        optimize=False,
    )
    try:
        save_kwargs["disposal"] = sel_disposal
        selected[0].save(out, **save_kwargs)
    except Exception:
        save_kwargs.pop("disposal", None)
        selected[0].save(out, **save_kwargs)
    return str(out), None


# ---------------------------------------------------------------- UI

def make_font(size, bold=False):
    """优先使用含中文字形的字体（微软雅黑 / 黑体），避免中文显示成问号。"""
    name = pygame.font.match_font(
        "microsoftyaheiui,microsoftyahei,simhei,simsun,nsimhei,arialunicodems",
        bold=bold)
    if name:
        return pygame.font.Font(name, size)
    # 再退一步：直接按路径找 Windows 字体
    for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
              r"C:\Windows\Fonts\simhei.ttf"):
        if os.path.isfile(p):
            return pygame.font.Font(p, size)
    return pygame.font.Font(None, int(size * 1.3))


def fmt(ms):
    return f"{ms:.0f} ms"


def draw(screen, frame, state, starts, total, size, fonts, flash):
    screen.fill((20, 20, 20))
    screen.blit(frame, (0, 0))

    f_big, f_small = fonts
    pos = state["pos"]
    markers = state["markers"]
    playing = state["playing"]

    # --- 半透明信息条 ---
    bar = pygame.Surface((size[0], 74), pygame.SRCALPHA)
    bar.fill((0, 0, 0, 150))
    screen.blit(bar, (0, 0))

    color = (120, 230, 140) if playing else (255, 200, 80)
    status = "播放中" if playing else "已暂停"
    screen.blit(f_big.render(f"{fmt(pos)}", True, (255, 255, 255)), (10, 6))
    screen.blit(f_small.render(status, True, color), (10, 44))

    hint = "Space:暂停/播放  P:打点  O:导出  Esc:清空  点击进度条:跳转  Q:退出"
    screen.blit(f_small.render(hint, True, (190, 190, 190)), (size[0] - 362, 10))

    # --- 时间节点显示（也画在画面上） ---
    if markers:
        parts = [f"#{i + 1} {fmt(t)}" for i, t in enumerate(markers)]
        txt = "时间节点:  " + "   →   ".join(parts)
        screen.blit(f_small.render(txt, True, (255, 230, 120)), (150, 44))
    if len(markers) == 2:
        a, b = sorted(markers)
        ready = f"区间长度 {fmt(b - a)}，按 O 导出"
        screen.blit(f_small.render(ready, True, (120, 230, 140)), (size[0] - 362, 34))

    # --- 底部进度条 + 节点刻度（整条区域均可点击跳转）---
    bh = 10
    by = size[1] - bh
    band = pygame.Surface((size[0], SEEK_BAND_H), pygame.SRCALPHA)
    band.fill((0, 0, 0, 110))
    screen.blit(band, (0, size[1] - SEEK_BAND_H))
    pygame.draw.rect(screen, (90, 160, 255),
                     (0, by, int(size[0] * pos / total), bh))
    for i, t in enumerate(markers):
        x = int(size[0] * t / total)
        pygame.draw.line(screen, (255, 80, 80), (x, by - 16), (x, size[1]), 2)
        lab = f_small.render(str(i + 1), True, (255, 80, 80))
        screen.blit(lab, (x - 4, by - 32))

    if flash:
        surf = f_big.render(flash, True, (255, 230, 120))
        screen.blit(surf, ((size[0] - surf.get_width()) // 2, size[1] // 2 - 60))

    pygame.display.flip()


def choose_file():
    if len(sys.argv) > 1:
        return sys.argv[1]
    try:
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        path = filedialog.askopenfilename(
            title="选择 GIF 文件",
            filetypes=[("GIF", "*.gif"), ("所有文件", "*.*")])
        root.destroy()
        return path or None
    except Exception:
        return None


def main():
    path = choose_file()
    if not path:
        print("未选择文件。用法: python gif_splitter.py xxx.gif")
        return
    if not os.path.isfile(path):
        print(f"文件不存在: {path}")
        return

    pygame.init()
    pygame.display.set_caption(Path(path).name)

    size, n_frames = gif_size(path)
    screen = pygame.display.set_mode(size)
    load_font = make_font(22, bold=True)

    def show_progress(done, total_n):
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit()
                sys.exit(0)
        screen.fill((20, 20, 20))
        msg = f"加载帧中... {done}/{total_n}"
        surf = load_font.render(msg, True, (255, 255, 255))
        screen.blit(surf, ((size[0] - surf.get_width()) // 2,
                           (size[1] - surf.get_height()) // 2))
        pygame.display.flip()

    frames, durations, starts, disposals, total = decode_gif(path, show_progress)
    fonts = (make_font(26, bold=True),
             make_font(17))

    def now_ms():
        return pygame.time.get_ticks()

    state = {"playing": True, "pos": 0.0, "markers": []}
    anchor = now_ms()          # pos = elapsed_base + (now - anchor)
    elapsed_base = 0.0
    last_idx = -1
    flash = ""
    flash_until = 0
    clock = pygame.time.Clock()

    def current_pos():
        if state["playing"]:
            return elapsed_base + (now_ms() - anchor)
        return state["pos"]

    def set_flash(msg, ms=1500):
        nonlocal flash, flash_until
        flash = msg
        flash_until = now_ms() + ms

    running = True
    while running:
        # 高频轮询：事件到来的当帧立即处理，保证暂停响应快
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                t_now = now_ms()
                if ev.key == pygame.K_SPACE:
                    if state["playing"]:
                        state["pos"] = elapsed_base + (t_now - anchor)
                        state["playing"] = False
                        set_flash(f"已暂停 @ {fmt(state['pos'])}")
                    else:
                        elapsed_base = state["pos"]
                        anchor = t_now
                        state["playing"] = True
                        set_flash("")
                elif ev.key == pygame.K_p:
                    if len(state["markers"]) >= 2:
                        set_flash("已有 2 个节点，按 P 无效（Esc 可清空）")
                    else:
                        p = current_pos()
                        state["markers"].append(p)
                        set_flash(f"记录节点 #{len(state['markers'])} @ {fmt(p)}")
                elif ev.key == pygame.K_o:
                    if len(state["markers"]) == 2:
                        out, err = export_segment(path, *state["markers"])
                        set_flash(err if err else f"已导出: {os.path.basename(out)}", 3000)
                    else:
                        set_flash("需要先用 P 记录 2 个节点")
                elif ev.key == pygame.K_ESCAPE:
                    state["markers"].clear()
                    set_flash("已清空所有时间节点")
                elif ev.key == pygame.K_q:
                    running = False
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                mx, my = ev.pos
                # 点击底部进度条区域（含进度条上方若干像素，方便点）
                if my >= size[1] - SEEK_BAND_H:
                    ratio = max(0.0, min(1.0, mx / size[0]))
                    target = ratio * total
                    state["pos"] = target
                    elapsed_base = target
                    state["playing"] = False      # 跳转后保持暂停，按空格继续
                    set_flash(f"跳转 @ {fmt(target)}（已暂停）")

        # 播放位置推进 & 循环
        if state["playing"]:
            pos = elapsed_base + (now_ms() - anchor)
            if pos >= total:
                pos %= total
                elapsed_base = pos
                anchor = now_ms()
            state["pos"] = pos
        else:
            state["pos"] = min(state["pos"], total - 1)

        idx = min(bisect.bisect_right(starts, state["pos"]) - 1, len(frames) - 1)
        if idx != last_idx:
            last_idx = idx

        if now_ms() > flash_until:
            flash = ""

        draw(screen, frames[idx], state, starts, total, size, fonts, flash)
        clock.tick(240)   # 输入轮询频率 240Hz，暂停延迟 ≈ 4ms

    pygame.quit()


if __name__ == "__main__":
    main()
