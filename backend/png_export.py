"""Readable standalone PNG notes with explicit Chinese fonts and Markdown blocks."""
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .note_layout import is_body_paragraph, is_caption_paragraph


def render_png(content, task_dir):
    from .math_render import prepare_math, formula_image
    content = prepare_math(content)
    width, margin = 1200, 84
    available = width - margin * 2
    regular = next((p for p in [Path('C:/Windows/Fonts/msyh.ttc'), Path('C:/Windows/Fonts/simhei.ttf'), Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'), Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')] if p.is_file()), None)
    heavy = next((p for p in [Path('C:/Windows/Fonts/msyhbd.ttc'), Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc')] if p.is_file()), regular)
    def font(size, bold=False):
        path = heavy if bold else regular
        return ImageFont.truetype(str(path), size) if path else ImageFont.load_default(size=size)
    body, small = font(26), font(21)
    headings = {1:font(44,True), 2:font(34,True), 3:font(29,True)}
    measure = ImageDraw.Draw(Image.new('RGB',(1,1)))
    commands=[]
    y=margin
    def wrap(text, selected, max_width, first_line_indent=0):
        # Preserve whitespace/newlines while keeping Latin words together where possible.
        import re
        lines=[]
        for paragraph in text.split('\n'):
            line=''
            for word in re.findall(r'[A-Za-z0-9_]+(?:[./:@-][A-Za-z0-9_]+)*|[^A-Za-z0-9_]',paragraph):
                if line and measure.textlength(line+word,font=selected)>max_width-(first_line_indent if not lines else 0):
                    lines.append(line.rstrip());line=''
                for char in word:
                    if line and measure.textlength(line+char,font=selected)>max_width-(first_line_indent if not lines else 0):
                        lines.append(line.rstrip());line=''
                    line+=char
            lines.append(line.rstrip())
        return lines
    def text_block(text, selected=body, x=margin, max_width=available, color='#334155', gap=18, background=None, first_line_indent=0, center=False):
        nonlocal y
        line_height=round(selected.size*1.7)
        lines=wrap(text,selected,max_width,first_line_indent)
        height=len(lines)*line_height
        if background:
            commands.append(('box',(margin,y-8,width-margin,y+height+8),background,12))
            if background=='#f3f6fb':commands.append(('box',(margin,y-8,margin+4,y+height+8),'#6483b7',0))
        for index,line in enumerate(lines):
            position=x+(max_width-measure.textlength(line,font=selected))/2 if center else x+(first_line_indent if index==0 else 0)
            commands.append(('text',(position,y),line,selected,color));y+=line_height
        y+=gap
    def picture(src, caption):
        nonlocal y
        data = formula_image(src)
        if data:
            with Image.open(BytesIO(data)) as source:
                image = source.convert('RGB')
                image.thumbnail((available, max(1, round(image.height*.75))), Image.Resampling.LANCZOS)
            commands.append(('image',(margin,y),image,None))
            y += image.height + 12
            return
        if not src.startswith(('frames/','./frames/','images/','./images/')):return
        path=(Path(task_dir)/'frames'/Path(unquote(urlsplit(src).path)).name).resolve()
        if path.parent!=(Path(task_dir)/'frames').resolve() or not path.is_file():return
        with Image.open(path) as source:
            image=ImageOps.exif_transpose(source).convert('RGB')
            image.thumbnail((available,available),Image.Resampling.LANCZOS)
        mask=Image.new('L',image.size,0)
        ImageDraw.Draw(mask).rounded_rectangle((0,0,image.width-1,image.height-1),radius=12,fill=255)
        commands.append(('image',((width-image.width)//2,y),image,mask))
        y+=image.height+12
        if caption:text_block(caption,small,color='#64748b',gap=20,center=True)
        else:y+=16
    def inline_text(token):
        return ''.join('\n' if c.type in {'softbreak','hardbreak'} else c.content if c.type in {'text','code_inline'} else '' for c in token.children or [])

    def formula_paragraph(token, selected, x, max_width, prefix='', color='#334155', background=None, first_line_indent=0):
        """Lay out text and inline formula runs in source order on the same line."""
        nonlocal y
        start_y, start_command = y, len(commands)
        line, used = [], 0
        first_line = True
        text_height = round(selected.size*1.7)
        def flush():
            nonlocal y, used, first_line
            if not line:
                return
            height=max(item[2] for item in line)
            position=x+(first_line_indent if first_line else 0)
            for kind, value, item_height, item_width in line:
                if kind=='text':
                    commands.append(('text',(position,y+(height-text_height)//2),value,selected,color))
                else:
                    commands.append(('image',(round(position),y+(height-item_height)//2),value,None))
                position+=item_width
            y+=height
            line.clear();used=0;first_line=False
        def add_text(value):
            nonlocal used, y
            for char in value:
                if char=='\n':
                    flush();continue
                char_width=measure.textlength(char,font=selected)
                if used+char_width>max_width-(first_line_indent if first_line else 0):
                    flush()
                # Keep adjacent characters in one draw call for font kerning.
                if line and line[-1][0]=='text':
                    kind,previous,height,previous_width=line[-1]
                    line[-1]=(kind,previous+char,height,previous_width+char_width)
                else:
                    line.append(('text',char,text_height,char_width))
                used+=char_width
        add_text(prefix)
        for child in token.children or []:
            if child.type in {'text','code_inline'}:
                add_text(child.content)
            elif child.type in {'softbreak','hardbreak'}:
                add_text('\n')
            elif child.type=='image':
                src=child.attrGet('src') or ''
                data=formula_image(src)
                if not data:
                    flush();picture(src,child.content);continue
                with Image.open(BytesIO(data)) as source:
                    image=source.convert('RGB')
                    factor=min(selected.size/40, max_width/image.width)
                    image=image.resize((max(1,round(image.width*factor)),max(1,round(image.height*factor))),Image.Resampling.LANCZOS)
                display=child.attrGet('title')=='note-formula-display'
                if display or used+image.width>max_width-(first_line_indent if first_line else 0):
                    flush()
                line.append(('image',image,image.height,image.width));used+=image.width
                if display:
                    flush();y+=8
        flush()
        if background:
            commands.insert(start_command,('box',(margin,start_y-8,width-margin,y+8),background,12))
        y+=18
    tokens=MarkdownIt('commonmark').enable('table').parse(content)
    heading=0;quote_depth=0;lists=[];prefix='';rows=None;row=None;header=False
    for token in tokens:
        kind=token.type
        if kind=='heading_open':heading=int(token.tag[1:])
        elif kind=='heading_close':heading=0
        elif kind=='blockquote_open':quote_depth+=1
        elif kind=='blockquote_close':quote_depth-=1
        elif kind in {'bullet_list_open','ordered_list_open'}:lists.append([kind=='ordered_list_open',int(token.attrGet('start') or 1)])
        elif kind in {'bullet_list_close','ordered_list_close'}:lists.pop();prefix=''
        elif kind=='list_item_open':
            ordered,number=lists[-1];prefix=f'{number}. ' if ordered else '• ';lists[-1][1]+=1
        elif kind=='list_item_close':prefix=''
        elif kind=='table_open':rows=[]
        elif kind=='thead_open':header=True
        elif kind=='thead_close':header=False
        elif kind=='tr_open':row=[]
        elif kind=='tr_close':rows.append((row,header));row=None
        elif kind=='table_close':
            count=max((len(cells) for cells,_ in rows),default=1)
            cell_width=available/count
            for cells,is_header in rows:
                selected=font(23,is_header)
                wrapped=[wrap(cell[0],selected,cell_width-28) if cell[0] else [] for cell in cells]
                for _, images in cells:
                    for image in images:
                        image.thumbnail((max(1, int(cell_width-28)), image.height), Image.Resampling.LANCZOS)
                row_height=max((len(lines)*38+sum(image.height+8 for image in cell[1]) for lines,cell in zip(wrapped,cells)),default=0)+24
                commands.append(('box',(margin,y,width-margin,y+row_height),'#edf2f8' if is_header else '#ffffff',0))
                for index,lines in enumerate(wrapped):
                    x=margin+round(index*cell_width)
                    commands.append(('border',(x,y,x+round(cell_width),y+row_height),'#dbe3ee'))
                    for j,line in enumerate(lines):commands.append(('text',(x+14,y+12+j*38),line,selected,'#334155'))
                    image_y=y+12+len(lines)*38
                    for image in cells[index][1]:
                        commands.append(('image',(x+14,image_y),image,None));image_y+=image.height+8
                y+=row_height
            y+=24;rows=None
        elif kind=='inline':
            text=inline_text(token)
            if rows is not None:
                images=[]
                for child in token.children or []:
                    if child.type=='image':
                        data=formula_image(child.attrGet('src') or '')
                        if data:
                            with Image.open(BytesIO(data)) as source:
                                image=source.convert('RGB')
                                image.thumbnail((max(1, int(available/max(1,len(row)+1))-28), max(1,round(image.height*.65))), Image.Resampling.LANCZOS)
                                images.append(image)
                row.append((text, images));continue
            has_math=any(child.type=='image' and (child.attrGet('src') or '').startswith('data:image/png;base64,') for child in token.children or [])
            if has_math:
                indent=(28*len(lists) if lists else 0)+(24 if quote_depth else 0)
                formula_paragraph(token,headings.get(heading,headings[3]) if heading else body,
                    margin+indent,available-indent,prefix=prefix,color='#596b83' if quote_depth else '#334155',
                    background='#f3f6fb' if quote_depth else None,
                    first_line_indent=body.size*2 if not heading and not lists and not quote_depth and is_body_paragraph(token) else 0)
                prefix=''
                continue
            if heading:
                if y>margin:y+=20 if heading==1 else 18
                text_block(text,headings.get(heading,headings[3]),color='#18283f',gap=16)
                if heading==1:
                    commands.append(('border',(margin,y,width-margin,y),'#dbe3ee'));y+=24
            elif text.strip() and not lists and not quote_depth and is_caption_paragraph(token):
                text_block(text,small,color='#64748b',gap=20,center=True)
            elif text.strip():
                indent=28*len(lists) if lists else 0
                if quote_depth:indent+=24
                text_block(prefix+text,x=margin+indent,max_width=available-indent- (20 if quote_depth else 0),gap=10 if lists else 18,color='#596b83' if quote_depth else '#334155',background='#f3f6fb' if quote_depth else None,
                    first_line_indent=body.size*2 if not heading and not lists and not quote_depth and is_body_paragraph(token) else 0)
                prefix=''
            for child in token.children or []:
                if child.type=='image':picture(child.attrGet('src') or '',child.content)
        elif kind in {'fence','code_block'}:
            y+=8;text_block(token.content.rstrip(),font(23),x=margin+20,max_width=available-40,background='#f5f7fa',gap=24)
        elif kind=='hr':commands.append(('border',(margin,y+8,width-margin,y+8),'#dbe3ee'));y+=36
    result=Image.new('RGB',(width,max(200,y+margin)),'white');draw=ImageDraw.Draw(result)
    for command in commands:
        if command[0]=='text':_,position,text,selected,color=command;draw.text(position,text,font=selected,fill=color)
        elif command[0]=='box':_,bounds,color,radius=command;draw.rounded_rectangle(bounds,radius=radius,fill=color)
        elif command[0]=='border':_,bounds,color=command;draw.rectangle(bounds,outline=color,width=1)
        elif command[0]=='image':_,position,image,mask=command;result.paste(image,position,mask)
    output=BytesIO();result.save(output,format='PNG');return output.getvalue()
