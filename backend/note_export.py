"""Save self-contained note exports; history's task files remain the canonical source."""
import base64
import hashlib
from io import BytesIO
import json
import mimetypes
from pathlib import Path
import re
import shutil
from urllib.parse import quote, unquote, urlsplit
from markdown_it import MarkdownIt
from .note_markdown import normalize_bold_spacing

IMAGE = re.compile(r'(!\[[^\]]*\]\()(?:\./)?(?:frames|images)/([^)]+)\)')
EXTENSIONS = {"markdown":"md","obsidian":"md","docx":"docx","html":"html","json":"json","txt":"txt","png":"png","pdf":"pdf","enex":"enex"}

def assets_directory(note_path):
    note_path = Path(note_path)
    token = hashlib.sha256(note_path.name.encode('utf-8')).hexdigest()[:16]
    return note_path.parent / f"note-{token}-assets"

def referenced_images(content, task_dir):
    images = {}
    for match in IMAGE.finditer(content):
        name=Path(unquote(urlsplit(match[2]).path)).name
        path=(task_dir/"frames"/name).resolve()
        if path.parent==(task_dir/"frames").resolve() and path.is_file():
            images[match[2]]=path
    return images

def plain_blocks(content):
    tokens=MarkdownIt("commonmark").enable("table").parse(content)
    heading=False
    for token in tokens:
        if token.type=="heading_open":heading=True
        elif token.type=="heading_close":heading=False
        elif token.type=="inline":
            text="".join(child.content for child in token.children or [] if child.type in {"text","code_inline","image","softbreak","hardbreak"})
            yield text, heading, [child.attrGet("src") for child in token.children or [] if child.type=="image"]

def png_document(content, task_dir):
    from .png_export import render_png
    return render_png(content, task_dir)


def save_markdown_folder(title, content, task_dir, destination, created_files=None):
    """Export one independent title-named folder; leave source frames untouched."""
    content = normalize_bold_spacing(content)
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '_', str(title)).strip(' .')[:80].rstrip(' .') or 'note'
    if re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', safe, re.I):
        safe = '_' + safe
    # mkdir is the reservation: concurrent exports never share a folder.
    number = 1
    while True:
        name = safe if number == 1 else f'{safe}-{number}'
        folder = destination / name
        try:
            folder.mkdir()
            break
        except FileExistsError:
            number += 1
    owned = []
    try:
        images = referenced_images(content, Path(task_dir)) if task_dir else {}
        for source in set(images.values()):
            target = folder / source.name
            owned.append(target)
            shutil.copy2(source, target)
        content = IMAGE.sub(
            lambda match: match[1] + quote(images[match[2]].name, safe='-_.~') + ')'
            if match[2] in images else match[0], content)
        note = folder / f'{name}.md'
        owned.append(note)
        with note.open('x', encoding='utf-8') as output:
            output.write(content)
    except Exception:
        for path in owned:
            path.unlink(missing_ok=True)
        try:
            folder.rmdir()
        except OSError:
            pass
        raise
    if created_files is not None:
        created_files.extend([note, *[path for path in owned if path != note]])
    return note



def save_note_export(title,content,task_dir,destination,note_format,created_files=None):
    content = normalize_bold_spacing(content)
    if note_format == 'markdown':
        return save_markdown_folder(title, content, task_dir, destination, created_files)
    task_dir=Path(task_dir);destination=Path(destination).resolve()
    if note_format not in EXTENSIONS:raise ValueError("Unsupported note format")
    destination.mkdir(parents=True,exist_ok=True)
    safe=re.sub(r'[<>:"/\\|?*\x00-\x1f]+',"_",title).strip(" .")[:100] or "note"
    token=re.sub(r"[^a-zA-Z0-9]","",task_dir.name)[:12] or "note"
    extension=EXTENSIONS[note_format]
    candidate=destination/f"{safe}-{token}.{extension}"
    index=2
    while candidate.exists() or assets_directory(candidate).exists() or (destination/(candidate.stem+"-assets")).exists():
        candidate=destination/f"{safe}-{index}-{token}.{extension}";index+=1
    owned=[];images=referenced_images(content,task_dir);owned_assets=None
    try:
        if note_format in {"markdown","obsidian","json"} and images:
            assets=assets_directory(candidate);assets.mkdir();owned_assets=assets
            for path in set(images.values()):
                target=assets/path.name
                with target.open("xb") as output:
                    owned.append(target)
                    with path.open("rb") as source:
                        shutil.copyfileobj(source, output)
            content=IMAGE.sub(lambda match:match[1]+assets.name+"/"+images[match[2]].name+")" if match[2] in images else match[0],content)
        if note_format=="docx":
            from .word_export import build_word_document
            data=build_word_document(content,task_dir)
        elif note_format=="pdf":
            from .pdf_export import build_pdf_document
            data=build_pdf_document(content,task_dir,title)
        elif note_format=="enex":
            from .enex_export import build_enex_document
            data=build_enex_document(content,task_dir,title)
        elif note_format=="html":
            from .math_render import prepare_math, formula_image
            content = prepare_math(content)
            parser=MarkdownIt("commonmark",{"html":False}).enable("table")
            def image_rule(tokens,index,options,env):
                import html
                token=tokens[index];src=token.attrGet("src") or ""
                formula = formula_image(src)
                if formula:
                    from PIL import Image
                    with Image.open(BytesIO(formula)) as image:
                        size = image.width/2
                    return '<img alt="'+html.escape(token.content,quote=True)+'" src="'+src+'" style="width:'+str(size)+'px;max-width:100%;vertical-align:middle">'
                name=Path(unquote(urlsplit(src).path)).name
                image_path=(task_dir/"frames"/name).resolve()
                if image_path.parent==(task_dir/"frames").resolve() and image_path.is_file():
                    src="data:"+str(mimetypes.guess_type(name)[0] or "image/jpeg")+";base64,"+base64.b64encode(image_path.read_bytes()).decode()
                    return '<img alt="'+html.escape(token.content,quote=True)+'" src="'+src+'">'
                return html.escape(token.content)
            parser.add_render_rule("image",lambda renderer,tokens,index,options,env:image_rule(tokens,index,options,env))
            body=parser.render(content)
            data=('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{max-width:900px;margin:40px auto;padding:20px;font:16px/1.7 system-ui;color:#172c4b}img{max-width:100%;border-radius:12px}table{border-collapse:collapse}td,th{border:1px solid #ccd7e5;padding:8px}pre{white-space:pre-wrap}</style></head><body>'+body+"</body></html>").encode()
        elif note_format=="json":data=json.dumps({"title":title,"content":content},ensure_ascii=False,indent=2).encode()
        elif note_format=="txt":data="\n\n".join(text for text,_,_ in plain_blocks(content)).encode()
        elif note_format=="png":data=png_document(content,task_dir)
        else:data=content.encode()
        with candidate.open("xb") as output:
            owned.insert(0,candidate)
            output.write(data)
    except BaseException:
        for path in owned:path.unlink(missing_ok=True)
        if owned_assets is not None:
            owned_assets.rmdir()
        raise
    if created_files is not None:created_files.extend(owned)
    return candidate
