from .base import *

DEBUG = False

ALLOWED_HOSTS = [
    config('AZURE_APP_URL'),   # your azure app url will go here
]

# Production Database — Azure PostgreSQL
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': config('DB_NAME'),
        'USER': config('DB_USER'),
        'PASSWORD': config('DB_PASSWORD'),
        'HOST': config('DB_HOST'),
        'PORT': '5432',
        'OPTIONS': {
            'sslmode': 'require',   # Azure requires SSL
        },
    }
}

CSRF_TRUSTED_ORIGINS = [
    'https://apocorp-backend-fudtbranbbh8c4e5.centralindia-01.azurewebsites.net',
    'https://apo-corp-frontend.vercel.app',
    'https://apocorptech.com',
    'https://www.apocorptech.com',
    'https://erp.apocorptech.com',
]

SECURE_SSL_REDIRECT = False  # Azure handles SSL, not Django
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')  # Trust Azure's SSL
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# CORS — only allow your Vercel frontend
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = [
    'https://erp.apocorptech.com',
    'https://apo-corp-frontend.vercel.app',
]

# Email Configuration for Production
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = config('EMAIL_HOST', default='smtp.gmail.com')
EMAIL_PORT = config('EMAIL_PORT', default=587, cast=int)
EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=True, cast=bool)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='security@apocorptech.com')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')  # Will be set in Azure env
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='security@apocorptech.com')

# Frontend URL for password reset (production)
FRONTEND_URL = config('FRONTEND_URL', default='https://erp.apocorptech.com')

# Azure Blob Storage
STORAGES = {
    "default": {
        "BACKEND": "storages.backends.azure_storage.AzureStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

AZURE_ACCOUNT_NAME = config('AZURE_STORAGE_ACCOUNT_NAME')
AZURE_ACCOUNT_KEY = config('AZURE_STORAGE_ACCOUNT_KEY')
AZURE_CONTAINER = 'media'
AZURE_CUSTOM_DOMAIN = f'{AZURE_ACCOUNT_NAME}.blob.core.windows.net'
MEDIA_URL = f'https://{AZURE_CUSTOM_DOMAIN}/media/'

AZURE_OVERWRITE_FILES = True