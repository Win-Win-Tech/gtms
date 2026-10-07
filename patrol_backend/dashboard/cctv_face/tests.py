from types import SimpleNamespace
from unittest import mock

import numpy as np
from django.test import SimpleTestCase, override_settings

from scheduler.models import SiteCamera

from . import reader
from .detector import FaceBox, FaceDetector, faces_from_yunet
from .tracker import FaceTracker


def _box(x1, y1, size=80, score=0.9):
    return FaceBox(x1=x1, y1=y1, x2=x1 + size, y2=y1 + size, score=score)


def _frame(h=720, w=1280):
    rng = np.random.default_rng(0)
    return rng.integers(0, 255, size=(h, w, 3), dtype=np.uint8)


def _tracker(min_hits=2, lost_sec=1.5, min_face_px=60):
    return FaceTracker(camera_key="cam", min_hits=min_hits, lost_sec=lost_sec, min_face_px=min_face_px)


class FacesFromYunetTests(SimpleTestCase):
    def test_none_gives_empty(self):
        self.assertEqual(faces_from_yunet(None, 1.0, 100, 100), [])

    def test_scales_back_to_full_frame(self):
        row = [10, 20, 30, 40] + [1.0] * 10 + [0.95]
        faces = faces_from_yunet(np.array([row], dtype=np.float32), 0.5, 1280, 720)
        self.assertEqual(len(faces), 1)
        f = faces[0]
        self.assertEqual((f.x1, f.y1, f.x2, f.y2), (20, 40, 80, 120))
        self.assertAlmostEqual(f.score, 0.95, places=5)
        self.assertEqual(len(f.landmarks), 10)
        self.assertAlmostEqual(f.landmarks[0], 2.0)

    def test_clips_to_frame_and_drops_tiny(self):
        rows = np.array(
            [
                [-5, -5, 50, 50] + [0.0] * 10 + [0.9],
                [99, 99, 0.5, 0.5] + [0.0] * 10 + [0.9],
            ],
            dtype=np.float32,
        )
        faces = faces_from_yunet(rows, 1.0, 100, 100)
        self.assertEqual(len(faces), 1)
        self.assertEqual((faces[0].x1, faces[0].y1), (0, 0))


class FaceDetectorModelTests(SimpleTestCase):
    def test_model_loads_and_blank_frame_has_no_faces(self):
        det = FaceDetector(score_threshold=0.8, max_side=640)
        self.assertTrue(det.available)
        self.assertEqual(det.detect(np.zeros((720, 1280, 3), dtype=np.uint8)), [])

    def test_missing_model_is_not_available(self):
        det = FaceDetector("/nonexistent/model.onnx")
        self.assertFalse(det.available)
        self.assertEqual(det.detect(np.zeros((10, 10, 3), dtype=np.uint8)), [])


class FaceTrackerTests(SimpleTestCase):
    def test_two_faces_make_two_tracks(self):
        t = _tracker()
        t.update([_box(100, 100), _box(600, 100)], _frame(), now=0.0)
        self.assertEqual(len(t.tracks), 2)
        self.assertEqual(len({tr.track_id for tr in t.tracks.values()}), 2)

    def test_moving_face_stays_one_track(self):
        t = _tracker()
        frame = _frame()
        for i in range(5):
            t.update([_box(100 + i * 30, 100)], frame, now=i * 0.5)
        self.assertEqual(len(t.tracks), 1)
        self.assertEqual(next(iter(t.tracks.values())).hits, 5)

    def test_fast_walker_matched_by_distance(self):
        t = _tracker()
        frame = _frame()
        t.update([_box(100, 100)], frame, now=0.0)
        t.update([_box(160, 100)], frame, now=0.5)
        self.assertEqual(len(t.tracks), 1)

    def test_lost_face_finishes_after_lost_sec(self):
        t = _tracker(lost_sec=1.5)
        frame = _frame()
        t.update([_box(100, 100)], frame, now=0.0)
        t.update([_box(100, 100)], frame, now=0.5)
        self.assertEqual(t.update([], frame, now=1.5), [])
        done = t.update([], frame, now=2.1)
        self.assertEqual(len(done), 1)
        self.assertTrue(t.is_confirmed(done[0]))
        self.assertEqual(t.tracks, {})

    def test_single_glimpse_is_not_confirmed(self):
        t = _tracker(min_hits=2)
        t.update([_box(100, 100)], _frame(), now=0.0)
        done = t.flush()
        self.assertEqual(len(done), 1)
        self.assertFalse(t.is_confirmed(done[0]))

    def test_small_face_has_no_crop(self):
        t = _tracker(min_face_px=60)
        t.update([_box(100, 100, size=40)], _frame(), now=0.0)
        tr = next(iter(t.tracks.values()))
        self.assertIsNone(tr.best_crop)

    def test_big_face_keeps_padded_crop(self):
        t = _tracker(min_face_px=60)
        t.update([_box(100, 100, size=80)], _frame(), now=0.0)
        tr = next(iter(t.tracks.values()))
        self.assertIsNotNone(tr.best_crop)
        self.assertEqual(tr.best_crop.shape[:2], (120, 120))
        self.assertEqual(tr.best_box_in_crop, (20, 20, 100, 100))
        self.assertEqual(tr.best_face_px, 80)


def _cam(cam_id, features, name="Cam"):
    cam = SimpleNamespace(
        id=cam_id,
        name=name,
        rtsp_url="rtsp://example/stream",
        direction="toggle",
        site_id=1,
        site=SimpleNamespace(location_id=1),
        features=features,
    )
    cam.has_feature = lambda f, _features=features: str(getattr(f, "value", f)) in _features
    return cam


class LoadFaceCamerasTests(SimpleTestCase):
    def _run(self, cams):
        qs = mock.MagicMock()
        qs.select_related.return_value.order_by.return_value = cams
        with mock.patch.object(SiteCamera.objects, "filter", return_value=qs) as flt, mock.patch.object(
            reader, "_release_db_connection"
        ) as release:
            out = reader.load_face_cameras()
        return out, flt, release

    @override_settings(FACE_CCTV_MAX_CAMERAS=5)
    def test_keeps_only_face_attendance_cameras(self):
        cams = [_cam("a", ["face_attendance"]), _cam("b", []), _cam("c", ["face_attendance"])]
        out, flt, release = self._run(cams)
        self.assertEqual([c.id for c in out], ["a", "c"])
        flt.assert_called_once_with(is_enabled=True, camera_type=SiteCamera.CameraType.FACE)
        release.assert_called_once()

    @override_settings(FACE_CCTV_MAX_CAMERAS=1)
    def test_respects_max_cameras(self):
        cams = [_cam("a", ["face_attendance"]), _cam("b", ["face_attendance"])]
        out, _, _ = self._run(cams)
        self.assertEqual([c.id for c in out], ["a"])


class FaceCameraWorkerTests(SimpleTestCase):
    def _worker(self, faces_per_tick):
        det = mock.MagicMock()
        det.detect.side_effect = faces_per_tick
        return reader.FaceCameraWorker(_cam("cam-1", ["face_attendance"]), det)

    @override_settings(FACE_CCTV_MOTION_GATE=False, FACE_CCTV_MIN_TRACK_HITS=2, FACE_CCTV_TRACK_LOST_SEC=1.0)
    def test_person_counted_once_after_leaving(self):
        w = self._worker([[_box(100, 100)], [_box(110, 100)], [], []])
        frame = _frame()
        for i, t in enumerate([0.0, 0.5, 1.0, 2.0]):
            w.process_tick(frame, now_m=t)
        self.assertEqual(w.stats["people"], 1)
        self.assertEqual(w.stats["good_faces"], 1)
        self.assertEqual(w.stats["max_faces"], 1)

    @override_settings(FACE_CCTV_MOTION_GATE=True, FACE_CCTV_IDLE_DETECT_SEC=2, FACE_CCTV_MOTION_HOLD_SEC=0)
    def test_still_picture_skips_detector(self):
        w = self._worker([[]] * 10)
        frame = _frame()
        w.process_tick(frame, now_m=0.0)
        w.process_tick(frame, now_m=0.5)
        w.process_tick(frame, now_m=1.0)
        self.assertEqual(w.detector.detect.call_count, 1)
        self.assertEqual(w.stats["idle_skips"], 2)
        w.process_tick(frame, now_m=2.5)
        self.assertEqual(w.detector.detect.call_count, 2)
