import uuid
import pytz
from django.db import models
from django.utils.timezone import now
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver
from scheduler.models import Location  

class Role(models.Model):
    name = models.CharField(max_length=100)
    location = models.ForeignKey(Location, on_delete=models.CASCADE, null=True, blank=True, related_name='roles')
    is_default = models.BooleanField(default=False, help_text="True if this is a system default role (Admin, SO, FO, Guard)")
    is_allow_webapp = models.BooleanField(default=False, help_text="True if this role allows web panel access")
    pages = models.JSONField(default=list, blank=True, help_text="List of menu strings (e.g., ['Dashboard', 'Users'])")

    class Meta:
        unique_together = ('name', 'location')

    def __str__(self):
        return f"{self.name} - {self.location.name if self.location else 'Global'}"

    def save(self, *args, **kwargs):
        if self.name:
            self.name = self.name.lower().strip()
        super().save(*args, **kwargs)


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
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    aadhar_no = models.CharField(max_length=20, blank=True, null=True, unique=True)
    email = models.EmailField(max_length=255, unique=True, null=True)
    name = models.CharField(max_length=255, blank=True)
    phone_no = models.CharField(max_length=20, blank=True, null=True, unique=True)
    
    # Role is sent from the frontend, dynamically driven by the Role table
    role = models.CharField(max_length=32, default='guard')
    
    location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users"
    )
    employee_code = models.CharField(max_length=64, null=True, blank=True)
    face_photo = models.ImageField(
        upload_to="user_faces/",
        null=True,
        blank=True,
        help_text="Reference photo for face attendance (enrollment)",
    )
    face_encoding = models.BinaryField(
        null=True,
        blank=True,
        help_text="128-d face_recognition encoding (float64 bytes); computed from face_photo",
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
    def get_by_roles(cls, role_names):
        # role_names should be a list of lowercase strings
        lower_roles = [r.lower().strip() for r in role_names]
        return cls.objects.filter(role__in=lower_roles, is_deleted=False)

    def delete(self, user=None, using=None, keep_parents=False):
        """Override delete method for soft delete"""
        self.is_deleted = True
        self.deleted_on = now()
        if user:
            self.deleted_by = user
        self.save()

    def save(self, *args, **kwargs):
        """Override save to sync timezone for location users when admin timezone changes"""
        if self.role:
            self.role = self.role.lower().strip()
            
        is_update = self.pk is not None
        old_timezone = None
        location_to_sync = None
        
        is_admin = self.role and self.role.lower() == 'admin'
        
        if is_update and is_admin:
            try:
                old_instance = User.objects.get(pk=self.pk)
                old_timezone = old_instance.timezone
                location_to_sync = old_instance.location
            except User.DoesNotExist:
                pass
        
        super().save(*args, **kwargs)
        
        # If admin's timezone changed, update all users in same location
        if is_update and is_admin and location_to_sync:
            if old_timezone != self.timezone and self.timezone:
                # Update all non-admin users in this location
                User.objects.filter(
                    location=location_to_sync,
                    role__in=['guard', 'so', 'fo'],
                    is_deleted=False
                ).exclude(id=self.id).update(timezone=self.timezone)

    def __str__(self):
        role_name = self.role if self.role else "No Role"
        return f"{self.email} ({role_name})"

    class Meta:
        unique_together = ('location', 'employee_code')
        indexes = [
            models.Index(fields=['is_deleted']),
        ]




@receiver(post_save, sender=Role)
def propagate_new_global_role(sender, instance, created, **kwargs):
    """When a global role template is created, copy it to all existing locations."""
    if created and instance.location is None:
        for loc in Location.objects.filter(is_deleted=False):
            Role.objects.get_or_create(
                name=instance.name,
                location=loc,
                defaults={
                    'is_default': instance.is_default,
                    'is_allow_webapp': instance.is_allow_webapp,
                    'pages': instance.pages
                }
            )
