from django.contrib import admin

from .models import SemesterAccount, Instructor

admin.site.register(Instructor)
admin.site.register(SemesterAccount)
