"""Native PDF text with character geometry; no OCR or inferred words."""
import math

import pypdfium2 as pdfium
import pypdfium2.raw as raw


def native_lines(page, limit):
    """Keep PDFium character order; join font runs, split large physical gutters."""
    textpage=page.get_textpage()
    lines=[];chars=[];boxes=[];sizes=[];weights=[];previous=None
    def flush():
        nonlocal chars,boxes,sizes,weights,previous
        text=''.join(chars).replace('\x00','').strip()
        if text and boxes:
            left=min(b[0] for b in boxes);bottom=min(b[1] for b in boxes)
            right=max(b[2] for b in boxes);top=max(b[3] for b in boxes)
            size=sorted(sizes)[len(sizes)//2] if sizes else top-bottom
            lines.append(dict(text=text,position={'x':left,'y':bottom,'font_size':max(1,size)},
                native_bbox=[left,bottom,right,top],bold=bool(weights and sum(w>=600 for w in weights)>len(weights)/2),native_line=True,source_engine='pdfium'))
        chars=[];boxes=[];sizes=[];weights=[];previous=None
    try:
        # A malformed page cannot make character inspection unbounded.
        count=min(textpage.count_chars(),max(1,limit)*2+1000)
        for index in range(count):
            code=raw.FPDFText_GetUnicode(textpage,index)
            if code in (10,13):flush();continue
            if not code or code>0x10ffff:continue
            char=chr(code)
            try:
                box=textpage.get_charbox(index)
                size=float(raw.FPDFText_GetFontSize(textpage,index))
                if not all(math.isfinite(v) for v in (*box,size)):continue
            except (ValueError,RuntimeError):continue
            if char.strip():
                if previous and box[0]-previous[2]>max(30,size*3.5):flush()
                boxes.append(box);sizes.append(size);weights.append(raw.FPDFText_GetFontWeight(textpage,index));previous=box
            chars.append(char)
        flush()
    finally:
        textpage.close()
    previous_bottom=None
    for line in lines:
        y=line['position']['y']
        line['gap_before']=max(0,previous_bottom-y) if previous_bottom is not None else 0
        previous_bottom=y
    return lines


def columns_uncertain(lines,width):
    # A single centered contact row is not evidence of two columns.
    left=[b for b in lines if b['position']['x']<width*.4]
    right=[b for b in lines if b['position']['x']>width*.5]
    pairs={round(r['position']['y']/10) for r in right if any(abs(r['position']['y']-l['position']['y'])<max(r['position']['font_size'],l['position']['font_size'])*1.5 for l in left)}
    return len(pairs)>=2
