"""生成桌面快捷方式用的图标：深底圆角 + 一个「简」字。

一次性脚本，图标生成完就不需要再跑了。产物：assets/简报.ico
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 256
OUT = Path("assets/简报.ico")

# 用界面上的两个领域色做一道斜向渐变，和软件里看到的颜色是一套
TOP = (76, 29, 149)      # 紫（游戏）
BOTTOM = (2, 132, 199)   # 蓝（AI）


def build() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))

    # 渐变底
    grad = Image.new("RGBA", (SIZE, SIZE))
    for y in range(SIZE):
        t = y / (SIZE - 1)
        color = tuple(int(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3)) + (255,)
        for x in range(SIZE):
            grad.putpixel((x, y), color)

    # 裁成圆角方形
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=54, fill=255)
    img.paste(grad, (0, 0), mask)

    draw = ImageDraw.Draw(img)

    # 一条细白线，暗示「报纸的分隔线」
    draw.line([(52, 74), (SIZE - 52, 74)], fill=(255, 255, 255, 90), width=4)

    # 主字
    font = None
    for path in (
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
    ):
        if Path(path).exists():
            font = ImageFont.truetype(path, 128)
            break
    if font is None:  # 实在找不到中文字体就退回默认
        font = ImageFont.load_default()

    draw.text((SIZE / 2, SIZE / 2 + 12), "简", font=font, fill=(255, 255, 255, 255), anchor="mm")

    return img


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    icon = build()
    icon.save(OUT, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)])
    print(f"图标已生成：{OUT}  ({OUT.stat().st_size} 字节)")
    for size in (256, 64, 32, 16):
        icon.resize((size, size), Image.LANCZOS).save(f"assets/_preview_{size}.png")
    print("预览图：assets/_preview_*.png")
