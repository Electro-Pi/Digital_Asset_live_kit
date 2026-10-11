"""Generate baseline/style pairs through the production TTS plugin."""
import argparse
import asyncio
import hashlib
import html
import json
import re
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / '.env.local')
import os
import aiohttp
from loguru import logger
from src.plugins.voxcpm_tts_plugin import VoxCPMTTSPlugin
from src.catalog import clean_for_speech
from src.speech_streaming import ConcatPhraseTokenizer

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--style', default='Warm, friendly conversational delivery, relaxed natural pace, varied intonation, gentle emphasis on meaningful words')
parser.add_argument('--punctuation', action='store_true', help='Compare Arabic phrasing without style instructions')
parser.add_argument('--chunks', action='store_true', help='Compare whole-reply PCM against joined streaming PCM phrases')
ARGS = parser.parse_args()
STYLE = ARGS.style
SAMPLES = [
    ('greeting', 'هَلا واللَّه، مَعَك سَلْمَى مِن لُقْمَهْ. وِش تِشْتَهي اليَوْم؟'),
    ('recommendation', 'يا سَلام، عِنْدَنا دِجاج مَشْوِي طَعْمُه يِفَوِّز، ومَعَه رِز وصُوص. وِش رايَك تِجَرِّبُه؟'),
    ('confirmation', 'أَبْشِر، كِذا طَلَبَك جاهِز. تِبِي نِزَوِّد لَك شَي بارِد مَعَه؟'),
]

def audition_text(text, variant):
    # Apply experiments after production cleanup, so it cannot silently undo
    # punctuation or reinsert the vowels being compared.
    text = clean_for_speech(text)
    if variant in ('connected', 'sentences', 'light_vowels'):
        text = re.sub(r'[؟?]\s*(?:\.{2,}|…)+', '؟', text)
        text = re.sub(r'(?:\.{2,}|…)+\s*$', '.', text)
        text = re.sub(r'\.{2,}|…+', '،', text)
    if variant in ('connected', 'light_vowels'):
        text = re.sub(r'(?<!\d)\.(?=\s+\S)', '،', text)
        text = text.replace('!', '،')
    if variant == 'sentences':
        text = text.replace('،', '.')
    if variant == 'light_vowels':
        # Keep shadda (consonant doubling), hamza, and every base letter.
        text = re.sub('[\u064b-\u0650\u0652]', '', text)
    return text

async def main():
    logger.remove()
    logger.add(sys.stderr, level='ERROR')
    out = ROOT / 'data' / 'tts-auditions' / datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    out.mkdir(parents=True)
    results = []
    variants = (
        [('baseline', 'A — Current Arabic punctuation'),
         ('connected', 'B — Connected phrases with commas'),
         ('sentences', 'C — Short phrases with full stops'),
         ('light_vowels', 'D — Connected phrases, lighter vowels')]
        if ARGS.punctuation else
        [('baseline', 'A — Current delivery'), ('expressive', 'B — With style guidance')]
    )
    if ARGS.chunks:
        variants = [('baseline', 'A — Whole reply, streaming PCM'), ('chunks', 'B — Joined phrases, streaming PCM')]
    async with aiohttp.ClientSession() as session:
        for name, text in SAMPLES:
            for variant, label in variants:
                style = STYLE if variant == 'expressive' else None
                plugin = VoxCPMTTSPlugin(
                    server_url=os.environ['TTS_SERVER_URL'],
                    api_key=os.getenv('TTS_API_KEY'),
                    model=os.getenv('TTS_MODEL', 'VoxCPM2'),
                    voice=os.getenv('TTS_VOICE', 'nouf'),
                    ref_audio=os.getenv('TTS_REF_AUDIO') or None,
                    ref_text=os.getenv('TTS_REF_TEXT') or None,
                    seed=int(os.getenv('TTS_SEED', '1234')),
                    speed=float(os.getenv('TTS_SPEED', '1.0')),
                    gain_db=float(os.getenv('TTS_GAIN_DB', '4.0')),
                    style=style,
                    phrase_streaming=variant == 'chunks',
                    text_filter=lambda value, variant=variant: audition_text(value, variant),
                    http_session=session,
                )
                frames = []
                phrases = ConcatPhraseTokenizer().tokenize(text) if variant == 'chunks' else [text]
                for phrase in phrases:
                    async with plugin.synthesize(phrase) as stream:
                        async for event in stream:
                            frames.append(bytes(event.frame.data))
                pcm = b''.join(frames)
                if not pcm:
                    raise RuntimeError(f'No audio for {name}/{variant}')
                filename = f'{name}-{variant}.wav'
                with wave.open(str(out / filename), 'wb') as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(48000)
                    wav.writeframes(pcm)
                results.append(dict(name=name, variant=variant, text=text,
                                    synthesis_text=audition_text(text, variant), style=style,
                                    phrases=phrases,
                                    file=filename, seconds=round(len(pcm)/96000, 2),
                                    sha256=hashlib.sha256(pcm).hexdigest()))
                print(f'{filename}: {len(pcm)/96000:.2f}s', flush=True)
    manifest = dict(voice=os.getenv('TTS_VOICE', 'nouf'), seed=int(os.getenv('TTS_SEED', '1234')), samples=results)
    (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    sections = []
    for name, text in SAMPLES:
        players = ''.join(f'<div><b>{label}</b><p dir="rtl" lang="ar">{html.escape(audition_text(text, variant))}</p><audio controls preload="metadata" src="{name}-{variant}.wav"></audio></div>'
                          for variant, label in variants)
        sections.append(f'<section><h2>{name.title()}</h2><p dir="rtl" lang="ar">{html.escape(text)}</p><div class="pair">{players}</div></section>')
    (out / 'index.html').write_text('''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>VoxCPM2 listening comparison</title><style>body{font:17px system-ui;background:#f4f6f8;color:#182334;max-width:900px;margin:40px auto;padding:20px}section{background:white;padding:24px;border-radius:16px;margin:20px 0}p[lang=ar]{font-size:25px}.pair{display:flex;gap:25px;flex-wrap:wrap}audio{display:block;margin-top:12px;max-width:100%}h1{font-size:28px}</style><h1>VoxCPM2 listening comparison</h1><p>Same voice, words, seed, speed and volume. Only delivery guidance changes.</p><p>Listen for natural phrasing, expression, pronunciation and voice consistency. If B reads the English instruction aloud, report that too.</p>''' + ''.join(sections) + '</html>')
    print(f'LISTEN: {out / "index.html"}', flush=True)
    if ARGS.punctuation:
        page = out / 'index.html'
        page.write_text(page.read_text().replace(
            'Same voice, words, seed, speed and volume. Only delivery guidance changes.',
            'Same voice, words, seed, speed and volume. No style instructions. Compare punctuation; D also removes short-vowel marks.'
        ).replace(
            'Listen for natural phrasing, expression, pronunciation and voice consistency. If B reads the English instruction aloud, report that too.',
            'Compare connected phrasing, pauses, pronunciation and voice consistency. Plausible duration does not guarantee correct or natural speech.'
        ))
    if ARGS.chunks:
        page = out / 'index.html'
        page.write_text(page.read_text().replace(
            'Same voice, words, seed, speed and volume. Only delivery guidance changes.',
            'Both versions request streaming PCM with fixed output gain. B splits at phrase boundaries, smooths edges and adds punctuation pauses. WAV files here are playback exports only.'
        ).replace(
            'Listen for natural phrasing, expression, pronunciation and voice consistency. If B reads the English instruction aloud, report that too.',
            'Listen for natural joins, preserved words and stable voice. Neither version uses a style instruction.'
        ))

if __name__ == '__main__':
    asyncio.run(main())
