"""No game execution, networking or pip; tests use disposable fake clients."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('fishing_install', ROOT/'install.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)

def pe():
    data = bytearray(256); data[:2] = b'MZ'
    struct.pack_into('<I',data,60,128); data[128:132] = b'PE\0\0'
    struct.pack_into('<H',data,132,0x14c)
    return bytes(data)

def fake_shortcut(path, *args): Path(path).write_bytes(b'test-only-shortcut')

class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='FishingBot-test-')
        self.addCleanup(self.temp.cleanup)
        self.game = Path(self.temp.name)/'Клиент с пробелами & test'
        (self.game/'Data').mkdir(parents=True)
        (self.game/'WoW.exe').write_bytes(pe())
        (self.game/'WTF').mkdir()
        (self.game/'WTF'/'Config.wtf').write_bytes(b'PRIVATE-SETTINGS-DO-NOT-TOUCH')
        self.kw = dict(prepare_python=lambda p: None, get_superwow=lambda: {'SuperWoWhook.dll':pe(),'SuperWoWlauncher.exe':pe()},get_superapi=lambda: {'Interface/AddOns/SuperAPI/SuperAPI.toc':b'## Interface: 11200'},make_shortcut=fake_shortcut,check_running=lambda:False,get_version=lambda p:(1,12,1,5875))
    def run_install(self, minutes=5, **overrides):
        with contextlib.redirect_stdout(io.StringIO()):
            return module.install(self.game,minutes,**(self.kw|overrides))
    def test_package_hashes_and_payload(self):
        self.assertGreater(module.verify_package(),12)
        self.assertIn('Interface/AddOns/SimpleAutoDelete/SimpleAutoDelete.lua',module.read_payload())
    def test_fresh_install_and_paths(self):
        report=self.run_install()
        self.assertEqual(report['launcher'],'SuperWoWlauncher.exe')
        self.assertEqual((self.game/'WTF'/'Config.wtf').read_bytes(),b'PRIVATE-SETTINGS-DO-NOT-TOUCH')
        self.assertTrue((self.game/'Start Fishing Bot.lnk').is_file())
        self.assertIn('--lure-interval 300',(self.game/'FishingBot'/'start.cmd').read_text())
        self.assertEqual((self.game/'Sound'/'Spells'/'Tradeskills'/'FishBite.wav').read_bytes(),(ROOT/'FishBite-reference.wav').read_bytes())
    def test_existing_loader_and_hook_are_preserved(self):
        (self.game/'VanillaFixes.exe').write_bytes(pe())
        (self.game/'dlls.txt').write_bytes(b'nampower.dll\r\nmods/SuperWoWhook.dll\r\n')
        (self.game/'mods').mkdir(); (self.game/'mods'/'SuperWoWhook.dll').write_bytes(pe()+b'preserve-existing')
        self.run_install(get_superwow=lambda: self.fail('Existing binary must not be downloaded'))
        self.assertEqual((self.game/'dlls.txt').read_bytes(),b'nampower.dll\r\nmods/SuperWoWhook.dll\r\n')
        self.assertTrue((self.game/'mods'/'SuperWoWhook.dll').read_bytes().endswith(b'preserve-existing'))
    def test_loader_list_adds_once_and_handles_utf16(self):
        (self.game/'VanillaFixes.exe').write_bytes(pe())
        (self.game/'dlls.txt').write_bytes('nampower.dll\r\n'.encode('utf-16'))
        self.run_install(); self.run_install(10)
        text=(self.game/'dlls.txt').read_text(encoding='utf-8')
        self.assertEqual(text.count('SuperWoWhook.dll'),1)
        self.assertIn('nampower.dll',text)
        self.assertIn('--lure-interval 600',(self.game/'FishingBot'/'start.cmd').read_text())
    def test_existing_file_backup(self):
        sound=self.game/'Sound'/'Spells'/'Tradeskills'/'FishBite.wav'
        sound.parent.mkdir(parents=True); sound.write_bytes(b'original-sound')
        report=self.run_install()
        self.assertEqual((Path(report['backup'])/'files'/'Sound'/'Spells'/'Tradeskills'/'FishBite.wav').read_bytes(),b'original-sound')
    def test_failed_shortcut_rolls_back_game_files(self):
        (self.game/'VanillaFixes.exe').write_bytes(pe())
        (self.game/'dlls.txt').write_bytes(b'nampower.dll\r\n')
        sound=self.game/'Sound'/'Spells'/'Tradeskills'/'FishBite.wav'
        sound.parent.mkdir(parents=True); sound.write_bytes(b'original-sound')
        def failure(*args): raise OSError('simulated shortcut failure')
        with self.assertRaises(OSError): self.run_install(make_shortcut=failure)
        self.assertEqual(sound.read_bytes(),b'original-sound')
        self.assertEqual((self.game/'dlls.txt').read_bytes(),b'nampower.dll\r\n')
        self.assertFalse((self.game/'SuperWoWhook.dll').exists())
        self.assertFalse((self.game/'Interface'/'AddOns'/'FishingBotSetup'/'FishingBotSetup.lua').exists())
    def test_running_game_blocks_install(self):
        with self.assertRaises(RuntimeError): self.run_install(check_running=lambda:True)
        self.assertFalse((self.game/'FishingBot').exists())
    def test_wrong_client_blocks_install(self):
        with self.assertRaises(RuntimeError): self.run_install(get_version=lambda p:(3,3,5,12340))
        self.assertFalse((self.game/'FishingBot').exists())
    def test_unknown_folder_is_not_overwritten(self):
        folder=self.game/'FishingBot'; folder.mkdir(); (folder/'user.txt').write_text('keep')
        with self.assertRaises(RuntimeError): self.run_install()
        self.assertEqual((folder/'user.txt').read_text(),'keep')
    def test_unknown_loader_blocks_install(self):
        (self.game/'dlls.txt').write_text('OtherMod.dll\n')
        with self.assertRaises(RuntimeError): self.run_install()
        self.assertFalse((self.game/'FishingBot').exists())
    def test_duplicate_hook_blocks_install(self):
        (self.game/'VanillaFixes.exe').write_bytes(pe())
        (self.game/'dlls.txt').write_text('SuperWoWhook.dll\nmods/SuperWoWhook.dll\n')
        with self.assertRaises(RuntimeError): self.run_install()
    def test_traversal_rejected(self):
        for path in ('../outside','C:/outside','folder/../../outside'):
            with self.assertRaises(RuntimeError): module.safe_target(self.game,path)
    def test_archive_traversal_rejected(self):
        source=Path(self.temp.name)/'payload'; source.mkdir()
        (source/'FishBite-reference.wav').write_bytes((ROOT/'FishBite-reference.wav').read_bytes())
        with zipfile.ZipFile(source/'addons-and-macros.zip','w') as z: z.writestr('../outside.txt','bad')
        with self.assertRaises(RuntimeError): module.read_payload(source)
    def test_failed_download_leaves_client_untouched(self):
        def failure(): raise OSError('network unavailable')
        with self.assertRaises(OSError): self.run_install(get_superwow=failure)
        self.assertFalse((self.game/'FishingBot').exists())
    def test_failed_superapi_download_leaves_client_untouched(self):
        def failure(): raise OSError('SuperAPI unavailable')
        with self.assertRaises(OSError): self.run_install(get_superapi=failure)
        self.assertFalse((self.game/'FishingBot').exists())
        self.assertFalse((self.game/'Interface').exists())
    def test_superapi_bad_hash_rejected(self):
        with patch.object(module.urllib.request,'urlopen',return_value=io.BytesIO(b'incorrect archive')):
            with self.assertRaisesRegex(RuntimeError,'SuperAPI'): module.fetch_superapi()
    def test_failed_pip_does_not_install_game_payload(self):
        def failure(p): raise RuntimeError('pip failed')
        with self.assertRaises(RuntimeError): self.run_install(prepare_python=failure)
        self.assertFalse((self.game/'SuperWoWhook.dll').exists())
        self.assertFalse((self.game/'Interface').exists())
        self.run_install() # Ownership marker permits a retry.

if __name__=='__main__': unittest.main(verbosity=2)
