from django.contrib import admin

from .models import (
    EmployeePayrollProfile,
    PayslipTemplate,
    PayslipFieldConfig,
    PayslipField,
    PayslipRecord,
)


admin.site.register(EmployeePayrollProfile)
admin.site.register(PayslipTemplate)
admin.site.register(PayslipFieldConfig)
admin.site.register(PayslipField)
admin.site.register(PayslipRecord)

