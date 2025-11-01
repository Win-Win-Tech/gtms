# reports/models.py
from django.db import models

class DummyReport(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
