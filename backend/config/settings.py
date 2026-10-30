import os
if os.environ.get('DJANGO_SETTINGS_MODULE', '').endswith('settings_test'):
    from enrollment_service.settings_test import *
else:
    from enrollment_service.settings import *
