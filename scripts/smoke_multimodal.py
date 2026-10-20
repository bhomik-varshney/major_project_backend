"""Optional live check; sends only a built-in synthetic page and no uploaded documents."""
import argparse
from io import BytesIO
import json
from tempfile import TemporaryDirectory
from pathlib import Path
from app.core.config import Settings
from app.orchestration.vision import analyse_pages
from app.providers.groq import GroqKeyManager,GroqProvider,ProviderUnavailable


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');args=parser.parse_args()
    if not args.live:
        parser.error('Use --live to allow this built-in synthetic page to be sent to Groq.')
    from PIL import Image,ImageDraw
    settings=Settings();manager=GroqKeyManager(settings.groq_keys)
    models=GroqProvider(manager,settings).available_models()
    print(json.dumps({'model_availability':{m:m in models for m in (settings.text_model,settings.groq_vision_model_primary,settings.groq_vision_model_fallback) if m}}),flush=True)
    image=Image.new('RGB',(700,900),'white');draw=ImageDraw.Draw(image)
    for i,text in enumerate(['Synthetic Student','Introduction','Engineering student building useful services.','Skills','Python, SQL','Projects','Campus Portal','Technologies: Python, FastAPI']):
        draw.text((50,50+i*55),text,fill='black',font_size=23)
    png=BytesIO();image.save(png,format='PNG');image.close()
    with TemporaryDirectory(prefix='synthetic-vision-') as scratch:
        settings.document_storage_path=Path(scratch)
        try:
            result=analyse_pages([{'page':1,'width':700,'height':900,'content':png.getvalue()}],settings,manager)
            print(json.dumps({'vision':'completed','model':result.model,'layout_blocks':len(result.layout.blocks),'warnings':result.warnings}))
        except ProviderUnavailable as exc:
            print(json.dumps({'vision':'fallback','code':exc.code,'message':exc.message}))


if __name__=='__main__':main()
