"""OCR uses temporary images and fake CDN responses, never platform accounts."""

import asyncio
import copy
import json
import os
from pathlib import Path

import httpx
import pytest

from api.services import research_ocr as module
from api.services.platform_materials import MaterialCollector, component, material


def source():
    return {'platform': 'xhs', 'content_id': 'one', 'title': '图文', 'content_type': 'note',
            'url': 'https://www.xiaohongshu.com/explore/one'}


def detail(count=3):
    return {'note_id': 'one', 'type': 'normal', 'desc': '帖子正文', 'image_list': [
        {'file_id': str(i), 'url_default': f'https://sns-webpic-qc.xhscdn.com/{i}?token=private'} for i in range(count)]}


@pytest.mark.asyncio
async def test_image_order_partial_retry_and_cache_without_private_urls(monkeypatch, tmp_path):
    service = module.OcrService(tmp_path / 'cache', tmp_path / 'temporary')
    downloads, calls = [], []
    failing = True
    async def download(url, target):
        downloads.append(url)
        if '/1?' in url and failing:
            raise RuntimeError('private URL and credential')
        target.write_bytes(url.encode())
    async def recognize(path=None, probe=False):
        if probe:
            return {'state': 'ok'}
        calls.append(path.read_bytes())
        return {'state': 'ok', 'text': '第零张核心文字' if b'/0?' in path.read_bytes() else '', 'language': 'zh-Hans-CN'}
    monkeypatch.setattr(module, 'download_image', download)
    monkeypatch.setattr(module, 'run_ocr', recognize)
    events = []
    result = await service.collect(source(), detail(), publish=lambda value, message: events.append(copy.deepcopy(value)))
    assert result['state'] == 'failed' and result['truncated']
    assert [(r['image'], r['state']) for r in result['entries']] == [(1, 'ok'), (2, 'failed'), (3, 'empty')]
    assert result['metadata']['recognized'] == 1 and result['metadata']['failed'] == 1
    assert any(r['entries'] and r['entries'][0]['text'] for r in events)
    assert not list((tmp_path / 'temporary').glob('ocr-*'))
    stored = ''.join(p.read_text(encoding='utf-8') for p in (tmp_path / 'cache').glob('*.json'))
    assert 'private' not in stored and 'http' not in stored and 'private' not in json.dumps(result)
    failing = False
    second = await service.collect(source(), detail(), result)
    assert second['state'] == 'ok' and not second['truncated']
    assert len(downloads) == 4 and len(calls) == 3 and second['metadata']['cached_images'] == 2
    service = module.OcrService(tmp_path / 'cache', tmp_path / 'temporary')
    third = await service.collect(source(), detail())
    assert third['metadata']['cached_images'] == 3 and len(downloads) == 4
    assert service.status is None, 'Cached text must work even if the engine is unavailable later'


@pytest.mark.asyncio
async def test_missing_language_unavailable_image_and_foreign_note(monkeypatch, tmp_path):
    service = module.OcrService(tmp_path)
    async def unavailable(*args, **kwargs):
        return {'state': 'failed', 'code': 'language'}
    monkeypatch.setattr(module, 'run_ocr', unavailable)
    result = await service.collect(source(), detail())
    assert result['state'] == 'failed' and '中文' in result['reason']
    assert result['metadata']['processed'] == 1 and result['metadata']['total'] == 3
    assert 'OCR' in result['reason']
    assert (await service.collect(source(), {**detail(), 'note_id': 'other'}))['state'] == 'failed'
    assert (await service.collect(source(), {**detail(), 'image_list': []}))['state'] == 'missing'
    assert (await service.collect(source(), {**detail(), 'type': 'video'}))['state'] == 'not_applicable'
    result = await service.collect(source(), {**detail(), 'image_list': [{}]})
    assert result['entries'][0]['state'] == 'failed' and result['metadata']['processed'] == 1


@pytest.mark.asyncio
async def test_bounded_images_cache_expiration_and_cancellation(monkeypatch, tmp_path):
    service = module.OcrService(tmp_path / 'cache', tmp_path / 'temporary')
    monkeypatch.setattr(module, 'MAX_IMAGES', 1)
    async def download(url, target): target.write_bytes(b'image')
    async def recognize(path=None, probe=False): return {'state': 'ok', 'text': '图片文字'}
    monkeypatch.setattr(module, 'download_image', download)
    monkeypatch.setattr(module, 'run_ocr', recognize)
    result = await service.collect(source(), detail())
    assert result['metadata']['processed'] == 1 and result['truncated'] and '前 1 张' in result['reason']
    path = next((tmp_path / 'cache').glob('*.json'))
    os.utime(path, (0, 0))
    assert service.cached(path.stem) is None
    path.write_text('broken', encoding='utf-8')
    assert service.cached(path.stem) is None
    async def cancel(*args, **kwargs): raise asyncio.CancelledError()
    monkeypatch.setattr(module, 'download_image', cancel)
    with pytest.raises(asyncio.CancelledError):
        await service.collect(source(), detail())
    assert not list((tmp_path / 'temporary').glob('ocr-*'))


@pytest.mark.asyncio
async def test_timeout_keeps_completed_images(monkeypatch, tmp_path):
    service = module.OcrService(tmp_path)
    monkeypatch.setattr(module, 'OCR_SECONDS', 0.05)
    async def download(url, target):
        if '/1?' in url:
            await asyncio.sleep(1)
        target.write_bytes(b'image')
    async def recognize(path=None, probe=False): return {'state': 'ok', 'text': '已读的第一张图'}
    monkeypatch.setattr(module, 'download_image', download)
    monkeypatch.setattr(module, 'run_ocr', recognize)
    result = await service.collect(source(), detail())
    assert result['entries'][0]['text'] == '已读的第一张图'
    assert result['metadata']['processed'] == 1 and result['truncated'] and '超时' in result['reason']


@pytest.mark.asyncio
async def test_download_checks_redirect_dns_and_has_no_credentials(monkeypatch, tmp_path):
    checked, requests = [], []
    async def target(url):
        checked.append(url)
        if 'private' in url:
            raise ValueError('private network')
        return 'https://8.8.8.8/image', 'cdn.example'
    def handler(request):
        requests.append(request)
        assert 'cookie' not in request.headers and 'authorization' not in request.headers
        return httpx.Response(302, headers={'location': 'https://private.example/image'})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(module, 'public_target', target)
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kwargs: client)
    with pytest.raises(ValueError, match='private network'):
        await module.download_image('https://cdn.example/image', tmp_path / 'image')
    assert len(checked) == 2 and len(requests) == 1


@pytest.mark.asyncio
async def test_download_rejects_large_or_empty_images(monkeypatch, tmp_path):
    async def target(url): return url, 'cdn.example'
    monkeypatch.setattr(module, 'public_target', target)
    constructor = httpx.AsyncClient
    for headers, data in [({'content-length': str(module.MAX_IMAGE_BYTES + 1)}, b'x'), ({}, b'')]:
        client = constructor(transport=httpx.MockTransport(lambda _: httpx.Response(200, headers=headers, content=data)))
        monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kwargs: client)
        with pytest.raises(ValueError):
            await module.download_image('https://cdn.example/image', tmp_path / 'image')


@pytest.mark.asyncio
async def test_collector_publishes_body_before_ocr_and_keeps_failures_separate(monkeypatch, tmp_path):
    collector = MaterialCollector(workdir=tmp_path)
    class Xhs:
        async def get_note_by_id(self, *args): return detail()
    async def client(_): return Xhs()
    async def request(method, *args): return await method(*args)
    async def comments(*args): return component('ok', entries=[{'id': 'one', 'text': '评论'}])
    events = []
    collector.emit = lambda event: events.append(copy.deepcopy(event))
    monkeypatch.setattr(collector, 'client', client)
    monkeypatch.setattr(collector, 'request', request)
    monkeypatch.setattr(collector, 'comments', comments)
    async def recognize(source, data, previous, publish):
        assert events[-1]['material']['body']['text'] == '帖子正文'
        result = {**component('failed'), 'entries': [{'image': 1, 'text': '图片里的核心文字'}]}
        publish(result, '已处理 1/3 张图片')
        return result
    monkeypatch.setattr(collector.ocr, 'collect', recognize)
    result = await collector.collect({'key': 'xhs|one', 'result': source()})
    assert result['body']['state'] == 'ok' and result['comments']['state'] == 'ok'
    assert result['ocr']['entries'][0]['text'] == '图片里的核心文字'
    assert any(row.get('checkpoint') for row in events)
    assert material({'key': 'xhs|one', 'result': {**source(), 'content_type': 'video'}})['ocr']['state'] == 'not_applicable'
    await collector.close()


@pytest.mark.skipif(os.name != 'nt', reason='Windows built-in OCR')
@pytest.mark.asyncio
async def test_native_chinese_ocr_and_long_image_tiles(tmp_path):
    from PIL import Image, ImageDraw, ImageFont
    status = await module.run_ocr(probe=True)
    if status.get('code') == 'language':
        pytest.skip('Windows Chinese OCR language is not installed')
    assert status['state'] == 'ok', status
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 55)
    path = tmp_path / 'long-chinese.png'
    image = Image.new('RGB', (1200, 14000), 'white')
    draw = ImageDraw.Draw(image)
    draw.text((50, 100), '小红书图片核心文字', fill='black', font=font)
    draw.text((50, 2360), '长图拼接处也要读取', fill='black', font=font)
    draw.text((50, 13700), '最后一张旅行清单', fill='black', font=font)
    image.save(path)
    result = await module.run_ocr(path)
    assert result['state'] == 'ok', result
    text = result['text'].replace(' ', '')
    assert '小红书图片核心文字' in text and '最后一张旅行清单' in text
    assert text.count('长图拼接处也要读取') == 1
    assert 'research_ocr_worker.ps1' in (Path(__file__).parents[1] / 'MediaCrawler.spec').read_text(encoding='utf-8')


@pytest.mark.skipif(os.name != 'nt', reason='Windows built-in OCR')
@pytest.mark.asyncio
@pytest.mark.parametrize('format', ['JPEG', 'WEBP', 'PNG'])
async def test_native_common_image_formats_and_transparency(tmp_path, format):
    from PIL import Image, ImageDraw, ImageFont
    path = tmp_path / 'image.bin'
    image = Image.new('RGBA' if format == 'PNG' else 'RGB', (1200, 500), (255, 255, 255, 0) if format == 'PNG' else 'white')
    draw = ImageDraw.Draw(image)
    draw.text((50, 100), '图片文字测试', fill='black', font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 55))
    image.save(path, format=format)
    result = await module.run_ocr(path)
    if result.get('code') == 'language':
        pytest.skip('Windows Chinese OCR language is not installed')
    assert result['state'] == 'ok', result
    assert '图片文字测试' in result['text'].replace(' ', '')
