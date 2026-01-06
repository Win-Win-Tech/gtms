import uuid
import pytz
from django.db import models
from django.utils.timezone import now
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.conf import settings
from scheduler.models import Location  


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
       
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('role', 'admin')
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('guard', 'Guard'),
        ('so', 'So'),
        ('fo', 'Fo'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    aadhar_no = models.CharField(max_length=20, blank=True, null=True, unique=True)
    email = models.EmailField(max_length=255, unique=True, null=True)
    name = models.CharField(max_length=255, blank=True)
    phone_no = models.CharField(max_length=20, blank=True, null=True, unique=True)
    role = models.CharField(max_length=32, choices=ROLE_CHOICES, default='guard')
    location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users"
    )
    timezone = models.CharField(
        max_length=50,
        default='Asia/Kolkata',
        help_text="User's timezone (e.g., 'Asia/Kolkata', 'America/New_York')"
    )
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    # Audit fields
    created_on = models.DateTimeField(auto_now_add=True)
    modified_on = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='user_created'
    )
    modified_by = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='user_modified'
    )

    # Soft delete fields
    is_deleted = models.BooleanField(default=False)
    deleted_on = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='user_deleted'
    )

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()

    @classmethod
    def get_by_roles(cls, roles):
        return cls.objects.filter(role__in=roles, is_deleted=False)

    def delete(self, user=None, using=None, keep_parents=False):
        """Override delete method for soft delete"""
        self.is_deleted = True
        self.deleted_on = now()
        if user:
            self.deleted_by = user
        self.save()

    def save(self, *args, **kwargs):
        """Override save to sync timezone for location users when admin timezone changes"""
        is_update = self.pk is not None
        old_timezone = None
        location_to_sync = None
        
        if is_update and self.role == 'admin':
            try:
                old_instance = User.objects.get(pk=self.pk)
                old_timezone = old_instance.timezone
                location_to_sync = old_instance.location
            except User.DoesNotExist:
                pass
        
        super().save(*args, **kwargs)
        
        # If admin's timezone changed, update all users in same location
        if is_update and self.role == 'admin' and location_to_sync:
            if old_timezone != self.timezone and self.timezone:
                # Update all non-admin users in this location
                User.objects.filter(
                    location=location_to_sync,
                    role__in=['guard', 'so', 'fo'],
                    is_deleted=False
                ).exclude(id=self.id).update(timezone=self.timezone)

    def __str__(self):
        return f"{self.email} ({self.role})"

    class Meta:
        indexes = [
            models.Index(fields=['is_deleted']),
        ]
