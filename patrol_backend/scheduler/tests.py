from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from .models import LocationSite, SiteCamera
from .serializers import SiteCameraBulkSerializer, SiteCameraWriteSerializer


def _camera(**overrides):
    data = {"name": "Gate", "rtsp_url": "rtsp://host/stream"}
    data.update(overrides)
    return data


class SiteCameraTypeSerializerTestCase(SimpleTestCase):
    def _valid(self, **overrides):
        serializer = SiteCameraWriteSerializer(data=_camera(**overrides))
        self.assertTrue(serializer.is_valid(), serializer.errors)
        return serializer.validated_data

    def _errors(self, **overrides):
        serializer = SiteCameraWriteSerializer(data=_camera(**overrides))
        self.assertFalse(serializer.is_valid())
        return serializer.errors

    def test_defaults_to_vehicle_without_features(self):
        data = self._valid()
        self.assertEqual(data["camera_type"], "vehicle")
        self.assertEqual(data["features"], [])

    def test_face_with_face_attendance(self):
        data = self._valid(camera_type="face", features=["face_attendance", " face_attendance "])
        self.assertEqual(data["camera_type"], "face")
        self.assertEqual(data["features"], ["face_attendance"])

    def test_face_needs_a_feature(self):
        errors = self._errors(camera_type="face", features=[])
        self.assertIn("features", errors)

    def test_vehicle_rejects_face_feature(self):
        errors = self._errors(camera_type="vehicle", features=["face_attendance"])
        self.assertIn("features", errors)

    def test_id_card_extract_not_available_yet(self):
        errors = self._errors(camera_type="face", features=["face_attendance", "id_card_extract"])
        self.assertIn("coming soon", str(errors["features"]))

    def test_unknown_type_and_feature_rejected(self):
        self.assertIn("camera_type", self._errors(camera_type="thermal"))
        self.assertIn("features", self._errors(camera_type="face", features=["ppe"]))

    def test_bulk_payload_from_old_client_still_valid(self):
        serializer = SiteCameraBulkSerializer(
            data={"cameras": [_camera(direction="in", gate_mode="line_direction")]}
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        cam = serializer.validated_data["cameras"][0]
        self.assertEqual((cam["camera_type"], cam["features"]), ("vehicle", []))


class SiteCameraTypeRuntimeTestCase(SimpleTestCase):
    def test_live_payload_includes_type_and_features(self):
        from .views_cameras import _camera_live_payload

        cam = SiteCamera(
            site=LocationSite(name="HQ"),
            name="Door",
            rtsp_url="rtsp://host/stream",
            camera_type="face",
            features=["face_attendance"],
            stream_path="cam-door",
        )
        payload = _camera_live_payload(cam)
        self.assertEqual(payload["camera_type"], "face")
        self.assertEqual(payload["features"], ["face_attendance"])
        self.assertTrue(cam.has_feature("face_attendance"))
        self.assertFalse(cam.has_feature("id_card_extract"))

    def test_anpr_reader_loads_only_vehicle_cameras(self):
        from visitor.anpr import reader

        manager = MagicMock()
        manager.filter.return_value.select_related.return_value.order_by.return_value = []
        with patch.object(SiteCamera, "objects", manager), patch.object(
            reader, "_release_db_connection"
        ):
            self.assertEqual(reader.load_cameras(), [])
        manager.filter.assert_called_once_with(is_enabled=True, camera_type="vehicle")
