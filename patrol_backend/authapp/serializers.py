from rest_framework import serializers
from .models import User

class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)  # Accepts 'password' from input

    class Meta:
        model = User
        fields = ['id', 'email', 'password', 'role', 'is_active', 'created_on', 'modified_on']

    def create(self, validated_data):
        password = validated_data.pop('password')
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user
