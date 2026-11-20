"""Generate GitHub social-preview and screenshot-tour images.

Optional maintainer utility: requires Pillow. Run with a Python environment that
has Pillow installed; generated files are committed under docs/images/.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "docs" / "images"


def font(size: int, bold: bool = False):
    candidates = [
        Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
             else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def build_social_preview():
    width, height = 1280, 640
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        draw.line((0, y, width, y), fill=(9 + y // 80, 18 + y // 55, 34 + y // 35))

    logo = Image.open(IMAGES / "mind-ashare-logo.png").convert("RGBA")
    logo = ImageOps.contain(logo, (92, 92))
    image.paste(logo, (64, 62), logo)
    draw = ImageDraw.Draw(image)
    draw.text((64, 180), "astock-hub", font=font(58, True), fill="#f1f5f9")
    draw.text((66, 255), "系统名称  Mind AShare", font=font(25), fill="#7dd3fc")
    draw.text((64, 318), "可自托管的 A 股数据聚合", font=font(34, True), fill="#e2e8f0")
    draw.text((64, 366), "与 AI 研究面板", font=font(34, True), fill="#e2e8f0")

    shot = Image.open(IMAGES / "dashboard.jpg").convert("RGB")
    shot = ImageOps.fit(shot, (590, 410), method=Image.Resampling.LANCZOS,
                        centering=(0.5, 0.15))
    image.paste(shot, (650, 82))
    draw.rounded_rectangle((648, 80, 1242, 494), radius=12,
                           outline="#334155", width=2)

    labels = ["实时看板", "个股研究", "板块轮动", "AI 可选"]
    x = 64
    for label in labels:
        w = 42 + len(label) * 22
        draw.rounded_rectangle((x, 456, x + w, 500), radius=18,
                               fill="#152b46", outline="#24507a")
        draw.text((x + 18, 465), label, font=font(18), fill="#bae6fd")
        x += w + 12
    draw.text((64, 566), "MIT  ·  Self-hosted  ·  Your data, your keys",
              font=font(21), fill="#94a3b8")
    image.save(IMAGES / "social-preview.png", optimize=True)


def build_tour_gif():
    sources = [
        ("dashboard.jpg", "01  实时看板 · 市场宽度与情绪"),
        ("stock-analysis.jpg", "02  个股分析 · 图表与关键数据"),
        ("sector-analysis.jpg", "03  板块分析 · 主线与轮动"),
        ("ai-chat.jpg", "04  AI 问答 · 按需接入自己的模型"),
    ]
    frames = []
    for filename, title in sources:
        src = Image.open(IMAGES / filename).convert("RGB")
        if filename == "ai-chat.jpg":
            content = ImageOps.contain(src, (1280, 664), method=Image.Resampling.LANCZOS)
        else:
            content = ImageOps.fit(src, (1280, 664), method=Image.Resampling.LANCZOS,
                                   centering=(0.5, 0.05))
        frame = Image.new("RGB", (1280, 720), "#0b1220")
        frame.paste(content, ((1280 - content.width) // 2, 56))
        draw = ImageDraw.Draw(frame)
        draw.text((28, 14), title, font=font(23, True), fill="#e2e8f0")
        frames.append(frame.quantize(colors=160, method=Image.Quantize.MEDIANCUT))
    frames[0].save(IMAGES / "overview.gif", save_all=True, append_images=frames[1:],
                   duration=2600, loop=0, optimize=True)


if __name__ == "__main__":
    build_social_preview()
    build_tour_gif()
    print("Generated docs/images/social-preview.png and overview.gif")
