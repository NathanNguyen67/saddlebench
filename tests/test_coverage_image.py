"""Browser-free image renderer and X11 opener regression tests."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from saddlebench import coverage_image as ri
from saddlebench import coverage_image_open as oi
from test_coverage_leaderboard import report, case

class ImageOutputTests(unittest.TestCase):
    def test_svg_is_per_dataset_and_no_scoring_change(self):
        rows=[case('c1','oc20:01','a',[.02,.04],k=3),
              case('c2','lemat:01','b',[.01],k=2)]
        with tempfile.TemporaryDirectory() as td:
            src=Path(td)/'a.json'
            original=json.dumps(report(rows))
            src.write_text(original)
            with patch.object(ri, '_png_from_svg', return_value=(False,'NO_SVG_RASTERIZER')):
                with patch.object(ri, '_png_using_pillow', return_value=False):
                    result=ri.render_images(src)
            self.assertEqual([x['dataset'] for x in result],['LEMAT','OC20'])
            self.assertTrue(all(Path(x['svg']).exists() for x in result))
            self.assertEqual([x['png'] for x in result],[None,None])
            self.assertEqual(src.read_text(),original)
            xml=ET.parse(result[0]['svg'])
            self.assertEqual(xml.getroot().tag,'{http://www.w3.org/2000/svg}svg')
            self.assertIn('LEMAT',Path(result[0]['svg']).read_text())
            self.assertNotIn('OC20',Path(result[0]['svg']).read_text())

    def test_rasterizes_png_with_available_dependency_and_reuses_existing(self):
        with tempfile.TemporaryDirectory() as td:
            src=Path(td)/'b.json'
            src.write_text(json.dumps(report([case('c1','lemat:01','sella',[.01],k=4)])))
            result=ri.render_images(src)
            self.assertEqual(len(result),1)
            self.assertIsNotNone(result[0]['png'])
            path=Path(result[0]['png'])
            self.assertTrue(path.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'))
            old=path.stat().st_mtime_ns
            same=ri.render_images(src)
            self.assertEqual(old,path.stat().st_mtime_ns)
            self.assertEqual(same[0]['conversion'],'EXISTING')

    def test_pillow_fallback_when_svg_conversion_unavailable(self):
        try: import PIL
        except ImportError: self.skipTest('Pillow not installed in test runtime')
        with tempfile.TemporaryDirectory() as td:
            src=Path(td)/'b.json'
            src.write_text(json.dumps(report([case('c1','lemat:01','sella',[.01])])))
            with patch.object(ri,'_png_from_svg',return_value=(False,'NO_SVG_RASTERIZER')):
                result=ri.render_images(src)
            self.assertEqual(result[0]['conversion'],'PILLOW')
            self.assertTrue(Path(result[0]['png']).read_bytes().startswith(b'\x89PNG'))

    def test_escaped_model_text_is_safe_svg(self):
        with tempfile.TemporaryDirectory() as td:
            src=Path(td)/'c.json'
            src.write_text(json.dumps(report([case('c1','lemat:01','X<&>',[.02])])))
            text=ri.svg_board(ri.build_boards(json.loads(src.read_text()))[0])
            self.assertIn('X&lt;&amp;&gt;',text)
            self.assertNotIn('<script',text)
            ET.fromstring(text)

    def test_open_image_skip_when_not_forwarded(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'x.png';p.write_bytes(b'abc')
            with patch.object(oi.subprocess,'Popen') as popen:
                self.assertEqual(oi.open_image(p,env={'PATH':'/usr/bin'}),'SKIPPED_NO_GRAPHICAL_DISPLAY')
                popen.assert_not_called()

    def test_open_image_prioritizes_image_app_and_tk_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'x.png';p.write_bytes(b'abc')
            with patch.object(oi.shutil,'which',side_effect=lambda cmd: '/usr/bin/eog' if cmd=='eog' else None):
                with patch.object(oi.subprocess,'Popen') as popen:
                    popen.return_value.wait.side_effect=subprocess.TimeoutExpired('eog',1.5)
                    self.assertEqual(oi.open_image(p,env={'DISPLAY':'localhost:11'}),'REQUESTED:eog')
            with patch.object(oi.shutil,'which',return_value=None):
                with patch.object(oi.subprocess,'Popen') as popen:
                    popen.return_value.wait.side_effect=subprocess.TimeoutExpired('tk',2)
                    self.assertEqual(oi.open_image(p,env={'DISPLAY':'localhost:11'}),'REQUESTED:tkinter')


if __name__=='__main__': unittest.main()
