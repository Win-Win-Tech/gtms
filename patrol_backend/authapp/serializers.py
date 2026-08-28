import logging

from rest_framework import serializers
from .models import User, Role
from scheduler.serializers import LocationSerializer as SchedulerLocationSerializer

logger = logging.getLogger(__name__)

class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = [
            'id', 'name', 'location', 'is_default', 'is_allow_webapp',
            'is_allow_edit', 'is_allow_create', 'pages',
        ]
        read_only_fields = ['id', 'location', 'is_default']

class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    location = SchedulerLocationSerializer(read_only=True)  # use existing serializer
    locationId = serializers.UUIDField(write_only=True, required=False, allow_null=True)
    timezone = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    face_photo = serializers.ImageField(required=False, allow_null=True)
    remove_face_photo = serializers.BooleanField(write_only=True, required=False, default=False)

    class Meta:
        model = User
        fields = ['id', 'email', 'password', 'role', 'is_active', 'created_on',
                  'modified_on', 'name', 'phone_no', 'location', 'locationId', 'aadhar_no', 'timezone', 'employee_code',
                  'face_photo', 'remove_face_photo']
        read_only_fields = ['id', 'is_active', 'created_on', 'modified_on']

    def create(self, validated_data):
        location_id = validated_data.pop('locationId', None)
        password = validated_data.pop('password', None)
        timezone = validated_data.pop('timezone', None)
        validated_data.pop('remove_face_photo', None)
        
        user = User(**validated_data)
        if password:
            user.set_password(password)
        if location_id:
            from scheduler.models import Location
            user.location = Location.objects.get(id=location_id)
        
        # Timezone logic: If admin sends timezone, use it; otherwise, get from location's admin
        if timezone:
            # Admin explicitly set timezone
            user.timezone = timezone
        elif user.location:
            # Find admin for this location and use their timezone
            location_admin = User.objects.filter(
                location=user.location,
                role='admin',
                is_deleted=False,
                is_active=True
            ).first()
            if location_admin and location_admin.timezone:
                user.timezone = location_admin.timezone
        
        user.save()
        _try_refresh_face_encoding(user)
        return user

    def update(self, instance, validated_data):
        old_location_id = (
            str(instance.location_id) if getattr(instance, "location_id", None) else None
        )
        location_id = validated_data.pop('locationId', None)
        password = validated_data.pop('password', None)
        timezone = validated_data.pop('timezone', None)
        remove_face_photo = validated_data.pop('remove_face_photo', False)
        
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if remove_face_photo:
            if instance.face_photo:
                instance.face_photo.delete(save=False)
            instance.face_photo = None
            instance.face_encoding = None
        if password:
            instance.set_password(password)
        if location_id:
            from scheduler.models import Location
            instance.location = Location.objects.get(id=location_id)
        
        # Timezone logic: If admin sends timezone, use it; otherwise, get from location's admin
        if timezone:
            # Admin explicitly set timezone
            instance.timezone = timezone
        elif instance.location:
            # Find admin for this location and use their timezone
            location_admin = User.objects.filter(
                location=instance.location,
                role='admin',
                is_deleted=False,
                is_active=True
            ).first()
            if location_admin and location_admin.timezone:
                instance.timezone = location_admin.timezone
        
        instance.save()
        new_location_id = (
            str(instance.location_id) if getattr(instance, "location_id", None) else None
        )
        sync_user_face_and_index(
            instance,
            old_location_id=old_location_id,
            new_location_id=new_location_id,
        )
        return instance


class UserListSerializer(serializers.ModelSerializer):
    location = SchedulerLocationSerializer(read_only=True)

    class Meta:
        model = User
        # Intentionally exclude face_photo/face_encoding for list performance
        fields = [
            'id', 'email', 'role', 'is_active', 'created_on', 'modified_on',
            'name', 'phone_no', 'location', 'aadhar_no', 'timezone', 'employee_code'
        ]


def sync_user_face_and_index(user, old_location_id=None, new_location_id=None):
    """
    Recompute face_encoding from face_photo and rebuild FAISS indexes for affected locations.
    Clears encoding when photo is missing or no face is detected in the photo.
    """
    try:
        from patrol_backend.utils.face_utils import refresh_user_face_encoding_from_photo

        if user.face_photo:
            if not refresh_user_face_encoding_from_photo(user):
                User.objects.filter(pk=user.pk).update(face_encoding=None)
                user.face_encoding = None
        elif not user.face_encoding:
            pass
        else:
            User.objects.filter(pk=user.pk).update(face_encoding=None)
            user.face_encoding = None
    except Exception as exc:
        logger.warning("refresh_user_face_encoding failed user=%s: %s", user.pk, exc)

    try:
        from patrol_backend.utils.face_index import rebuild_location_index

        for loc_id in {lid for lid in (old_location_id, new_location_id) if lid}:
            rebuild_location_index(str(loc_id))
    except Exception as exc:
        logger.warning("rebuild_location_index failed user=%s: %s", user.pk, exc)


def _try_refresh_face_encoding(user):
    """Backward-compatible alias used by mobile profile view."""
    loc = str(user.location_id) if getattr(user, "location_id", None) else None
    sync_user_face_and_index(user, old_location_id=loc, new_location_id=loc)


def _register_heif_opener_if_available():
    """iPhone often uses HEIC/HEIF; Pillow needs pillow-heif to decode."""
    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
    except ImportError:
        pass


_register_heif_opener_if_available()


class MobileSelfProfileSerializer(serializers.Serializer):
    """
    Mobile app self-service profile updates (authenticated user only).
    Currently: face photo upload only (replaces existing photo if any).
    Add fields here later (e.g. phone_no).
    """

    face_photo = serializers.ImageField(required=True, allow_null=False)

    # Camera-friendly raster formats (no GIF — not used for real camera output).
    # HEIF = what Pillow reports for iPhone .heic after pillow-heif is registered.
    _ALLOWED_PIL_FORMATS = frozenset({"JPEG", "PNG", "WEBP", "HEIF"})

    def validate_face_photo(self, value):
        if not value:
            raise serializers.ValidationError("Image file is required.")

        ct = (getattr(value, "content_type", None) or "").lower().split(";")[0].strip()
        if ct and not ct.startswith("image/") and ct != "application/octet-stream":
            raise serializers.ValidationError(
                "Only image uploads are allowed (got non-image content type)."
            )

        try:
            from PIL import Image

            value.seek(0)
            with Image.open(value) as img:
                img.load()
                fmt = (img.format or "").upper()
                if fmt not in self._ALLOWED_PIL_FORMATS:
                    raise serializers.ValidationError(
                        "Unsupported image type. Use JPEG, PNG, WebP, or HEIC/HEIF."
                    )
        except serializers.ValidationError:
            raise
        except Exception:
            raise serializers.ValidationError(
                "File is not a valid image or is corrupted."
            )
        finally:
            try:
                value.seek(0)
            except Exception:
                pass

        return value


# from rest_framework import serializers
# from .models import User


# class UserSerializer(serializers.ModelSerializer):
#     password = serializers.CharField(write_only=True)  # Accept 'password' for input but never return

#     class Meta:
#         model = User
#         fields = '__all__'  # ✅ include every field from User model
#         extra_kwargs = {
#             'password': {'write_only': True}
#         }

#     def create(self, validated_data):
#         password = validated_data.pop('password', None)
#         user = User(**validated_data)
#         if password:
#             user.set_password(password)
#         user.save()
#         return user
