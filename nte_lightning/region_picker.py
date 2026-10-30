"""Full-screen overlay for drawing detection regions on a screenshot."""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from PIL import Image, ImageTk

from .config import Region

HELP = (
    "Drag to draw a region where lightning should be detected.   "
    "Right-click a region to delete it.   C = clear all.   "
    "Enter = save.   Esc = cancel."
)
OUTLINE = "#33e0ff"


class RegionPicker:
    def __init__(
        self,
        master: tk.Misc,
        image: Image.Image,
        regions: list[Region],
        on_done: Callable[[list[Region] | None], None],
    ):
        self.image = image
        self.regions = [Region(r.x, r.y, r.w, r.h) for r in regions]
        self.on_done = on_done
        self._drag_start: tuple[float, float] | None = None
        self._drag_item: int | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._scale = 1.0
        self._offset = (0.0, 0.0)

        top = self.top = tk.Toplevel(master)
        top.title("Select lightning regions")
        top.configure(background="black")
        top.attributes("-fullscreen", True)
        top.attributes("-topmost", True)
        canvas = self.canvas = tk.Canvas(top, background="black", highlightthickness=0, cursor="crosshair")
        canvas.pack(fill="both", expand=True)

        canvas.bind("<Configure>", lambda e: self._render())
        canvas.bind("<ButtonPress-1>", self._on_press)
        canvas.bind("<B1-Motion>", self._on_drag)
        canvas.bind("<ButtonRelease-1>", self._on_release)
        canvas.bind("<Button-3>", self._on_delete)
        top.bind("<Return>", lambda e: self._finish(True))
        top.bind("<KP_Enter>", lambda e: self._finish(True))
        top.bind("<Escape>", lambda e: self._finish(False))
        top.bind("<Key-c>", lambda e: self._clear())
        top.bind("<Key-C>", lambda e: self._clear())
        top.protocol("WM_DELETE_WINDOW", lambda: self._finish(False))
        top.after(50, self._grab_focus)

    def _grab_focus(self) -> None:
        self.top.lift()
        self.top.focus_force()
        self.canvas.focus_set()

    # ----------------------------------------------------------- coordinates

    def _to_screen(self, x: float, y: float) -> tuple[float, float]:
        return x * self._scale + self._offset[0], y * self._scale + self._offset[1]

    def _to_image(self, x: float, y: float) -> tuple[int, int]:
        ix = (x - self._offset[0]) / self._scale
        iy = (y - self._offset[1]) / self._scale
        return (
            int(round(min(max(ix, 0), self.image.width))),
            int(round(min(max(iy, 0), self.image.height))),
        )

    # -------------------------------------------------------------- drawing

    def _render(self) -> None:
        c = self.canvas
        cw, ch = max(1, c.winfo_width()), max(1, c.winfo_height())
        iw, ih = self.image.size
        self._scale = min(cw / iw, ch / ih)
        dw, dh = max(1, int(iw * self._scale)), max(1, int(ih * self._scale))
        self._offset = ((cw - dw) / 2, (ch - dh) / 2)
        shown = self.image if (dw, dh) == (iw, ih) else self.image.resize((dw, dh), Image.BILINEAR)
        self._photo = ImageTk.PhotoImage(shown)
        c.delete("all")
        c.create_image(self._offset[0], self._offset[1], image=self._photo, anchor="nw")
        self._draw_regions()
        self._draw_help()

    def _draw_regions(self) -> None:
        c = self.canvas
        c.delete("region")
        for i, r in enumerate(self.regions):
            x0, y0 = self._to_screen(r.x, r.y)
            x1, y1 = self._to_screen(r.x + r.w, r.y + r.h)
            c.create_rectangle(x0, y0, x1, y1, outline="black", width=4, tags="region")
            c.create_rectangle(x0, y0, x1, y1, outline=OUTLINE, width=2, tags="region")
            c.create_text(
                x0 + 8, y0 + 6, text=str(i + 1), anchor="nw", fill=OUTLINE,
                font=("Segoe UI", 16, "bold"), tags="region",
            )

    def _draw_help(self) -> None:
        c = self.canvas
        c.delete("help")
        text = HELP + f"   ({len(self.regions)} region(s))"
        item = c.create_text(
            c.winfo_width() / 2, 18, text=text, anchor="n", fill="white",
            font=("Segoe UI", 12), tags="help",
        )
        x0, y0, x1, y1 = c.bbox(item)
        bg = c.create_rectangle(x0 - 12, y0 - 6, x1 + 12, y1 + 6, fill="#202020", outline=OUTLINE, tags="help")
        c.tag_lower(bg, item)

    # --------------------------------------------------------------- events

    def _on_press(self, e: tk.Event) -> None:
        self._drag_start = (e.x, e.y)
        self._drag_item = self.canvas.create_rectangle(e.x, e.y, e.x, e.y, outline=OUTLINE, width=2, dash=(6, 4))

    def _on_drag(self, e: tk.Event) -> None:
        if self._drag_start and self._drag_item:
            self.canvas.coords(self._drag_item, *self._drag_start, e.x, e.y)

    def _on_release(self, e: tk.Event) -> None:
        if not self._drag_start:
            return
        if self._drag_item:
            self.canvas.delete(self._drag_item)
        (sx, sy), self._drag_start, self._drag_item = self._drag_start, None, None
        if abs(e.x - sx) < 6 or abs(e.y - sy) < 6:
            return
        x0, y0 = self._to_image(min(sx, e.x), min(sy, e.y))
        x1, y1 = self._to_image(max(sx, e.x), max(sy, e.y))
        if x1 - x0 >= 4 and y1 - y0 >= 4:
            self.regions.append(Region(x0, y0, x1 - x0, y1 - y0))
            self._draw_regions()
            self._draw_help()

    def _on_delete(self, e: tk.Event) -> None:
        ix, iy = self._to_image(e.x, e.y)
        for i in range(len(self.regions) - 1, -1, -1):
            r = self.regions[i]
            if r.x <= ix <= r.x + r.w and r.y <= iy <= r.y + r.h:
                del self.regions[i]
                self._draw_regions()
                self._draw_help()
                return

    def _clear(self) -> None:
        self.regions.clear()
        self._draw_regions()
        self._draw_help()

    def _finish(self, save: bool) -> None:
        self.top.destroy()
        self.on_done(self.regions if save else None)
