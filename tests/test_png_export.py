from io import BytesIO
from PIL import Image, ImageDraw
from backend.png_export import render_png

def test_markdown_text_and_images_survive_layout(tmp_path,monkeypatch):
    (tmp_path/"frames").mkdir()
    Image.new("RGB",(200,100),"#ff0000").save(tmp_path/"frames/shot.png")
    recorded=[]
    original=ImageDraw.ImageDraw.text
    def capture(self,position,text,*args,**kwargs):
        recorded.append((position,text,kwargs["font"].size))
        return original(self,position,text,*args,**kwargs)
    monkeypatch.setattr(ImageDraw.ImageDraw,"text",capture)
    content="# Heading\n\nParagraph before ![Caption](frames/shot.png) after.\n\n## Section\n\n- First item\n- Second item\n\n| Name | Value |\n| --- | --- |\n| Key | Data |\n\n> Quote\n\n```\ncode line\n```"
    with Image.open(BytesIO(render_png(content,tmp_path))) as output:
        assert output.width==1200
        assert output.getpixel((600,275))[0] >= 0
        assert any(count>1000 and color==(255,0,0) for count,color in output.getcolors(output.width*output.height))
    text="\n".join(item[1] for item in recorded)
    for expected in ["Paragraph before  after.","Caption","• First item","• Second item","Name","Key","Data","Quote","code line"]:assert expected in text
    assert next(size for _,text,size in recorded if text=="Heading") > next(size for _,text,size in recorded if text=="Section")

def test_external_image_does_not_resolve_to_local_frame(tmp_path):
    (tmp_path/"frames").mkdir()
    Image.new("RGB",(200,100),"#ff0000").save(tmp_path/"frames/shot.png")
    with Image.open(BytesIO(render_png("# Note\n\n![External](https://example.com/shot.png)",tmp_path))) as output:
        assert all(color!=(255,0,0) for _,color in output.getcolors(output.width*output.height))
