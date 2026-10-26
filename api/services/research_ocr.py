"""Bounded local OCR for the image pages of selected Xiaohongshu notes."""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from base.runtime_paths import application_root, library_data_root, resource_path
from .public_network import public_target
from .research_transcription import worker_environment
from .worker_process import terminate_worker, windows_process_job

MAX_IMAGES = 60
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_TEXT = 24000
OCR_SECONDS = 90
CACHE_SECONDS = 30 * 86400
REASONS = {
    'engine': 'Windows 本地 OCR 不可用，需要 Windows 10/11',
    'language': '未安装中文 OCR，请在 Windows 语言设置中安装中文的光学字符识别组件',
    'decode': '图片格式无法解码或图片已损坏',
    'too_large': '图片超过本地 OCR 的像素上限',
    'text_limit': '单张图片文字超过 24000 字上限，请在原图中复核',
    'recognition': '本地 OCR 识别失败，可重试',
    'download': '图片下载失败、超过 8 MB 或平台限制公开访问',
    'timeout': '图片文字识别超时，已取得的内容保留，可重试',
}


def ocr_environment():
    env = worker_environment()
    env.update({key: value for key, value in os.environ.items() if key.upper() in {
        'SYSTEMDRIVE', 'HOMEDRIVE', 'HOMEPATH', 'PROGRAMDATA', 'ALLUSERSPROFILE', 'PROGRAMFILES', 'PROGRAMFILES(X86)'}})
    return env


@contextmanager
def system_dll_search():
    """System PowerShell must not inherit PyInstaller's private DLL directory."""
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        yield
        return
    import ctypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    previous = ctypes.create_unicode_buffer(32768)
    kernel.GetDllDirectoryW(len(previous), previous)
    kernel.SetDllDirectoryW(None)
    try:
        yield
    finally:
        kernel.SetDllDirectoryW(previous.value or None)


def component(state='missing', reason=''):
    return {'state': state, 'text': '', 'entries': [], 'reason': reason, 'truncated': False}


async def download_image(url, target):
    async with httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False) as client:
        async with asyncio.timeout(20):
            for _ in range(6):
                pinned, host = await public_target(url)
                async with client.stream('GET', pinned, headers={'Host': urlsplit(url).netloc,
                        'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.xiaohongshu.com/'},
                        extensions={'sni_hostname': host}) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get('location')
                        if not location:
                            raise ValueError('missing redirect')
                        url = urljoin(url, location)
                        continue
                    response.raise_for_status()
                    if int(response.headers.get('content-length', '0')) > MAX_IMAGE_BYTES:
                        raise ValueError('image size')
                    size = 0
                    with target.open('wb') as output:
                        async for chunk in response.aiter_bytes(65536):
                            size += len(chunk)
                            if size > MAX_IMAGE_BYTES:
                                raise ValueError('image size')
                            output.write(chunk)
                    if not size:
                        raise ValueError('empty image')
                    return
    raise ValueError('too many redirects')


async def run_native_ocr(path=None, probe=False):
    if os.name != 'nt':
        return {'state': 'failed', 'code': 'engine'}
    executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    command = [str(executable), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
               '-File', str(resource_path('scripts', 'research_ocr_worker.ps1'))]
    if probe:
        command.append('-Probe')
    proc, handle = None, None
    try:
        env = ocr_environment()
        if getattr(sys, 'frozen', False):
            bundle = str(resource_path()).casefold().rstrip('\\/')
            env['PATH'] = os.pathsep.join(entry for entry in env.get('PATH', '').split(os.pathsep)
                if entry.casefold().rstrip('\\/') != bundle and not entry.casefold().startswith(bundle + os.sep))
        with system_dll_search():
            proc = await asyncio.create_subprocess_exec(*command, env=env,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW)
        handle = windows_process_job(proc)
        async with asyncio.timeout(20):
            output, _ = await proc.communicate((json.dumps({'path': str(Path(path).resolve())} if path else {}) + '\n').encode('utf-8'))
        if len(output) > 256000:
            raise ValueError('worker size')
        result = json.loads(output.decode('utf-8-sig'))
        if not isinstance(result, dict) or result.get('state') not in {'ok', 'failed'}:
            raise ValueError('worker result')
        if result['state'] == 'ok' and (proc.returncode or not isinstance(result.get('text', ''), str) or len(result.get('text', '')) > MAX_IMAGE_TEXT):
            raise ValueError('worker text')
        return result
    except TimeoutError:
        return {'state': 'failed', 'code': 'timeout'}
    except (OSError, ValueError):
        return {'state': 'failed', 'code': 'engine'}
    finally:
        if handle is not None:
            handle.Close()
        await terminate_worker(proc)


async def recognize_image(path):
    """Decode existing Pillow formats in the isolated worker, then call Windows OCR."""
    from PIL import Image, ImageOps
    converted = Path(path).with_name(Path(path).name + '.ocr.png')
    try:
        with Image.open(path) as image:
            width, height = image.size
            if width * height > 40000000 or width > 40000 or height > 40000:
                return {'state': 'failed', 'code': 'too_large'}
            image = ImageOps.exif_transpose(image)
            if image.width > 10000:
                return {'state': 'failed', 'code': 'too_large'}
            if image.mode in {'RGBA', 'LA'} or 'transparency' in image.info:
                rgba = image.convert('RGBA')
                background = Image.new('RGB', rgba.size, 'white')
                background.paste(rgba, mask=rgba.getchannel('A'))
                background.save(converted)
            else:
                image.convert('RGB').save(converted)
    except Exception:
        return {'state': 'failed', 'code': 'decode'}
    return await run_native_ocr(converted)


async def run_ocr(path=None, probe=False):
    if probe or os.name != 'nt':
        return await run_native_ocr(probe=True)
    command = ([sys.executable, '--research-worker'] if getattr(sys, 'frozen', False) else
               [sys.executable, '-m', 'api.services.research_worker'])
    proc, handle = None, None
    try:
        proc = await asyncio.create_subprocess_exec(*command, cwd=application_root(), env=ocr_environment(),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        handle = windows_process_job(proc)
        async with asyncio.timeout(25):
            output, _ = await proc.communicate((json.dumps({'mode': 'ocr', 'path': str(Path(path).resolve())}) + '\n').encode())
        if len(output) > 256000:
            raise ValueError('worker size')
        event = json.loads(output)
        if proc.returncode or event.get('type') != 'done' or not isinstance(event.get('result'), dict):
            raise ValueError('worker result')
        return event['result']
    except TimeoutError:
        return {'state': 'failed', 'code': 'timeout'}
    except (OSError, ValueError):
        return {'state': 'failed', 'code': 'engine'}
    finally:
        if handle is not None:
            handle.Close()
        await terminate_worker(proc)


class OcrService:
    def __init__(self, root=None, temporary_root=None):
        self.root = Path(root) if root is not None else library_data_root() / 'research-ocr'
        self.temporary_root = Path(temporary_root) if temporary_root else self.root
        self.status = None

    def cached(self, identity):
        path = self.root / (identity + '.json')
        try:
            if time.time() - path.stat().st_mtime > CACHE_SECONDS or path.stat().st_size > 128000:
                return None
            value = json.loads(path.read_text(encoding='utf-8'))
            if value.get('version') != 1 or not isinstance(value.get('text'), str) or len(value['text']) > MAX_IMAGE_TEXT:
                return None
            return {'text': value['text'], 'language': str(value.get('language') or '')[:24]}
        except (OSError, ValueError, TypeError, AttributeError):
            return None

    def save(self, identity, value):
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / (identity + '.json')
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'version': 1, 'text': value['text'], 'language': value.get('language', '')}, ensure_ascii=False), encoding='utf-8')
            temporary.replace(path)
            used = 0
            for index, old in enumerate(sorted(self.root.glob('*.json'), key=lambda row: row.stat().st_mtime, reverse=True)):
                used += old.stat().st_size
                if index >= 512 or used > 32 * 1024 * 1024 or time.time() - old.stat().st_mtime > CACHE_SECONDS:
                    old.unlink(missing_ok=True)
        except OSError:
            pass

    async def collect(self, source, detail, previous=None, publish=lambda value, message: None):
        from .reading_platforms import first_url
        if not isinstance(detail, dict) or str(detail.get('note_id') or detail.get('id') or source['content_id']) != str(source['content_id']):
            value = previous or component()
            return {**value, 'state': 'failed', 'reason': '未取得对应原帖的配图，已有图片文字保留', 'truncated': bool(value['entries'])}
        if detail.get('type') == 'video':
            return component('not_applicable')
        images = detail.get('image_list')
        if not isinstance(images, list) or not images:
            return component('missing', '原帖未返回配图，不能判断图片是否有文字')
        rows, cached_count = [], 0
        retained = {row.get('id'): row for row in (previous or {}).get('entries', []) if row.get('state') in {'ok', 'empty'}}
        current = component()

        def snapshot(reason=''):
            failed = sum(row['state'] == 'failed' for row in rows)
            empty = sum(row['state'] == 'empty' for row in rows)
            remaining = len(images) - len(rows)
            return {**component('failed' if failed or remaining else 'ok', reason), 'entries': list(rows),
                'truncated': bool(failed or remaining), 'metadata': {'engine': 'windows-ocr', 'total': len(images),
                    'processed': len(rows), 'recognized': len(rows) - failed - empty, 'empty': empty, 'failed': failed,
                    'cached_images': cached_count, 'failures': [
                        {'image': row['image'], 'reason': row.get('reason', REASONS['recognition'])} for row in rows if row['state'] == 'failed']}}

        try:
            async with asyncio.timeout(OCR_SECONDS):
                for index, image in enumerate(images[:MAX_IMAGES], 1):
                    url = first_url('xhs', image)
                    identity = hashlib.sha256(json.dumps([1, source['content_id'], image.get('file_id') if isinstance(image, dict) else '',
                        urlsplit(url)._replace(query='', fragment='').geturl() if url else '', index]).encode()).hexdigest()
                    saved = retained.get(identity) or self.cached(identity)
                    row = {'id': identity, 'image': index, 'text': '', 'state': 'failed'}
                    if saved is not None:
                        row.update(text=saved['text'], state='ok' if saved['text'].strip() else 'empty')
                        cached_count += 1
                    elif url:
                        if self.status is None:
                            self.status = await run_ocr(probe=True)
                        if self.status['state'] != 'ok':
                            rows.append({**row, 'reason': REASONS.get(self.status.get('code'), REASONS['engine'])})
                            current = snapshot(rows[-1]['reason'])
                            publish(current, '本地图片文字识别不可用')
                            return current
                        self.temporary_root.mkdir(parents=True, exist_ok=True)
                        with tempfile.TemporaryDirectory(prefix='ocr-', dir=self.temporary_root) as temp:
                            path = Path(temp) / 'image.bin'
                            publish(snapshot(), f'正在识别原帖第 {index}/{len(images)} 张图片文字')
                            try:
                                await download_image(url, path)
                            except Exception:
                                result = {'state': 'failed', 'code': 'download'}
                            else:
                                result = await run_ocr(path)
                        if result['state'] == 'ok':
                            row.update(text=result['text'], state='ok' if result['text'].strip() else 'empty')
                            self.save(identity, result)
                        else:
                            row['reason'] = REASONS.get(result.get('code'), REASONS['recognition'])
                    else:
                        row['reason'] = '原帖未返回可读取的图片地址'
                    rows.append(row)
                    current = snapshot()
                    publish(current, f'已处理 {index}/{len(images)} 张图片文字')
        except TimeoutError:
            current = snapshot(REASONS['timeout'])
        else:
            reason = f'已处理 {len(rows)}/{len(images)} 张图片'
            if len(images) > MAX_IMAGES:
                reason += f'；最多识别前 {MAX_IMAGES} 张'
            if current.get('metadata', {}).get('failed'):
                reason += '；部分图片失败，可重试，已有文字保留'
            elif not any(row['text'].strip() for row in rows):
                reason += '；未识别到文字，不代表图片没有信息'
            current = snapshot(reason)
        return current
