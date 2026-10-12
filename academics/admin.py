from django.contrib import admin

from .models import BandwidthPolicy, SchoolYear, Semester

admin.site.register(SchoolYear)
admin.site.register(Semester)
admin.site.register(BandwidthPolicy)
