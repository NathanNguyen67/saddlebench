#!/usr/bin/env python3
"""Browser-free PNG/SVG output for each dataset-specific SaddleBench leaderboard.

Read-only post-processing of coverage-tier JSON. No changes to scientific scoring.
PNG uses Pillow if already installed, otherwise optional CairoSVG/rsvg-convert.
SVG is always available with only the Python standard library.
"""
from __future__ import annotations
import argparse
import html
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from saddlebench.coverage_leaderboard import build_boards, fmt_cov, fmt_dev, fmt_time, grade

WIDTH = 1480
LEFT = 68
RIGHT = WIDTH - 68
COL = [68, 368, 476, 591, 720, 849, 978, 1110, 1258]
# Each cell uses these right edges. MODEL is left-aligned, remaining cells right-aligned.
HEADERS = ['MODEL', 'CASES', 'FOUND', '≤ .20 Å', '≤ .10 Å', '≤ .05 Å', 'MEDIAN', 'BEST MED', 'TIME']
INK = '#202c3d'
MUTED = '#66788d'
BG = '#f3f6fa'
WHITE = '#ffffff'
LINE = '#dde5ef'
CYAN = '#1685a4'
PURPLE = '#7d55b0'
BLUE = '#5074b6'
GOOD = '#13875a'
MID = '#a86d14'
LOW = '#c6504f'


def metrics(board, model):
    t = model['tiers']['0.20']
    return [model['model'], str(model['cases']), f"{t['u']}/{t['k']}",
            *(fmt_cov(model['tiers'][x]['cov']) for x in ('0.20', '0.10', '0.05')),
            fmt_dev(t['median']), fmt_dev(t['best']), fmt_time(model['time_s'])]


def _escape(v):
    return html.escape(str(v), quote=True)


def _board_height(board):
    # Reserve generous fixed-height rows and a multi-line legend.
    return 595 + 59 * max(1, len(board['models']))


def svg_board(board):
    """Native SVG with no scripts, external assets, or network loads."""
    h = _board_height(board)
    a = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {h}" width="{WIDTH}" height="{h}">',
         f'<rect width="{WIDTH}" height="{h}" fill="{BG}"/>',
         '<style>text{font-family:DejaVu Sans,Liberation Sans,Arial,sans-serif;dominant-baseline:middle}</style>']

    def rect(x, y, w, height, fill, radius=0, stroke=None):
        stroke_attr = f' stroke="{stroke}"' if stroke else ''
        a.append(f'<rect x="{x}" y="{y}" width="{w}" height="{height}" rx="{radius}" fill="{fill}"{stroke_attr}/>')

    def txt(text, x, y, size=16, color=INK, bold=False, anchor='start', spacing=None):
        extra = f' letter-spacing="{spacing}"' if spacing else ''
        a.append(f'<text x="{x}" y="{y}" fill="{color}" font-size="{size}" '
                 f'font-weight="{700 if bold else 400}" text-anchor="{anchor}"{extra}>{_escape(text)}</text>')

    def line(y):
        rect(LEFT + 25, y, RIGHT - LEFT - 50, 1, LINE)

    txt('SADDLEBENCH', LEFT, 54, 17, CYAN, True, spacing='2')
    txt('Reference coverage & geometric accuracy', LEFT, 103, 30, INK, True)
    txt('Dataset-specific leaderboard  ·  matched references, not independent saddle validation', LEFT, 143, 16, MUTED)
    card_x, card_y, card_w = LEFT, 177, RIGHT - LEFT
    rect(card_x, card_y, card_w, h - card_y - 40, WHITE, 12, LINE)
    txt(board['dataset'], LEFT + 27, 219, 25, INK, True)
    qty = board['evaluated_cases']
    txt(f'{qty} evaluated case' + ('' if qty == 1 else 's'), LEFT + 27, 254, 15, MUTED)
    notice = 'Same evaluation cases across models' if board['equal_sets'] else 'Different case sets — not ranked'
    txt(notice, RIGHT - 27, 231, 14, MUTED, anchor='end')
    line(282)
    txt('REFERENCE COVERAGE (%)', 713, 315, 13, CYAN, True, anchor='middle')
    txt('GEOMETRY (Å)', 1067, 315, 13, PURPLE, True, anchor='middle')
    txt('RUNTIME', 1305, 315, 13, BLUE, True, anchor='middle')
    for j, header in enumerate(HEADERS):
        color = CYAN if 3 <= j <= 5 else PURPLE if 6 <= j <= 7 else BLUE if j == 8 else MUTED
        txt(header, COL[j] + (27 if j == 0 else 0), 348, 13, color, True,
            'start' if j == 0 else 'end')
    line(373)
    first_y = 402
    for i, model in enumerate(board['models']):
        baseline = first_y + i * 59
        fields = metrics(board, model)
        if i % 2 == 1:
            rect(LEFT + 20, baseline - 25, RIGHT - LEFT - 40, 50, '#f8fafd', 4)
        for j, val in enumerate(fields):
            if j == 0:
                if len(val) > 28:
                    val = val[:25] + '...'
                txt(val, COL[j]+27, baseline, 17, INK, True)
            elif 3 <= j <= 5:
                cov = model['tiers'][('0.20', '0.10', '0.05')[j-3]]['cov']
                color = {'good':GOOD,'mid':MID,'low':LOW,'muted':MUTED}[grade(cov)]
                rect(COL[j] - 95, baseline + 15, 60, 5, '#e8edf2', 3)
                if cov is not None:
                    rect(COL[j] - 95, baseline + 15, max(1, 60 * max(0, min(1, cov))), 5, color, 3)
                txt(val, COL[j], baseline - 3, 17, color, True, 'end')
            else:
                txt(val, COL[j], baseline, 16,
                    PURPLE if j in (6,7) else BLUE if j == 8 else INK, False, 'end')
    legend_top = first_y + 59 * max(1, len(board['models'])) - 5
    line(legend_top)
    a.append('<!-- Legend, separated into the same three color families as the table. -->')
    subtitle_y = legend_top + 31
    txt('COVERAGE', LEFT + 27, subtitle_y, 14, CYAN, True)
    txt('GEOMETRY (Å)', LEFT + 495, subtitle_y, 14, PURPLE, True)
    txt('RUNTIME', LEFT + 980, subtitle_y, 14, BLUE, True)
    fy = subtitle_y + 31
    for i, text in enumerate(('Known eligible reactions found / known references.',
                               '0.20, 0.10, 0.05 Å are maxdev cutoffs.',
                               'Green ≥90%   Amber 70–<90%   Red <70%')):
        txt(text, LEFT + 27, fy + 28*i, 13, MUTED)
    for i, text in enumerate(('Median: all matched predictions (repeats included).',
                               'Best med: median of best observed match / reaction.',
                               'Unmatched predictions are excluded.')):
        txt(text, LEFT + 495, fy + 28*i, 13, MUTED)
    for i, text in enumerate(('Self-reported method execution time.',
                               'Evaluation overhead is excluded.')):
        txt(text, LEFT + 980, fy + 28*i, 13, MUTED)
    txt('Coverage is relative to recorded eligible reactions, not all physically possible reactions.',
        LEFT, h - 17, 13, MUTED)
    a.append('</svg>')
    return '\n'.join(a) + '\n'


def _png_from_svg(svg_path, png_path):
    """Best effort conversion with tools already present; never install packages."""
    try:
        import cairosvg
    except ImportError:
        cairosvg = None
    if cairosvg is not None:
        try:
            cairosvg.svg2png(url=str(svg_path), write_to=str(png_path))
            return True, 'CAIROSVG'
        except Exception:
            if png_path.exists(): png_path.unlink()
    from shutil import which
    if which('rsvg-convert'):
        try:
            with open(png_path, 'xb') as f:
                subprocess.run(['rsvg-convert', str(svg_path)], stdout=f,
                               check=True, stderr=subprocess.DEVNULL)
            return True, 'RSVG'
        except (OSError,subprocess.CalledProcessError):
            if png_path.exists(): png_path.unlink()
    if which('convert'):
        try:
            subprocess.run(['convert', '-background', 'white', str(svg_path), str(png_path)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True, 'IMAGEMAGICK'
        except (OSError,subprocess.CalledProcessError):
            if png_path.exists(): png_path.unlink()
    return False, 'NO_SVG_RASTERIZER'



def _png_using_pillow(board, png_path):
    """Draw PNG directly if Pillow exists; avoids requiring browser or SVG tools."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return False
    height = _board_height(board)
    image = Image.new('RGB', (WIDTH, height), BG)
    draw = ImageDraw.Draw(image)
    font_path = next((str(p) for p in (
        Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
        Path('/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf'),
    ) if p.exists()), None)
    bold_path = next((str(p) for p in (
        Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'),
        Path('/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf'),
    ) if p.exists()), font_path)
    fonts = {}
    def font(size, bold=False):
        key=(size,bold)
        if key not in fonts:
            path=bold_path if bold else font_path
            try: fonts[key]=ImageFont.truetype(path,size) if path else ImageFont.load_default(size=size)
            except (OSError,TypeError): fonts[key]=ImageFont.load_default()
        return fonts[key]
    def rect(x,y,w,h,fill,radius=0,outline=None):
        draw.rounded_rectangle((x,y,x+w,y+h),radius=radius,fill=fill,outline=outline)
    def txt(value,x,y,size=16,color=INK,bold=False,align='left'):
        anchor={'left':'lm','middle':'mm','right':'rm'}[align]
        draw.text((x,y), str(value), font=font(size,bold), fill=color, anchor=anchor)
    def line(y): rect(LEFT+25,y,RIGHT-LEFT-50,1,LINE)
    txt('SADDLEBENCH',LEFT,54,17,CYAN,True)
    txt('Reference coverage & geometric accuracy',LEFT,103,30,INK,True)
    txt('Dataset-specific leaderboard  ·  matched references, not independent saddle validation',LEFT,143,16,MUTED)
    rect(LEFT,177,RIGHT-LEFT,height-217,WHITE,12,LINE)
    txt(board['dataset'],LEFT+27,219,25,INK,True)
    qty=board['evaluated_cases']
    txt(f'{qty} evaluated case'+('' if qty==1 else 's'),LEFT+27,254,15,MUTED)
    txt('Same evaluation cases across models' if board['equal_sets'] else 'Different case sets - not ranked',RIGHT-27,231,14,MUTED,align='right')
    line(282)
    txt('REFERENCE COVERAGE (%)',713,315,13,CYAN,True,'middle')
    txt('GEOMETRY (Å)',1067,315,13,PURPLE,True,'middle')
    txt('RUNTIME',1305,315,13,BLUE,True,'middle')
    for j, header in enumerate(HEADERS):
        color=CYAN if 3<=j<=5 else PURPLE if 6<=j<=7 else BLUE if j==8 else MUTED
        txt(header,COL[j]+(27 if j==0 else 0),348,13,color,True,'left' if j==0 else 'right')
    line(373)
    first_y=402
    for i,model in enumerate(board['models']):
        y=first_y+i*59
        fields=metrics(board,model)
        if i%2:rect(LEFT+20,y-25,RIGHT-LEFT-40,50,'#f8fafd',4)
        for j,val in enumerate(fields):
            if j==0:
                if len(val)>28: val=val[:25]+'...'
                txt(val,COL[j]+27,y,17,INK,True)
            elif 3<=j<=5:
                cov=model['tiers'][('0.20','0.10','0.05')[j-3]]['cov']
                color={'good':GOOD,'mid':MID,'low':LOW,'muted':MUTED}[grade(cov)]
                rect(COL[j]-95,y+15,60,5,'#e8edf2',3)
                if cov is not None:rect(COL[j]-95,y+15,max(1,60*min(1,max(0,cov))),5,color,3)
                txt(val,COL[j],y-3,17,color,True,'right')
            else:
                txt(val,COL[j],y,16,PURPLE if j in (6,7) else BLUE if j==8 else INK,align='right')
    legend_top=first_y+59*max(1,len(board['models']))-5
    line(legend_top)
    subtitle_y=legend_top+31
    txt('COVERAGE',LEFT+27,subtitle_y,14,CYAN,True)
    txt('GEOMETRY (Å)',LEFT+495,subtitle_y,14,PURPLE,True)
    txt('RUNTIME',LEFT+980,subtitle_y,14,BLUE,True)
    fy=subtitle_y+31
    for i,s in enumerate(('Known eligible reactions found / known references.',
        '0.20, 0.10, 0.05 Å are maxdev cutoffs.',
        'Green ≥90%   Amber 70–<90%   Red <70%')):
        txt(s,LEFT+27,fy+28*i,13,MUTED)
    for i,s in enumerate(('Median: all matched predictions (repeats included).',
        'Best med: median of best observed match / reaction.',
        'Unmatched predictions are excluded.')):
        txt(s,LEFT+495,fy+28*i,13,MUTED)
    for i,s in enumerate(('Self-reported method execution time.',
        'Evaluation overhead is excluded.')):
        txt(s,LEFT+980,fy+28*i,13,MUTED)
    txt('Coverage is relative to recorded eligible reactions, not all physically possible reactions.',
        LEFT,height-17,13,MUTED)
    # File did not exist on entry. PNG encoder writes directly to this new file.
    with png_path.open('xb') as out:
        image.save(out,format='PNG')
    return True

def safe_name(dataset):
    slug = re.sub(r'[^A-Za-z0-9_-]+', '_', dataset).strip('_')
    if not slug or slug in ('.', '..'):
        slug = 'UNKNOWN'
    return slug[:60]


def render_images(report_path, *, out_dir=None):
    source = Path(report_path).expanduser().absolute()
    report = json.loads(source.read_text(encoding='utf8'))
    boards = build_boards(report)
    root = Path(out_dir).expanduser().absolute() if out_dir else source.parent
    if not root.is_dir():
        raise FileNotFoundError(str(root))
    outputs = []
    seen_names = set()
    for board in boards:
        slug = safe_name(board['dataset'])
        if slug in seen_names:
            raise ValueError('DATASET_FILENAME_COLLISION')
        seen_names.add(slug)
        stem = f'{source.stem}_{slug}'
        svg = root / (stem + '.svg')
        png = root / (stem + '.png')
        if svg.exists() and (not svg.is_file() or svg.is_symlink()):
            raise ValueError('UNSAFE_EXISTING_SVG: '+str(svg))
        if png.exists() and (not png.is_file() or png.is_symlink()):
            raise ValueError('UNSAFE_EXISTING_PNG: '+str(png))
        if not svg.exists():
            with svg.open('x', encoding='utf8') as f:
                f.write(svg_board(board))
        if not png.exists():
            try:
                created, backend = _png_from_svg(svg,png)
                if not created and _png_using_pillow(board,png):
                    created, backend = True, 'PILLOW'
            except Exception as exc:
                # Conversion failure must not claim a complete/valid PNG.
                if png.exists():
                    png.unlink()
                created, backend = False, 'CONVERSION_ERROR:'+str(exc)[:120]
        else:
            created, backend = True, 'EXISTING'
        outputs.append({'dataset':board['dataset'], 'svg':str(svg),
                        'png': str(png) if created else None, 'conversion':backend})
    return outputs


def main(argv=None):
    p=argparse.ArgumentParser(description='Produce dataset-specific browser-free SVG and PNG leaderboards')
    p.add_argument('report', help='existing coverage-tier JSON')
    p.add_argument('--open', action='store_true', help='request X11 image viewer')
    args=p.parse_args(argv)
    results=render_images(args.report)
    print('LEADERBOARD_IMAGE_RENDER=PASS')
    for item in results:
        print('DATASET='+item['dataset'])
        print('SVG_THIS='+item['svg'])
        if item['png']:
            print('PNG_THIS='+item['png'])
        else:
            print('PNG_NOTE='+item['conversion']+'; SVG output is still available')
        if args.open:
            from saddlebench.coverage_image_open import open_image
            target=item['png'] or item['svg']
            status=open_image(target)
            print('IMAGE_OPEN='+status)
            if status.startswith(('FAILED_', 'SKIPPED_')):
                print('IMAGE_OPEN_HINT=No usable remote GUI image opener. The PNG/SVG remains saved; see README.')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
