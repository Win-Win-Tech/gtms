from rest_framework import serializers
from .models import User
from scheduler.serializers import LocationSerializer as SchedulerLocationSerializer

class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    location = SchedulerLocationSerializer(read_only=True)  # use existing serializer
    locationId = serializers.UUIDField(write_only=True, required=False, allow_null=True)
    timezone = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    class Meta:
        model = User
        fields = ['id', 'email', 'password', 'role', 'is_active', 'created_on', 
                  'modified_on', 'name', 'phone_no', 'location', 'locationId', 'aadhar_no', 'timezone']

    def create(self, validated_data):
        location_id = validated_data.pop('locationId', None)
        password = validated_data.pop('password', None)
        timezone = validated_data.pop('timezone', None)
        
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
        return user

    def update(self, instance, validated_data):
        location_id = validated_data.pop('locationId', None)
        password = validated_data.pop('password', None)
        timezone = validated_data.pop('timezone', None)
        
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
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
        return instance


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
