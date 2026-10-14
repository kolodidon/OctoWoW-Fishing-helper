"""Personal, repeatable installation into a selected Vanilla WoW client.

Never launches the game, edits account data, or loads a DLL into this process.
"""
from pathlib import Path, PurePosixPath
from datetime import datetime
import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
import struct
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

PACKAGE = Path(__file__).resolve().parent
OWNER = 'FishingBot-personal-installer-v1'
SUPERWOW_URL = 'https://github.com/balakethelock/SuperWoW/releases/download/Release/SuperWoW.release.2.2.zip'
SUPERWOW_SHA256 = 'e1b37caf5496037fc369b8b68b5ef529be5fa951543869328bf38bef83dfa4a8'
SUPERAPI_COMMIT = '901322dc88890a2ea10610b8228fb43c9c2a3610'
SUPERAPI_URL = 'https://codeload.github.com/balakethelock/SuperAPI/zip/' + SUPERAPI_COMMIT
SUPERAPI_SHA256 = '9a719a1cf57a8de32b336b14c27ab165f741083ff1e833fa76034e882df94377'
BOT_FILES = ('cursor_test.py', 'audio_detector.py', 'FishBite-reference.wav', 'requirements.txt')
PAYLOAD_PREFIXES = ('Interface/AddOns/SimpleAutoDelete/', 'Interface/AddOns/SuperAPI/', 'Sound/Spells/Tradeskills/')

def digest(data): return hashlib.sha256(data).hexdigest()

def ensure(condition, message):
    if not condition: raise RuntimeError(message)

def x86_pe(data):
    if len(data) < 64 or data[:2] != b'MZ': return False
    offset = struct.unpack_from('<I', data, 60)[0]
    return offset + 6 <= len(data) and data[offset:offset+4] == b'PE\0\0' and struct.unpack_from('<H', data, offset+4)[0] == 0x14c

def safe_target(root, relative):
    relative = Path(relative)
    ensure(not relative.is_absolute() and '..' not in relative.parts, 'Недопустимый путь: ' + str(relative))
    root = Path(root).absolute()
    target = root / relative
    current = target
    while current != root:
        ensure(not current.is_symlink() and not (hasattr(os.path, 'isjunction') and os.path.isjunction(current)), 'Ссылка вместо обычного пути: ' + str(current))
        # Python 3.11 does not expose isjunction; reject Windows reparse points.
        if current.exists():
            ensure(not getattr(current.stat(follow_symlinks=False), 'st_file_attributes', 0) & 0x400, 'Reparse point: ' + str(current))
        current = current.parent
    ensure(target.resolve().is_relative_to(root.resolve()), 'Путь выходит за пределы папки игры.')
    return target

def verify_package(package=PACKAGE):
    package = Path(package)
    manifest = package / 'SHA256SUMS.txt'
    ensure(manifest.is_file(), 'Нет SHA256SUMS.txt. Распакуйте весь комплект.')
    listed = set()
    for line in manifest.read_text(encoding='utf-8-sig').splitlines():
        if not line.strip(): continue
        expected, name = line.split(maxsplit=1)
        name = name.lstrip('*')
        path = safe_target(package, name)
        ensure(path.is_file() and digest(path.read_bytes()) == expected.lower(), 'Контрольная сумма не совпадает: ' + name)
        listed.add(name.replace('\\', '/'))
    needed = set(BOT_FILES) | {'addons-and-macros.zip', 'install.py', 'bootstrap.ps1', 'Install.cmd', 'installer/create-shortcuts.py', 'installer/FishingBotSetup.lua', 'installer/FishingBotSetup.toc', 'README-v2.md'}
    ensure(needed <= listed, 'В контрольных суммах отсутствуют обязательные файлы.')
    return len(listed)

def read_payload(package=PACKAGE):
    files = {}
    with zipfile.ZipFile(Path(package)/'addons-and-macros.zip') as archive:
        ensure(archive.testzip() is None, 'Поврежден архив аддонов.')
        for info in archive.infolist():
            if info.is_dir(): continue
            name = info.filename
            ensure('\\' not in name and not PurePosixPath(name).is_absolute() and '..' not in PurePosixPath(name).parts and ':' not in name, 'Недопустимый путь в ZIP.')
            ensure((info.external_attr >> 16) & 0o170000 != 0o120000, 'Символическая ссылка в ZIP.')
            if name.startswith(PAYLOAD_PREFIXES) or name.startswith('macros/') or name in ('SOURCES.md', 'INSTALL.md'):
                ensure(name not in files, 'Повторяющийся файл в ZIP: ' + name)
                files[name] = archive.read(info)
    for addon in ('SimpleAutoDelete',):
        toc = 'Interface/AddOns/{0}/{0}.toc'.format(addon)
        ensure(toc in files and b'11200' in files[toc], 'Не найден аддон Vanilla: ' + addon)
    sound = 'Sound/Spells/Tradeskills/FishBite.wav'
    ensure(files.get(sound) == (Path(package)/'FishBite-reference.wav').read_bytes(), 'Эталон звука отличается от игрового файла.')
    return files

def fetch_superapi():
    print('Скачиваю SuperAPI из официального репозитория...', flush=True)
    request = urllib.request.Request(SUPERAPI_URL, headers={'User-Agent':'FishingBot-personal-installer'})
    with urllib.request.urlopen(request, timeout=90) as response:
        data = response.read(8 * 1024 * 1024 + 1)
    ensure(len(data) <= 8 * 1024 * 1024 and digest(data) == SUPERAPI_SHA256, 'Контрольная сумма SuperAPI не совпадает. Клиент не изменён.')
    import io
    prefix = 'SuperAPI-' + SUPERAPI_COMMIT + '/'
    files = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        ensure(archive.testzip() is None, 'Поврежден архив SuperAPI.')
        for info in archive.infolist():
            name = info.filename
            ensure(name.startswith(prefix) and '..' not in PurePosixPath(name).parts and ':' not in name and '\\' not in name, 'Недопустимый путь SuperAPI.')
            ensure((info.external_attr >> 16) & 0o170000 != 0o120000, 'Ссылка в архиве SuperAPI.')
            if info.is_dir(): continue
            relative = name[len(prefix):]
            ensure(bool(relative) and not PurePosixPath(relative).is_absolute(), 'Недопустимый путь SuperAPI.')
            destination = 'Interface/AddOns/SuperAPI/' + relative
            ensure(destination not in files, 'Повтор файла SuperAPI.')
            files[destination] = archive.read(info)
    toc = files.get('Interface/AddOns/SuperAPI/SuperAPI.toc', b'')
    ensure(b'11200' in toc, 'Не найден SuperAPI для Vanilla.')
    return files

def running_game():
    result = subprocess.run(['tasklist.exe', '/FI', 'IMAGENAME eq WoW.exe', '/FO', 'CSV', '/NH'], capture_output=True, check=True)
    return b'"wow.exe"' in result.stdout.lower()

def game_version(path):
    lib = ctypes.WinDLL('version', use_last_error=True)
    lib.GetFileVersionInfoSizeW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
    lib.GetFileVersionInfoSizeW.restype = ctypes.c_uint32
    lib.GetFileVersionInfoW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    lib.GetFileVersionInfoW.restype = ctypes.c_int
    lib.VerQueryValueW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32)]
    lib.VerQueryValueW.restype = ctypes.c_int
    unused = ctypes.c_uint32()
    size = lib.GetFileVersionInfoSizeW(str(path), ctypes.byref(unused))
    ensure(size > 0, 'Не удалось прочитать версию WoW.exe. Нужен подтверждённый клиент 1.12.1.')
    data = ctypes.create_string_buffer(size)
    ensure(lib.GetFileVersionInfoW(str(path),0,size,data), 'Ошибка чтения версии WoW.exe.')
    pointer, length = ctypes.c_void_p(), ctypes.c_uint32()
    ensure(lib.VerQueryValueW(data,'\\',ctypes.byref(pointer),ctypes.byref(length)) and length.value >= 16, 'Ошибка ресурса версии WoW.exe.')
    fixed = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32))
    ensure(fixed[0] == 0xfeef04bd, 'Неожиданный ресурс версии WoW.exe.')
    return fixed[2] >> 16, fixed[2] & 65535, fixed[3] >> 16, fixed[3] & 65535

def parse_dlls(data):
    if data.startswith((b'\xff\xfe', b'\xfe\xff')): return data.decode('utf-16')
    return data.decode('utf-8-sig')

def loader_plan(game):
    game = Path(game)
    path = safe_target(game, 'dlls.txt')
    text = parse_dlls(path.read_bytes()) if path.is_file() else ''
    entries = [s.strip() for s in text.splitlines() if s.strip() and not s.strip().startswith(('#', ';'))]
    hook_entries = [s for s in entries if s.replace('\\','/').split('/')[-1].lower() == 'superwowhook.dll']
    ensure(len(hook_entries) <= 1, 'В dlls.txt несколько записей SuperWoW. Сначала устраните дубли.')
    if (game/'VanillaFixes.exe').is_file():
        hook = hook_entries[0].replace('\\', '/') if hook_entries else 'SuperWoWhook.dll'
        safe_target(game, hook)
        new_text = text if hook_entries else text.rstrip('\r\n') + ('\r\n' if text else '') + 'SuperWoWhook.dll\r\n'
        # Normalize a BOM/UTF-16 loader list without dropping any existing entries.
        return {'launcher':'VanillaFixes.exe', 'hook':hook, 'dlls':new_text.encode('utf-8')}
    others = [s for s in entries if s not in hook_entries]
    ensure(not others, 'В клиенте есть список других DLL, но нет VanillaFixes.exe. Автоматически менять загрузчик такой сборки нельзя. Используйте ее совместимый загрузчик или установите VanillaFixes отдельно.')
    return {'launcher':'SuperWoWlauncher.exe', 'hook':'SuperWoWhook.dll', 'dlls':None}

def fetch_superwow():
    print('Скачиваю SuperWoW 2.2 из официального релиза... ', flush=True)
    request = urllib.request.Request(SUPERWOW_URL, headers={'User-Agent':'FishingBot-personal-installer'})
    with urllib.request.urlopen(request, timeout=90) as response:
        data = response.read(8 * 1024 * 1024 + 1)
    ensure(len(data) <= 8 * 1024 * 1024 and digest(data) == SUPERWOW_SHA256, 'Контрольная сумма SuperWoW не совпадает. Файлы клиента не изменены.')
    import io
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        result = {name:archive.read(name) for name in ('SuperWoWhook.dll', 'SuperWoWlauncher.exe')}
    ensure(all(x86_pe(v) for v in result.values()), 'Неожиданный формат SuperWoW.')
    return result

class Changes:
    def __init__(self, game, backup):
        self.game, self.backup = Path(game), Path(backup)
        self.records = []
        self.backup.mkdir(parents=True, exist_ok=False)
        self.persist('in_progress')
    def persist(self, state):
        (self.backup/'manifest.json').write_text(json.dumps({'state':state,'game':str(self.game),'files':self.records},ensure_ascii=False,indent=2),encoding='utf-8')
    def write(self, relative, data):
        path = safe_target(self.game, relative)
        if path.is_file() and path.read_bytes() == data: return
        ensure(not path.exists() or path.is_file(), 'Вместо файла существует папка: ' + str(path))
        relative = path.relative_to(self.game).as_posix()
        existed = path.exists()
        if existed:
            backup_file = safe_target(self.backup/'files', relative)
            backup_file.parent.mkdir(parents=True, exist_ok=True)
            backup_file.write_bytes(path.read_bytes())
        record = {'path':relative, 'existed':existed, 'installed_sha256':digest(data)}
        self.records.append(record)
        self.persist('in_progress')
        path.parent.mkdir(parents=True, exist_ok=True)
        # Replace only after a complete write; interrupted writes keep the old file.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.fishing-install-', delete=False) as output:
                temporary = Path(output.name)
                output.write(data)
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists(): temporary.unlink()
    def rollback(self):
        for record in reversed(self.records):
            path = safe_target(self.game, record['path'])
            if path.exists() and digest(path.read_bytes()) != record['installed_sha256']:
                # A failed write may leave the original file unchanged.
                original = self.backup/'files'/record['path']
                if record['existed'] and original.exists() and path.read_bytes() == original.read_bytes(): continue
                record['rollback_skipped_changed_file'] = True
                continue
            if record['existed']:
                path.write_bytes((self.backup/'files'/record['path']).read_bytes())
            elif path.exists(): path.unlink()
        self.persist('rolled_back')

def setup_python(bot_dir):
    python = bot_dir/'.venv'/'Scripts'/'python.exe'
    if not python.is_file():
        subprocess.run([sys.executable, '-m', 'venv', str(bot_dir/'.venv')],check=True)
    subprocess.run([str(python), '-c', 'import sys,struct; assert sys.version_info[:2]==(3,11) and struct.calcsize("P")==8'],check=True)
    subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(PACKAGE/'requirements.txt')],check=True)
    subprocess.run([str(python), '-m', 'pip', 'check'],check=True)
    subprocess.run([str(python), '-c', 'import numpy,keyboard,soundcard,cffi'],check=True)

def install(game, lure_minutes, *, prepare_python=setup_python, get_superwow=fetch_superwow, get_superapi=fetch_superapi, make_shortcut=None, check_running=running_game, get_version=game_version):
    game = Path(game).expanduser().resolve(strict=True)
    ensure((game/'WoW.exe').is_file() and (game/'Data').is_dir(), 'Нужна папка с WoW.exe и Data.')
    ensure(x86_pe((game/'WoW.exe').read_bytes()), 'Ожидался 32-разрядный WoW.exe клиента Vanilla.')
    version = get_version(game/'WoW.exe')
    ensure(version == (1,12,1,5875), 'Нужен WoW 1.12.1 build 5875; найден ' + '.'.join(map(str,version)))
    ensure(lure_minutes in (5,10), 'Длительность наживки должна быть 5 или 10 минут.')
    ensure(not check_running(), 'Закройте WoW перед установкой и запустите установщик повторно. Игра не закрывалась.')
    verify_package()
    payload = read_payload()
    payload.update(get_superapi())
    bot = safe_target(game, 'FishingBot')
    ensure(bot.resolve() != PACKAGE, 'Запустите обновление из отдельно распакованного комплекта, не из установленной FishingBot.')
    marker = bot/'installation.json'
    if bot.exists():
        ensure(marker.is_file() and json.loads(marker.read_text(encoding='utf-8')).get('owner') == OWNER, 'Папка FishingBot уже существует и не принадлежит этому установщику. Выберите другой клиент или переименуйте эту папку самостоятельно.')
    plan = loader_plan(game)
    hook_path = safe_target(game, plan['hook'])
    launcher_path = safe_target(game, plan['launcher'])
    binaries = {}
    if not hook_path.is_file() or not launcher_path.is_file(): binaries = get_superwow()
    for path in (hook_path, launcher_path):
        if path.is_file(): ensure(x86_pe(path.read_bytes()), 'Неожиданный формат файла: ' + str(path))
    for name in BOT_FILES: ensure((PACKAGE/name).is_file(), 'Отсутствует ' + name)
    # Validate every destination before making changes, including reparse points.
    paths = ['FishingBot/'+n for n in BOT_FILES] + [n for n in payload if n.startswith(PAYLOAD_PREFIXES)]
    paths += ['Interface/AddOns/FishingBotSetup/FishingBotSetup.lua','Interface/AddOns/FishingBotSetup/FishingBotSetup.toc', 'FishingBot/start.cmd','FishingBot/QUICKSTART.txt','FishingBot/lure-settings.json','FishingBot/installation.json', 'Start Fishing Bot.lnk', 'Start WoW for Fishing.lnk', plan['hook'], plan['launcher']]
    if plan['dlls'] is not None: paths.append('dlls.txt')
    paths += ['FishingBot/'+n for n in payload if n.startswith('macros/') or n in ('SOURCES.md','INSTALL.md')]
    for name in paths: safe_target(game, name)
    bot.mkdir(exist_ok=True)
    # Ownership is recorded before venv preparation so an interrupted install can be retried.
    if not marker.exists(): marker.write_text(json.dumps({'owner':OWNER,'state':'preparing'}),encoding='utf-8')
    print('Подготавливаю окружение Python и зависимости...', flush=True)
    prepare_python(bot)
    ensure(not check_running(), 'WoW запущен во время подготовки. Закройте его и повторите установку.')
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    changes = Changes(game, safe_target(game, 'FishingBot/backups/'+stamp))
    if make_shortcut is None:
        spec = importlib.util.spec_from_file_location('fishing_shortcuts', PACKAGE/'installer'/'create-shortcuts.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        make_shortcut = module.shortcut
    try:
        for name in BOT_FILES: changes.write('FishingBot/'+name, (PACKAGE/name).read_bytes())
        for name, data in payload.items():
            changes.write(name if name.startswith(PAYLOAD_PREFIXES) else 'FishingBot/'+name, data)
        for name in ('FishingBotSetup.lua','FishingBotSetup.toc'):
            changes.write('Interface/AddOns/FishingBotSetup/'+name, (PACKAGE/'installer'/name).read_bytes())
        if not hook_path.is_file(): changes.write(plan['hook'], binaries['SuperWoWhook.dll'])
        if not launcher_path.is_file(): changes.write(plan['launcher'], binaries['SuperWoWlauncher.exe'])
        if plan['dlls'] is not None: changes.write('dlls.txt', plan['dlls'])
        cmd = '@echo off\r\nchcp 65001 >nul\r\ncd /d "%~dp0"\r\n".venv\\Scripts\\python.exe" cursor_test.py --lure-interval '+str(lure_minutes*60)+' %*\r\npause\r\n'
        changes.write('FishingBot/start.cmd', cmd.encode('ascii'))
        changes.write('FishingBot/lure-settings.json', json.dumps({'minutes':lure_minutes,'seconds':lure_minutes*60},indent=2).encode('utf-8'))
        changes.write('FishingBot/QUICKSTART.txt', quickstart(lure_minutes, plan['launcher']).encode('utf-8-sig'))
        with tempfile.TemporaryDirectory(prefix='FishingBot-links-') as temp:
            for name, target, cwd, arguments in [
                ('Start Fishing Bot.lnk', os.environ.get('COMSPEC',r'C:\Windows\System32\cmd.exe'), bot, '/d /c start.cmd'),
                ('Start WoW for Fishing.lnk', launcher_path, game, '')]:
                link = Path(temp)/name
                make_shortcut(link, target, cwd, arguments, name[:-4])
                ensure(link.is_file(), 'Не удалось создать ярлык.')
                changes.write(name, link.read_bytes())
        report = {'owner':OWNER,'state':'complete','installed_at':datetime.now().isoformat(),'game':str(game),'client_version':list(version),'launcher':plan['launcher'],'lure_minutes':lure_minutes,'backup':str(changes.backup),'superwow':'downloaded_2.2' if binaries else 'existing_not_version_verified'}
        changes.write('FishingBot/installation.json', json.dumps(report,ensure_ascii=False,indent=2).encode('utf-8'))
        changes.persist('complete')
    except Exception:
        changes.rollback()
        raise
    print('\nУстановка завершена.\n'+quickstart(lure_minutes, plan['launcher']), flush=True)
    print('Резервные копии:', changes.backup, flush=True)
    return report

def quickstart(minutes, launcher):
    return f'''1. Запустите игру через Start WoW for Fishing.lnk ({launcher}).
2. На выборе персонажа включите SuperAPI, SimpleAutoDelete и FishingBotSetup.
3. Войдите в игру и выполните /fishsetup: создадутся макросы и включится автолут.
4. Положите Fishing на кнопку 1, макрос FB-Lure на 2, FB-Boxes на 3. Проверьте кнопки вручную.
5. Проверьте звук клёва: три тона. Автолут должен забирать добычу без Shift.
6. Если автолут работает наоборот: /fishsetup autoloot off. Проверьте ещё раз.
7. Удочка надета, наживка Aquadynamic Fish Attractor в сумках. Интервал: {minutes} мин.
8. Запустите Start Fishing Bot.lnk, введите 2 минуты, переключитесь в игру и оставьте курсор над пустой водой.
F8/Esc — остановка без закрытия игры. По окончании таймера бот закрывает WoW.
Очистка сумок первоначально выключена. Настройка: /sad off, /sad add <ссылка>, /sad list, /sad test, /sad on.
Игра и бот установщиком не запускались. Игровые действия и реальные устройства нужно проверить вручную.
'''

def main():
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    parser = argparse.ArgumentParser(description='Установка Fishing Bot в клиент Vanilla WoW.')
    parser.add_argument('--game')
    parser.add_argument('--lure-minutes',type=int,choices=(5,10))
    parser.add_argument('--check-package',action='store_true',help='Проверить комплект, не изменяя клиент и не скачивая DLL')
    args = parser.parse_args()
    if args.check_package:
        print('Контрольные суммы:',verify_package()); print('Файлы архива:',len(read_payload())); return
    ensure(os.name=='nt' and sys.version_info[:2]==(3,11) and struct.calcsize('P')==8, 'Требуется Windows и Python 3.11 x64. Используйте Install.cmd.')
    game = args.game or input('Путь к папке игры (где WoW.exe): ').strip().strip('"')
    ensure(bool(game), 'Путь не введён.')
    minutes = args.lure_minutes
    if minutes is None:
        while True:
            answer = input('Сколько минут действует наживка на вашей удочке? [5/10, Enter = 5]: ').strip() or '5'
            if answer in ('5','10'): minutes=int(answer); break
            print('Введите 5 или 10.')
    install(game, minutes)

if __name__=='__main__':
    try: main()
    except (KeyboardInterrupt, EOFError): print('\nУстановка отменена.'); sys.exit(1)
    except Exception as exc:
        print('\nОШИБКА:',str(exc)); print('Установка не завершена. Исправьте причину и повторите.'); sys.exit(1)
