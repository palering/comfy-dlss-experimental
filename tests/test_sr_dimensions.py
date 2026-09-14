from pathlib import Path
import unittest
import json
from comfy_dlss_experimental.sr_dimensions import dimensions, output_size
from scripts.generate_sr_limits import render, render_python


class SRDimensionTests(unittest.TestCase):
    def test_dynamic_multiplier_options_have_comfy_locale_keys(self):
        from comfy_dlss_experimental.sr_dimensions import SIZE_MODES
        root=Path(__file__).resolve().parents[1]
        for locale in ('en','zh','zh-TW'):
            values=json.loads((root/'locales'/locale/'nodeDefs.json').read_text(encoding="utf-8"))
            options=values['DLSSExperimentalStreamlineStage']['inputs']['output_size_mode']['options']
            self.assertEqual(set(options),{value.replace('.','_') for value in SIZE_MODES})
    def test_generated_native_limits_match(self):
        root=Path(__file__).resolve().parents[1]
        self.assertEqual((root/'sidecar/src/sr_limits.hpp').read_text(),render(root))
        self.assertEqual((root/'comfy_dlss_experimental/_sr_limits.py').read_text(),render_python(root))

    def test_orientation_and_pixel_budget(self):
        for w,h in ((736,1280),(1280,736),(1920,1080),(1080,1920)):
            dimensions(w,h)
        for w,h in ((1920,1920),(1921,64),(63,1280)):
            with self.assertRaises(ValueError):dimensions(w,h)

    def test_multipliers_source_changes_manual_and_dlaa(self):
        self.assertEqual(output_size(736,1280,'2x',0,0),(1472,2560))
        self.assertEqual(output_size(736,1280,'1.5x',0,0),(1104,1920))
        self.assertEqual(output_size(1280,720,'2x',0,0),(2560,1440))
        self.assertEqual(output_size(736,1280,'manual',1104,1920),(1104,1920))
        self.assertEqual(output_size(736,1280,'2x',0,0,dlaa=True),(736,1280))
        self.assertEqual(output_size(736,1280,'3x',0,0),(2208,3840))
        with self.assertRaises(ValueError):output_size(736,1280,'manual',1472,2500)
        w,h=output_size(66,98,'1.5x',0,0)
        self.assertEqual(w*98,h*66);self.assertEqual(w%2+h%2,0)

    def test_output_budget_is_bytes_not_a_4k_rectangle(self):
        from comfy_dlss_experimental.sr_dimensions import LIMITS, output_budget_report
        for shape in ((2208,3840),(3840,2208),(4096,2160),(2160,4096),(4094,4096)):
            dimensions(*shape,output=True)
        for shape in ((4096,4096),(16385,64)):
            with self.assertRaises(ValueError):dimensions(*shape,output=True)
        report=output_budget_report(2208,3840)
        self.assertEqual(report['result_bytes'],67829768)
        self.assertFalse(report['gpu_memory_estimate'])
        self.assertEqual(LIMITS['output_max_pixels']*8+8,LIMITS['cxr_max_payload'])
