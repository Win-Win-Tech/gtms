import numpy as np
from django.test import SimpleTestCase

from patrol_backend.utils.face_liveness import (
    _crop_face_bgr,
    _parse_spoof_result,
    _softmax,
    _uniface_model_list,
    assert_live_image_bytes,
    liveness_enabled,
    liveness_required,
)


class MiniFASNetHelpersTests(SimpleTestCase):
    def test_softmax_sums_to_one(self):
        p = _softmax(np.array([1.0, 2.0, 3.0]))
        self.assertAlmostEqual(float(np.sum(p)), 1.0, places=6)
        self.assertEqual(int(np.argmax(p)), 2)

    def test_crop_returns_80x80(self):
        img = np.zeros((240, 320, 3), dtype=np.uint8)
        crop = _crop_face_bgr(img, [80, 60, 160, 180], scale=2.7, size=80)
        self.assertEqual(crop.shape, (80, 80, 3))

    def test_liveness_context_does_not_leak(self):
        self.assertFalse(liveness_required())
        with liveness_enabled(True):
            self.assertTrue(liveness_required())
        self.assertFalse(liveness_required())

    def test_parse_spoof_result_uses_is_real_flag(self):
        class _R:
            is_real = False
            confidence = 0.99

        is_real, score = _parse_spoof_result(_R())
        self.assertFalse(is_real)
        self.assertAlmostEqual(score, 0.99)

    def test_empty_bytes_fail_closed(self):
        ok, msg, score = assert_live_image_bytes(b"")
        self.assertFalse(ok)
        self.assertIn("image", msg.lower())
        self.assertEqual(score, 0.0)

    def test_punch_path_uses_minifasnet_v2_only(self):
        names = [str(getattr(m, "name", m)) for m in _uniface_model_list()]
        self.assertEqual(names, ["V2"])
