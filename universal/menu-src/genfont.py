# Generates a 6x10 monochrome font (ASCII 32..126) for the SSD1306 menu
from PIL import Image, ImageDraw, ImageFont
import sys
W,H=6,10
f=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", int(sys.argv[1]) if len(sys.argv)>1 else 9)
glyphs=[]
sheet=Image.new("1",(W*32*4,H*3*4),0)
for i,c in enumerate(range(32,127)):
    im=Image.new("1",(W,H),0); d=ImageDraw.Draw(im); d.fontmode="1"
    d.text((0,-1),chr(c),font=f,fill=1)
    cols=[]
    for x in range(W):
        v=0
        for y in range(H):
            if im.getpixel((x,y)): v|=1<<y
        cols.append(v)
    glyphs.append(cols)
    big=im.resize((W*4,H*4)); sheet.paste(big,((i%32)*W*4,(i//32)*H*4))
sheet.save("/tmp/font.png")
with open(sys.argv[2] if len(sys.argv)>2 else "font6x10.h","w") as o:
    o.write("// 6x10 font, ASCII 32..126, column-major, bit0 = top row\n#pragma once\n#include <stdint.h>\nstatic const uint16_t FONT6x10[95][6] = {\n")
    for c,g in zip(range(32,127),glyphs):
        o.write("  {"+",".join("0x%03x"%v for v in g)+"}, // %r\n"%chr(c))
    o.write("};\n")
