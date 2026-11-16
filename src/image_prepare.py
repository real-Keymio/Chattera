"""Bounded image derivative worker; original uploads are never changed."""
import base64, hashlib, io, json, sys
from pathlib import Path

def prepare(path, policy):
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS=40_000_000
    raw=path.read_bytes()
    with Image.open(path) as source:
        if source.width*source.height>40_000_000:raise ValueError('Image exceeds 40 MP')
        original=list(source.size)
        im=ImageOps.exif_transpose(source); im.load()
        scale=min(1,policy['max_edge']/max(im.size),(policy['max_pixels']/(im.width*im.height))**.5)
        if min(im.size)>=320 and min(im.size)*scale<256:raise ValueError('Long image requires cropping into readable sections')
        if scale<1:im=im.resize((max(1,int(im.width*scale)),max(1,int(im.height*scale))),Image.Resampling.LANCZOS)
        fmt=source.format;data=raw
        if scale<1 or len(data)>policy['max_file_bytes'] or source.getexif().get(274,1)!=1:
            out=io.BytesIO();im.save(out,format='PNG',optimize=True);data=out.getvalue();fmt='PNG'
            if len(data)>policy['max_file_bytes']:
                if im.mode!='RGB':
                    bg=Image.new('RGB',im.size,'white');rgba=im.convert('RGBA');bg.paste(rgba,mask=rgba.getchannel('A'));im=bg
                for quality in (92,85,75):
                    out=io.BytesIO();im.save(out,format='JPEG',quality=quality,optimize=True);data=out.getvalue();fmt='JPEG'
                    if len(data)<=policy['max_file_bytes']:break
        if len(data)>policy['max_file_bytes']:raise ValueError('Compressed image exceeds transfer limit; crop image')
        return dict(original_size=original,prepared_size=list(im.size),format=fmt,
            media_type=Image.MIME[fmt],image_sha256=hashlib.sha256(data).hexdigest(),
            encoded_bytes=len(data),base64_bytes=4*((len(data)+2)//3),
            transform='unchanged' if data==raw else 'aspect-preserving derivative',encoded=base64.b64encode(data).decode())

if __name__=='__main__':
    if sys.argv[2]:sys.path.insert(0,sys.argv[2])
    try:print(json.dumps(prepare(Path(sys.argv[1]),json.loads(sys.argv[3]))))
    except Exception as exc:
        known=('Image exceeds 40 MP','Long image requires cropping into readable sections','Compressed image exceeds transfer limit; crop image')
        print(str(exc) if str(exc) in known else 'Image preparation failed or exceeds limits',file=sys.stderr);sys.exit(1)
