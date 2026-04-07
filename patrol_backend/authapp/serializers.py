from rest_framework import serializers
from .models import User, Role
from scheduler.serializers import LocationSerializer as SchedulerLocationSerializer

class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ['id', 'name', 'location', 'is_default', 'is_allow_webapp', 'pages']
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
        _try_refresh_face_encoding(instance)
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


def _try_refresh_face_encoding(user):
    try:
        from patrol_backend.utils.face_utils import refresh_user_face_encoding_from_photo
        refresh_user_face_encoding_from_photo(user)
    except Exception:
        pass


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
